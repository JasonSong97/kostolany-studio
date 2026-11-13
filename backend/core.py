import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('STUDIO_DATA_DIR', ROOT / 'data')).resolve()
BOOKS = {'money': '돈, 뜨겁게 사랑하고 차갑게 다루어라', 'psychology': '투자는 심리게임이다', 'practice': '실전 투자강의'}
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix='studio')
JOB_LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def config():
    values = {**dotenv_values(ROOT / '.env'), **os.environ}
    return {k: values.get(k, default) or default for k, default in {
        'OPENAI_API_KEY': '', 'OPENAI_MODEL': 'gpt-6-astra',
        'OPENAI_REASONING_EFFORT': 'xhigh', 'BUFFER_API_KEY': '',
        'BUFFER_CHANNEL_ID': '', 'RSS_INTERVAL_MINUTES': '0',
    }.items()}


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def db():
    conn = sqlite3.connect(DATA / 'studio.db', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(sql, args=()):
    with db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    result = rows(sql, args)
    if not result:
        raise ValueError('해당 항목을 찾을 수 없습니다.')
    return result[0]


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / 'artifacts').mkdir(exist_ok=True)
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS feeds(id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, checked_at TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS articles(id TEXT PRIMARY KEY, feed_id TEXT REFERENCES feeds(id), title TEXT NOT NULL, url TEXT UNIQUE NOT NULL, guid TEXT, published_at TEXT, collected_at TEXT NOT NULL, summary TEXT NOT NULL, body TEXT, body_method TEXT, body_checked_at TEXT, score INTEGER, rationale TEXT, event_group TEXT, search_terms TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS article_guid ON articles(feed_id,guid) WHERE guid IS NOT NULL;
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, book_id TEXT NOT NULL, title TEXT NOT NULL, edition TEXT NOT NULL, origin TEXT NOT NULL, source_url TEXT, text TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(book_id,content_hash));
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE, book_id TEXT NOT NULL, locator TEXT NOT NULL, text TEXT NOT NULL, keywords TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id), title TEXT NOT NULL, payload TEXT NOT NULL, references_json TEXT NOT NULL, news_snapshot TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, rendered_version INTEGER, approved_version INTEGER, status TEXT NOT NULL DEFAULT 'draft', manifest TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS publications(id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES drafts(id), version INTEGER NOT NULL, format TEXT NOT NULL, due_at TEXT NOT NULL, status TEXT NOT NULL, remote_id TEXT, remote_url TEXT, payload TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, checked_at TEXT, UNIQUE(draft_id,version,format));
        CREATE TABLE IF NOT EXISTS metrics(id TEXT PRIMARY KEY, publication_id TEXT NOT NULL REFERENCES publications(id), fetched_at TEXT NOT NULL, provider_updated_at TEXT, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, resource TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, message TEXT, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_usage(id TEXT PRIMARY KEY, task TEXT NOT NULL, model TEXT NOT NULL, usage TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL);
        ''')
        for name, url in [('BBC Business', 'https://feeds.bbci.co.uk/news/business/rss.xml'), ('CNBC US', 'https://www.cnbc.com/id/100003114/device/rss/rss.html'), ('Federal Reserve', 'https://www.federalreserve.gov/feeds/press_monetary.xml')]:
            c.execute('INSERT OR IGNORE INTO feeds(id,name,url) VALUES(?,?,?)', (uid(), name, url))
        c.execute("UPDATE jobs SET status='failed', message='앱이 종료되어 작업이 중단됐습니다.',updated_at=? WHERE status IN ('queued','running')", (now(),))
        c.execute("UPDATE publications SET status='unknown',error='전송 도중 앱 종료. Buffer에서 확인 후 게시 ID를 연결하세요.' WHERE status='sending'")


def safe_error(exc):
    # Do not persist upstream response bodies or bearer tokens.
    if isinstance(exc, ValueError):
        text = str(exc)[:500]
        for key in ('OPENAI_API_KEY', 'BUFFER_API_KEY'):
            secret = config()[key]
            if secret:
                text = text.replace(secret, '[redacted]')
        return text
    return f'작업 실패 ({type(exc).__name__}). 연결과 입력을 확인해 주세요.'


def submit(kind, resource, fn):
    with JOB_LOCK:
        active = rows("SELECT id FROM jobs WHERE resource=? AND status IN ('queued','running')", (resource,))
        if active:
            return {'job_id': active[0]['id']}
        job_id = uid()
        with db() as c:
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)', (job_id, resource, kind, 'queued', '대기 중', None, now(), now()))
    EXECUTOR.submit(run_job, job_id, fn)
    return {'job_id': job_id}


def run_job(job_id, fn):
    with db() as c:
        c.execute("UPDATE jobs SET status='running',message='진행 중',updated_at=? WHERE id=?", (now(), job_id))
    try:
        result = fn()
        with db() as c:
            c.execute("UPDATE jobs SET status='done',message='완료',result=?,updated_at=? WHERE id=?", (dumps(result), now(), job_id))
    except Exception as exc:
        with db() as c:
            c.execute("UPDATE jobs SET status='failed',message=?,updated_at=? WHERE id=?", (safe_error(exc), now(), job_id))
