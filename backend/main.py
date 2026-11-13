import asyncio
import io
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import ai, knowledge, news, publishing, render
from .core import BOOKS, DATA, ROOT, config, db, dumps, init_db, now, one, rows, submit, uid
from .schemas import Content


async def polling():
    last = time.monotonic()
    while True:
        await asyncio.sleep(15)
        try:
            interval = int(config()['RSS_INTERVAL_MINUTES'])
            if interval >= 15 and time.monotonic() - last >= interval * 60:
                submit('RSS 자동 수집', 'feeds', news.collect)
                last = time.monotonic()
        except (ValueError, OSError):
            pass


@asynccontextmanager
async def lifespan(app):
    init_db()
    knowledge.seed()
    task = asyncio.create_task(polling())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='Kostolany Studio', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin not in ('http://127.0.0.1:8765', 'http://localhost:8765', 'http://127.0.0.1:5173', 'http://localhost:5173'):
            return JSONResponse({'detail': '허용되지 않은 요청 출처입니다.'}, 403)
        if int(request.headers.get('content-length', '0')) > 12_000_000:
            return JSONResponse({'detail': '12MB 이하의 파일을 등록하세요.'}, 413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({'detail': str(exc)}, 400)


def ensure_idle(draft_id):
    if rows("SELECT id FROM jobs WHERE resource=? AND status IN ('queued','running')", ('draft:' + draft_id,)):
        raise ValueError('이 초안의 작업이 진행 중입니다. 완료 후 수정하세요.')
    if rows('SELECT id FROM publications WHERE draft_id=?', (draft_id,)):
        raise ValueError('게시 요청이 있는 초안은 고정됩니다. Buffer에서 해당 게시물을 관리하세요.')


def draft_view(draft):
    for field in ('payload', 'references_json', 'news_snapshot', 'manifest'):
        draft[field] = json.loads(draft[field]) if draft.get(field) else None
    return draft


@app.get('/api/status')
def status():
    cfg = config()
    return {'model': cfg['OPENAI_MODEL'], 'reasoning': cfg['OPENAI_REASONING_EFFORT'], 'ai_configured': bool(cfg['OPENAI_API_KEY']), 'buffer_configured': bool(cfg['BUFFER_API_KEY'] and cfg['BUFFER_CHANNEL_ID']), 'rss_interval_minutes': cfg['RSS_INTERVAL_MINUTES'], 'books': BOOKS, 'counts': {table: one(f'SELECT count(*) AS n FROM {table}')['n'] for table in ('articles', 'drafts', 'documents', 'publications')}, 'usage': rows('SELECT task,model,usage,created_at FROM ai_usage ORDER BY created_at DESC LIMIT 10')}


@app.get('/api/jobs')
def jobs():
    return rows('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 15')


@app.get('/api/feeds')
def feeds():
    return rows('SELECT * FROM feeds ORDER BY name')


class FeedInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(max_length=2000)


@app.post('/api/feeds')
def add_feed(data: FeedInput):
    news.public_url(data.url)
    if rows('SELECT id FROM feeds WHERE url=?', (data.url,)):
        raise ValueError('이미 등록된 피드입니다.')
    with db() as c:
        c.execute('INSERT INTO feeds(id,name,url) VALUES(?,?,?)', (uid(), data.name, data.url))
    return {'ok': True}


class Toggle(BaseModel):
    enabled: bool


@app.patch('/api/feeds/{feed_id}')
def toggle_feed(feed_id: str, data: Toggle):
    with db() as c:
        c.execute('UPDATE feeds SET enabled=? WHERE id=?', (data.enabled, feed_id))
    return {'ok': True}


@app.post('/api/collect')
def collect():
    return submit('뉴스 수집', 'feeds', news.collect)


@app.get('/api/articles')
def articles():
    return rows('''SELECT a.*,f.name AS source FROM articles a LEFT JOIN feeds f ON f.id=a.feed_id
        ORDER BY a.score IS NULL,a.score DESC,a.published_at DESC LIMIT 300''')


@app.post('/api/rank')
def rank():
    return submit('AI 우선순위', 'ranking', ai.rank)


@app.post('/api/articles/{article_id}/original')
def original(article_id: str):
    return submit('뉴스 원문 가져오기', 'article:' + article_id, lambda: news.original(article_id))


class BodyInput(BaseModel):
    body: str = Field(min_length=350, max_length=60000)


@app.put('/api/articles/{article_id}/body')
def save_body(article_id: str, data: BodyInput):
    one('SELECT id FROM articles WHERE id=?', (article_id,))
    if rows("SELECT id FROM jobs WHERE resource=? AND status IN ('queued','running')", ('article:' + article_id,)):
        raise ValueError('원문 가져오기가 완료된 후 확인하세요.')
    with db() as c:
        c.execute('UPDATE articles SET body=?,body_method=?,body_checked_at=? WHERE id=?', (data.body, 'user_checked', now(), article_id))
    return {'ok': True}


@app.post('/api/articles/{article_id}/generate')
def generate(article_id: str):
    return submit('영어 콘텐츠 초안', 'article:' + article_id, lambda: ai.generate(article_id))


@app.get('/api/articles.xlsx')
def excel():
    wb = Workbook()
    sheet = wb.active
    sheet.title = 'News'
    fields = ['source', 'title', 'url', 'published_at', 'summary', 'score', 'rationale']
    sheet.append(fields)
    for article in articles():
        sheet.append([("'" + value if value and value.lstrip().startswith(('=', '+', '-', '@')) else value) if isinstance(value := article.get(k), str) else value for k in fields])
    sheet.freeze_panes = 'A2'
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return StreamingResponse(stream, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="kostolany-news.xlsx"'})


@app.get('/api/documents')
def documents():
    return knowledge.catalog()


@app.post('/api/documents')
async def upload_document(book_id: str = Form(...), title: str = Form(...), edition: str = Form('판본 미입력'), file: UploadFile = File(...)):
    if Path(file.filename or '').suffix.lower() not in ('.txt', '.md'):
        raise ValueError('.txt 또는 .md 파일을 등록하세요.')
    raw = await file.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError('파일은 10MB 이하여야 합니다.')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = raw.decode('cp949')
        except UnicodeDecodeError as exc:
            raise ValueError('UTF-8로 저장한 텍스트 파일을 등록하세요.') from exc
    return knowledge.import_document(book_id, title[:200], edition[:200], text)


@app.get('/api/documents/search')
def search_documents(q: str = ''):
    return knowledge.retrieve(q[:500])


@app.get('/api/documents/{doc_id}')
def document(doc_id: str):
    return one('SELECT * FROM documents WHERE id=?', (doc_id,))


@app.delete('/api/documents/{doc_id}')
def delete_document(doc_id: str):
    doc = one('SELECT * FROM documents WHERE id=?', (doc_id,))
    if doc['origin'] == 'provisional':
        raise ValueError('임시 자료는 같은 책의 직접 입력 자료를 등록하면 자동 제외됩니다.')
    with db() as c:
        c.execute('DELETE FROM documents WHERE id=?', (doc_id,))
    return {'ok': True}


@app.get('/api/drafts')
def drafts():
    return [draft_view(r) for r in rows('SELECT * FROM drafts ORDER BY created_at DESC LIMIT 100')]


class DraftEdit(BaseModel):
    version: int
    content: Content


@app.put('/api/drafts/{draft_id}')
def edit_draft(draft_id: str, data: DraftEdit):
    ensure_idle(draft_id)
    draft = one('SELECT * FROM drafts WHERE id=?', (draft_id,))
    ai.validate_content(data.content, json.loads(draft['references_json']), json.loads(draft['news_snapshot']))
    with db() as c:
        changed = c.execute("UPDATE drafts SET payload=?,title=?,version=version+1,rendered_version=NULL,approved_version=NULL,manifest=NULL,status='draft',updated_at=? WHERE id=? AND version=?", (data.content.model_dump_json(), data.content.title, now(), draft_id, data.version)).rowcount
        if not changed:
            raise ValueError('다른 수정이 먼저 저장됐습니다. 새로고침하세요.')
    return {'ok': True}


@app.post('/api/drafts/{draft_id}/render')
def render_draft(draft_id: str):
    ensure_idle(draft_id)
    return submit('캐러셀·릴스 제작', 'draft:' + draft_id, lambda: render.render(draft_id))


class VersionInput(BaseModel):
    version: int


@app.post('/api/drafts/{draft_id}/approve')
def approve(draft_id: str, data: VersionInput):
    ensure_idle(draft_id)
    with db() as c:
        changed = c.execute("UPDATE drafts SET approved_version=version,status='approved',updated_at=? WHERE id=? AND version=? AND rendered_version=version AND manifest IS NOT NULL", (now(), draft_id, data.version)).rowcount
        if not changed:
            raise ValueError('최신 완성본을 제작한 뒤 검수해 주세요.')
    return {'ok': True}


class ScheduleInput(BaseModel):
    version: int
    format: Literal['carousel', 'reel']
    due_at: str
    urls: list[str] = Field(min_length=1, max_length=7)


@app.post('/api/drafts/{draft_id}/schedule')
def schedule(draft_id: str, data: ScheduleInput):
    return submit('Buffer 예약 전송', 'draft:' + draft_id, lambda: publishing.schedule(draft_id, data.version, data.format, data.due_at, data.urls))


@app.get('/api/publications')
def publications():
    result = rows('''SELECT p.*,d.title FROM publications p JOIN drafts d ON p.draft_id=d.id ORDER BY p.created_at DESC''')
    for item in result:
        item.pop('payload')
        metric = rows('SELECT * FROM metrics WHERE publication_id=? ORDER BY rowid DESC LIMIT 1', (item['id'],))
        item['metrics'] = json.loads(metric[0]['payload']) if metric else None
        item['metrics_updated_at'] = metric[0]['provider_updated_at'] if metric else None
    return result


class SyncInput(BaseModel):
    remote_id: str | None = None


@app.post('/api/publications/{pub_id}/sync')
def sync(pub_id: str, data: SyncInput):
    return submit('게시·성과 확인', 'pub:' + pub_id, lambda: publishing.sync(pub_id, data.remote_id))


@app.post('/api/buffer/check')
def check_buffer():
    return submit('Buffer 연결 확인', 'buffer', publishing.channel_info)


@app.post('/api/analyze')
def analyze():
    return submit('AI 성과 분석', 'analysis', ai.analyze)


@app.get('/api/reports')
def reports():
    return [{**r, 'body': json.loads(r['body'])} for r in rows('SELECT * FROM reports ORDER BY created_at DESC LIMIT 10')]


# Only generated media is exposed. The SQLite database and full book text stay private.
(DATA / 'artifacts').mkdir(parents=True, exist_ok=True)
app.mount('/media', StaticFiles(directory=DATA / 'artifacts'), name='media')
if (ROOT / 'dist').exists():
    app.mount('/', StaticFiles(directory=ROOT / 'dist', html=True), name='frontend')
