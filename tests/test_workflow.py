import hashlib
import io
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import imageio_ffmpeg
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend import ai, core, knowledge, news, publishing, render
from backend.main import app
from backend.schemas import Content


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'DATA', tmp_path)
    monkeypatch.setattr(render, 'DATA', tmp_path)
    monkeypatch.setattr(publishing, 'DATA', tmp_path)
    monkeypatch.setattr(core, 'config', lambda: {'OPENAI_API_KEY': '', 'OPENAI_MODEL': 'gpt-6-astra', 'OPENAI_REASONING_EFFORT': 'xhigh', 'BUFFER_API_KEY': '', 'BUFFER_CHANNEL_ID': '', 'RSS_INTERVAL_MINUTES': '0'})
    with TestClient(app) as client:
        yield client, tmp_path


RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title><item><guid>one</guid><title>Policy expectations shift</title><link>https://example.com/article?utm_source=rss</link><description>Investors review interest rates.</description><pubDate>Fri, 13 Nov 2026 08:00:00 GMT</pubDate></item></channel></rss>'''
BODY = 'Investors reviewed the latest policy statement. The statement described uncertainty about the economic outlook. ' * 6


def add_article():
    feed = core.rows('SELECT id FROM feeds')[0]['id']
    news.ingest(feed, RSS)
    article = core.rows('SELECT * FROM articles')[0]
    with core.db() as c:
        c.execute('UPDATE articles SET body=?,body_checked_at=?,search_terms=? WHERE id=?', (BODY, core.now(), 'economy psychology 심리 경제', article['id']))
    return core.one('SELECT * FROM articles WHERE id=?', (article['id'],))


def make_content(refs):
    return Content.model_validate({'title': 'A headline is only the beginning', 'summary_ko': '테스트용 검수 요약', 'interpretation_ko': '테스트용 현대적 해석',
        'facts': [{'claim': 'Investors reviewed the statement.', 'evidence': 'Investors reviewed the latest policy statement.'}],
        'reference_ids': [refs[0]['id']],
        'carousel': [{'headline': h, 'body': b} for h, b in [
            ('A headline is only the beginning', 'Before reacting, ask what changed in the facts and what changed in expectations.'),
            ('Read the evidence first', 'Start with the actual statement. A headline cannot carry all the context.'),
            ('Consider the crowd', 'Our modern interpretation: examine expectations alongside the event itself.'),
            ('Make room for uncertainty', 'What evidence would change your interpretation of this story?')]],
        'reel': [{'headline': h, 'body': b, 'seconds': 5} for h, b in [
            ('Pause before reacting', 'A new headline does not automatically mean a new investment thesis.'),
            ('Find the original source', 'Separate what was reported from what people expect next.'),
            ('Look at expectations', 'Our interpretation connects this story to investor psychology.'),
            ('Ask a better question', 'What would make you reconsider your first reaction?')]],
        'caption': 'A question for the next market headline. Our modern interpretation inspired by Kostolany. Unofficial.',
        'review_notes_ko': ['검증용 콘텐츠. 실제 뉴스 해설이 아닙니다.']})


