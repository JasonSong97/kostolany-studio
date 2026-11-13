import hashlib
import json
import re

from .core import BOOKS, ROOT, db, now, rows, uid


def split_text(text):
    """Keep heading/page markers as locators; use overlapping chunks for plain books."""
    locator = '위치 미입력'
    chapter = ''
    page = ''
    block = ''
    output = []
    def flush():
        nonlocal block
        while block.strip():
            end = min(len(block), 1800)
            if end < len(block):
                boundary = max(block.rfind('\n', 900, end), block.rfind('. ', 900, end))
                if boundary > 900:
                    end = boundary + 1
            part = block[:end].strip()
            if part:
                output.append((locator, part))
            if end >= len(block):
                break
            block = block[max(1, end - 180):]
        block = ''
    for line in text.splitlines():
        heading = re.match(r'^\s*#{1,6}\s', line)
        page_marker = re.match(r'^\s*(\[p(?:age)?\.?\s*\d+|\[\d+쪽\])', line, re.I)
        if heading or page_marker:
            flush()
            if heading:
                chapter = line.strip().lstrip('#').strip()[:140]
                page = ''
            else:
                page = line.strip()[:60]
            locator = ' · '.join(x for x in (chapter, page) if x) or '위치 미입력'
        block += line + '\n'
    flush()
    return output


def import_document(book_id, title, edition, text, origin='user', source_url='', keywords=''):
    if book_id not in BOOKS:
        raise ValueError('지정한 세 권만 등록할 수 있습니다.')
    text = text.strip()
    if len(text) < 30 or len(text) > 8_000_000:
        raise ValueError('본문은 30자 이상, 800만 자 이하로 입력하세요.')
    digest = hashlib.sha256(text.encode()).hexdigest()
    with db() as c:
        old = c.execute('SELECT id FROM documents WHERE book_id=? AND content_hash=?', (book_id, digest)).fetchone()
        if old:
            return {'document_id': old['id'], 'duplicate': True}
        doc_id = uid()
        c.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)', (doc_id, book_id, title, edition, origin, source_url, text, digest, now()))
        chunks = split_text(text)
        for i, (locator, body) in enumerate(chunks, 1):
            c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)', (uid(), doc_id, book_id, f'{locator} · 구간 {i}', body, keywords))
    return {'document_id': doc_id, 'chunks': len(chunks), 'duplicate': False}


def seed():
    for note in json.loads((ROOT / 'knowledge/seed.json').read_text(encoding='utf-8')):
        import_document(note['book_id'], note['title'], '공개 발췌의 요약 · 전체 책 아님', '# ' + note['locator'] + '\n' + note['text'], 'provisional', note['source_url'], note['keywords'])


def retrieve(query, limit=12):
    candidates = rows('''SELECT ch.*,d.title,d.edition,d.origin,d.source_url FROM chunks ch JOIN documents d ON ch.document_id=d.id
        WHERE d.origin='user' OR NOT EXISTS(SELECT 1 FROM documents u WHERE u.book_id=d.book_id AND u.origin='user')''')
    terms = set(re.findall(r'[a-zA-Z]{3,}|[가-힣]{2,}', query.lower()))
    terms |= {t[:3] for t in terms if re.fullmatch('[가-힣]{4,}', t)}
    scored = []
    for item in candidates:
        corpus = (item['text'] + ' ' + item['title'] + ' ' + item['keywords']).lower()
        score = sum(min(corpus.count(term), 3) * (2 if len(term) > 3 else 1) for term in terms)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:limit]]


def catalog():
    return rows('''SELECT d.id,d.book_id,d.title,d.edition,d.origin,d.source_url,d.created_at,length(d.text) AS characters,count(ch.id) AS chunks
        FROM documents d LEFT JOIN chunks ch ON ch.document_id=d.id GROUP BY d.id ORDER BY d.created_at DESC''')
