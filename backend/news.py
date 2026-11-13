import calendar
import hashlib
import ipaddress
import socket
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from .core import db, now, one, rows, safe_error, uid


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError('일반 HTTP(S) 주소를 입력하세요.')
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except OSError as exc:
        raise ValueError('주소의 호스트를 찾을 수 없습니다.') from exc
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('공개된 인터넷 주소만 사용할 수 있습니다.')
    return url


def fetch(url, max_bytes=5_000_000):
    with httpx.Client(timeout=25, follow_redirects=False, headers={'User-Agent': 'KostolanyStudio/0.1 (personal RSS reader)'}) as client:
        for _ in range(6):
            public_url(url)
            with client.stream('GET', url) as r:
                if r.is_redirect:
                    url = str(r.url.join(r.headers['location']))
                    continue
                if r.status_code >= 400:
                    raise ValueError(f'원격 서버가 HTTP {r.status_code}로 응답했습니다. 원문을 직접 확인해 주세요.')
                raw = bytearray()
                for data in r.iter_bytes():
                    raw.extend(data)
                    if len(raw) > max_bytes:
                        raise ValueError('응답 파일이 너무 큽니다.')
                return bytes(raw), str(r.url)
    raise ValueError('주소 이동 횟수가 너무 많습니다.')


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        return ''
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k not in ('fbclid', 'gclid')])
    return urlunsplit((p.scheme, p.netloc.lower(), p.path or '/', query, ''))


def plain(value):
    return BeautifulSoup(value or '', 'html.parser').get_text(' ', strip=True)


def ingest(feed_id, raw):
    parsed = feedparser.parse(raw)
    if not parsed.version:
        raise ValueError('유효한 RSS/Atom 피드가 아닙니다.')
    added = 0
    with db() as c:
        for item in parsed.entries[:300]:
            url = canonical(item.get('link', ''))
            if not url:
                continue
            date = item.get('published_parsed') or item.get('updated_parsed')
            published = datetime.fromtimestamp(calendar.timegm(date), timezone.utc).isoformat() if date else None
            guid = item.get('id') or None
            result = c.execute('''INSERT OR IGNORE INTO articles(id,feed_id,title,url,guid,published_at,collected_at,summary)
                VALUES(?,?,?,?,?,?,?,?)''', (uid(), feed_id, plain(item.get('title', 'Untitled'))[:1000], url, guid, published, now(), plain(item.get('summary', ''))[:12000]))
            added += result.rowcount
    return added


def collect():
    results = []
    for feed in rows('SELECT * FROM feeds WHERE enabled=1'):
        error, added = None, 0
        try:
            raw, _ = fetch(feed['url'])
            added = ingest(feed['id'], raw)
        except Exception as exc:
            error = safe_error(exc)
        with db() as c:
            c.execute('UPDATE feeds SET checked_at=?,error=? WHERE id=?', (now(), error, feed['id']))
        results.append({'feed': feed['name'], 'added': added, 'error': error})
    if results and all(x['error'] for x in results):
        raise ValueError('모든 피드 수집이 실패했습니다. 뉴스 출처별 오류를 확인하세요.')
    return results


def original(article_id):
    article = one('SELECT * FROM articles WHERE id=?', (article_id,))
    raw, final_url = fetch(article['url'])
    soup = BeautifulSoup(raw, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form']):
        tag.decompose()
    root = soup.find('article') or soup.find('main') or soup
    paragraphs = [p.get_text(' ', strip=True) for p in root.find_all('p')]
    body = '\n\n'.join(p for p in paragraphs if len(p) > 45)
    if len(body) < 350:
        raise ValueError('원문을 충분히 가져오지 못했습니다. 접근 가능한 본문을 직접 입력하세요.')
    with db() as c:
        c.execute('UPDATE articles SET body=?,body_method=?,body_checked_at=NULL WHERE id=?', (body[:60000], 'auto_extracted', article_id))
    return {'characters': min(len(body), 60000), 'url': final_url, 'needs_review': True}