def add_draft():
    article = add_article()
    refs = knowledge.retrieve('economy 심리 경제')
    content = make_content(refs)
    draft_id = core.uid()
    with core.db() as c:
        c.execute('INSERT INTO drafts(id,article_id,title,payload,references_json,news_snapshot,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (draft_id, article['id'], content.title, content.model_dump_json(), core.dumps(refs), core.dumps(article), core.now(), core.now()))
    return draft_id, content


def test_feed_dedup_and_bad_feed(studio):
    feed = core.rows('SELECT id FROM feeds')[0]['id']
    assert news.ingest(feed, RSS) == 1
    assert news.ingest(feed, RSS) == 0
    assert news.ingest(feed, RSS.replace(b'?utm_source=rss', b'?utm_source=another')) == 0
    article = core.rows('SELECT * FROM articles')[0]
    assert article['url'] == 'https://example.com/article'
    assert article['published_at'].startswith('2026-11-13T08:00')
    with pytest.raises(ValueError):
        news.ingest(feed, b'<html>Error</html>')


def test_knowledge_scope_pages_and_user_priority(studio):
    client, _ = studio
    data = '# Economics\n[p. 12]\n경제와 주식시장은 단기적으로 다르게 움직일 수 있습니다. ' * 25
    result = knowledge.import_document('money', 'My book', '2023', data)
    assert knowledge.import_document('money', 'Repeated', '2023', data)['duplicate']
    assert core.rows('SELECT count(*) n FROM documents')[0]['n'] == 7
    matched = knowledge.retrieve('경제 economy')
    assert any(r['document_id'] == result['document_id'] for r in matched)
    assert not any(r['book_id'] == 'money' and r['origin'] == 'provisional' for r in matched)
    assert any('p. 12' in r['locator'] for r in matched)
    assert any('Economics' in r['locator'] and 'p. 12' in r['locator'] for r in matched)
    with pytest.raises(ValueError):
        knowledge.import_document('unknown-book', 'Other', '', data)
    response = client.post('/api/documents', data={'book_id': 'psychology', 'title': '장별 파일', 'edition': '2023'}, files={'file': ('book.txt', data.encode('utf-8'), 'text/plain')})
    assert response.status_code == 200
    assert client.delete('/api/documents/' + result['document_id']).status_code == 200
    assert any(r['book_id'] == 'money' and r['origin'] == 'provisional' for r in knowledge.retrieve('경제 economy'))


def test_large_book_split_preserves_content_and_bounds():
    text = '# Chapter\n[p. 20]\n' + ('Economy, market psychology and independent thinking.\n' * 2000)
    pieces = knowledge.split_text(text)
    assert len(pieces) > 30
    assert all(len(body) <= 1800 for _, body in pieces)
    assert all('p. 20' in locator for locator, body in pieces if 'Economy' in body)


def test_reference_and_news_evidence_checks(studio):
    article = add_article()
    refs = knowledge.retrieve('economy')
    content = make_content(refs)
    ai.validate_content(content, refs, article)
    content.reference_ids = ['fabricated-id']
    with pytest.raises(ValueError, match='참고 자료'):
        ai.validate_content(content, refs, article)
    content.reference_ids = [refs[0]['id']]
    content.facts[0].evidence = 'This quotation was never in the actual article.'
    with pytest.raises(ValueError, match='원문'):
        ai.validate_content(content, refs, article)


def test_generate_with_supplied_books_and_real_local_persistence(studio, monkeypatch):
    article = add_article()
    calls = []
    def fake_model(task, instruction, data, schema):
        calls.append(data)
        return make_content(data['references'])
    monkeypatch.setattr(ai, 'call', fake_model)
    result = ai.generate(article['id'])
    saved = core.one('SELECT * FROM drafts WHERE id=?', (result['draft_id'],))
    assert json.loads(saved['payload'])['carousel']
    assert json.loads(saved['payload'])['reel']
    assert {r['book_id'] for r in calls[0]['references']} <= set(core.BOOKS)
    assert json.loads(saved['news_snapshot'])['body'] == BODY


def test_ai_schema_model_and_incomplete_response(studio, monkeypatch):
    from backend.schemas import SearchTerms
    monkeypatch.setattr(ai, 'config', lambda: {'OPENAI_API_KEY': 'test-secret', 'OPENAI_MODEL': 'gpt-6-astra', 'OPENAI_REASONING_EFFORT': 'xhigh'})
    calls = []
    def fake_post(url, **kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(200, json={'status': 'incomplete', 'usage': {'input_tokens': 3}})
    monkeypatch.setattr(ai.httpx, 'post', fake_post)
    with pytest.raises(ValueError, match='완료되지'):
        ai.call('test', 'Search terms', {}, SearchTerms)
    assert calls[0]['model'] == 'gpt-6-astra'
    assert calls[0]['reasoning']['effort'] == 'xhigh'
    assert calls[0]['store'] is False
    assert calls[0]['text']['format']['schema']['additionalProperties'] is False


def test_approval_requires_render_and_edit_invalidates(studio):
    client, _ = studio
    draft_id, content = add_draft()
    assert client.post(f'/api/drafts/{draft_id}/approve', json={'version': 1}).status_code == 400
    with core.db() as c:
        c.execute('UPDATE drafts SET rendered_version=1,manifest=? WHERE id=?', ('{}', draft_id))
    assert client.post(f'/api/drafts/{draft_id}/approve', json={'version': 1}).status_code == 200
    content.caption = 'Edited caption with original interpretation.'
    response = client.put(f'/api/drafts/{draft_id}', json={'version': 1, 'content': content.model_dump()})
    assert response.status_code == 200
    updated = core.one('SELECT * FROM drafts WHERE id=?', (draft_id,))
    assert updated['version'] == 2
    assert updated['approved_version'] is None and updated['manifest'] is None
    assert client.put(f'/api/drafts/{draft_id}', json={'version': 1, 'content': content.model_dump()}).status_code == 400


def test_api_secrets_and_cross_origin(studio, monkeypatch):
    import backend.main as main
    client, _ = studio
    monkeypatch.setattr(main, 'config', lambda: {'OPENAI_API_KEY': 'never-expose-this', 'OPENAI_MODEL': 'gpt-6-astra', 'OPENAI_REASONING_EFFORT': 'xhigh', 'BUFFER_API_KEY': 'private', 'BUFFER_CHANNEL_ID': 'id', 'RSS_INTERVAL_MINUTES': '0'})
    response = client.get('/api/status')
    assert response.json()['ai_configured']
    assert 'never-expose-this' not in response.text and 'private' not in response.text
    assert client.post('/api/collect', headers={'Origin': 'https://untrusted.example'}).status_code == 403
    assert client.get('/api/status', headers={'Host': 'untrusted.example'}).status_code == 400


def test_excel_formula_safety(studio):
    from openpyxl import load_workbook
    client, _ = studio
    article = add_article()
    with core.db() as c:
        c.execute('UPDATE articles SET title=? WHERE id=?', ('=1+1', article['id']))
    result = client.get('/api/articles.xlsx')
    wb = load_workbook(io.BytesIO(result.content))
    assert wb.active['B2'].data_type == 's'
    assert wb.active['B2'].value == "'=1+1"


def test_job_records_failure_without_secret(studio):
    def fail():
        raise ValueError('자료가 없습니다.')
    job = core.submit('test', 'resource', fail)['job_id']
    for _ in range(100):
        state = core.one('SELECT * FROM jobs WHERE id=?', (job,))
        if state['status'] == 'failed':
            break
        time.sleep(.02)
    assert state['status'] == 'failed'
    assert state['message'] == '자료가 없습니다.'


def test_actual_carousel_and_video_render(studio):
    _, tmp_path = studio
    draft_id, _ = add_draft()
    manifest = render.render(draft_id)
    folder = tmp_path / 'artifacts' / Path(manifest['reel'].removeprefix('/media/')).parent
    image = Image.open(folder / 'card-01.png')
    assert image.size == (1080, 1350)
    assert (folder / 'carousel.zip').is_file()
    assert (folder / 'reel.mp4').read_bytes()[4:8] == b'ftyp'
    reader = imageio_ffmpeg.read_frames(str(folder / 'reel.mp4'))
    metadata = next(reader)
    reader.close()
    assert metadata['size'] == (1080, 1920)
    assert abs(metadata['duration'] - 20) < .3
    assert metadata['fps'] == 24
    assert 'Sources:' in manifest['caption']
    assert 'REFERENCE' not in (folder / 'sources.json').read_text(encoding='utf-8')
    assert core.one('SELECT status FROM drafts WHERE id=?', (draft_id,))['status'] == 'review'


def setup_publication(monkeypatch):
    draft_id, _ = add_draft()
    raw = b'approved-image'
    manifest = {'carousel': ['/media/folder/card-01.png'], 'reel': '/media/folder/reel.mp4', 'hashes': {'card-01.png': hashlib.sha256(raw).hexdigest()}}
    with core.db() as c:
        c.execute('UPDATE drafts SET manifest=?,rendered_version=1,approved_version=1 WHERE id=?', (core.dumps(manifest), draft_id))
    monkeypatch.setattr(publishing, 'channel_info', lambda: {'id': 'expected-channel'})
    monkeypatch.setattr(publishing, 'fetch', lambda *a, **kw: (raw, a[0]))
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return draft_id, due


def test_publish_timeout_does_not_duplicate(studio, monkeypatch):
    draft_id, due = setup_publication(monkeypatch)
    mutations = []
    def unknown(query, variables):
        mutations.append(variables)
        raise httpx.ReadTimeout('not received')
    monkeypatch.setattr(publishing, 'graphql', unknown)
    with pytest.raises(httpx.ReadTimeout):
        publishing.schedule(draft_id, 1, 'carousel', due, ['https://example.com/asset.png'])
    assert core.rows('SELECT status FROM publications')[0]['status'] == 'unknown'
    with pytest.raises(ValueError, match='이미'):
        publishing.schedule(draft_id, 1, 'carousel', due, ['https://example.com/asset.png'])
    assert len(mutations) == 1
    assert mutations[0]['input']['metadata']['instagram']['type'] == 'post'


def test_publish_wrong_asset_and_approval_block(studio, monkeypatch):
    draft_id, due = setup_publication(monkeypatch)
    monkeypatch.setattr(publishing, 'fetch', lambda *a, **kw: (b'different image', a[0]))
    with pytest.raises(ValueError, match='다릅니다'):
        publishing.schedule(draft_id, 1, 'carousel', due, ['https://example.com/asset.png'])
    assert not core.rows('SELECT * FROM publications')
    with pytest.raises(ValueError, match='검수'):
        publishing.schedule(draft_id, 2, 'carousel', due, ['https://example.com/asset.png'])


def test_publish_and_sync_actual_status_only(studio, monkeypatch):
    draft_id, due = setup_publication(monkeypatch)
    monkeypatch.setattr(publishing, 'graphql', lambda *a: {'createPost': {'post': {'id': 'remote', 'status': 'buffer'}}})
    result = publishing.schedule(draft_id, 1, 'carousel', due, ['https://example.com/asset.png'])
    assert result['status'] == 'buffer'
    assert not core.rows('SELECT * FROM metrics')
    pub = core.rows('SELECT * FROM publications')[0]
    payload = json.loads(pub['payload'])
    monkeypatch.setattr(publishing, 'read_post', lambda _: {'id': 'remote', 'channelId': 'expected-channel', 'text': payload['text'], 'status': 'sent', 'externalLink': 'https://instagram.com/p/example', 'metrics': [{'type': 'reach', 'name': 'Reach', 'value': 4, 'unit': 'count'}], 'metricsUpdatedAt': core.now()})
    publishing.sync(pub['id'])
    assert core.rows('SELECT status FROM publications')[0]['status'] == 'sent'
    assert len(core.rows('SELECT * FROM metrics')) == 1
    assert json.loads(core.rows('SELECT * FROM metrics')[0]['payload'])[0]['value'] == 4
