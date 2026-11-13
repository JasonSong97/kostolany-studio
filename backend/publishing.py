import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .core import DATA, config, db, dumps, now, one, rows, uid
from .news import fetch, public_url
from .render import caption_for


def graphql(query, variables):
    cfg = config()
    if not cfg['BUFFER_API_KEY']:
        raise ValueError('.env에 BUFFER_API_KEY를 설정하세요.')
    response = httpx.post('https://api.buffer.com', headers={'Authorization': 'Bearer ' + cfg['BUFFER_API_KEY']}, json={'query': query, 'variables': variables}, timeout=60)
    if response.status_code >= 400:
        raise ValueError(f'Buffer HTTP {response.status_code}. Buffer 계정에서 처리 여부를 확인하세요.')
    result = response.json()
    if result.get('errors'):
        raise ValueError('Buffer API 요청 오류입니다. 연결 권한·채널·요청 형식을 확인하세요.')
    return result.get('data', {})


def channel_info():
    channel_id = config()['BUFFER_CHANNEL_ID']
    if not channel_id:
        raise ValueError('.env에 BUFFER_CHANNEL_ID를 설정하세요.')
    result = graphql('query($input:ChannelInput!){channel(input:$input){id name service isDisconnected isLocked}}', {'input': {'id': channel_id}})['channel']
    if result['service'] != 'instagram' or result['isDisconnected'] or result['isLocked']:
        raise ValueError('사용 가능한 Instagram 채널을 연결하세요.')
    return result


def schedule(draft_id, version, format_name, due_at, urls):
    draft = one('SELECT * FROM drafts WHERE id=?', (draft_id,))
    if draft['version'] != version or draft['approved_version'] != version or draft['rendered_version'] != version:
        raise ValueError('현재 제작 버전의 검수 완료가 필요합니다.')
    if rows('SELECT id FROM publications WHERE draft_id=? AND format=?', (draft_id, format_name)):
        raise ValueError('이 형식의 게시 요청이 이미 있습니다. 상태를 확인하세요.')
    if format_name not in ('carousel', 'reel'):
        raise ValueError('게시 형식을 확인하세요.')
    date = datetime.fromisoformat(due_at.replace('Z', '+00:00'))
    if date.tzinfo is None or date <= datetime.now(timezone.utc):
        raise ValueError('시간대가 포함된 미래의 예약 시간을 지정하세요.')
    manifest = json.loads(draft['manifest'])
    local_urls = manifest['carousel'] if format_name == 'carousel' else [manifest['reel']]
    if len(urls) != len(local_urls):
        raise ValueError('완성본의 파일 수와 공개 URL 수가 일치해야 합니다.')
    channel = channel_info()
    # Ensure the publicly hosted asset is exactly the artifact the user reviewed.
    for url, local in zip(urls, local_urls):
        if urlsplit(url).scheme != 'https':
            raise ValueError('파일 URL은 공개 HTTPS 주소여야 합니다.')
        raw, _ = fetch(url, max_bytes=300_000_000)
        name = local.rsplit('/', 1)[-1]
        if hashlib.sha256(raw).hexdigest() != manifest['hashes'][name]:
            raise ValueError('공개 URL의 파일이 검수한 완성본과 다릅니다. 원본 파일을 업로드하세요.')
    media_type = 'image' if format_name == 'carousel' else 'video'
    payload = {'channelId': channel['id'], 'text': caption_for(draft), 'schedulingType': 'automatic', 'mode': 'customScheduled', 'dueAt': date.astimezone(timezone.utc).isoformat(), 'assets': [{media_type: {'url': u}} for u in urls], 'needsApproval': False, 'aiAssisted': True, 'metadata': {'instagram': {'type': 'post' if format_name == 'carousel' else 'reel', 'shouldShareToFeed': True}}}
    pub_id = uid()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if date <= datetime.now(timezone.utc):
            raise ValueError('파일 확인 중 예약 시간이 지났습니다. 시간을 다시 지정하세요.')
        if c.execute('SELECT id FROM publications WHERE draft_id=? AND format=?', (draft_id, format_name)).fetchone():
            raise ValueError('이 형식의 게시 요청이 이미 있습니다. 상태를 확인하세요.')
        current = c.execute('SELECT version,approved_version FROM drafts WHERE id=?', (draft_id,)).fetchone()
        if current['version'] != version or current['approved_version'] != version:
            raise ValueError('초안 버전이 변경됐습니다. 다시 검수하세요.')
        c.execute('INSERT INTO publications(id,draft_id,version,format,due_at,status,payload,created_at) VALUES(?,?,?,?,?,?,?,?)', (pub_id, draft_id, version, format_name, payload['dueAt'], 'sending', dumps(payload), now()))
    try:
        result = graphql('mutation($input:CreatePostInput!){createPost(input:$input){... on PostActionSuccess{post{id status dueAt}} ... on MutationError{message}}}', {'input': payload}).get('createPost', {})
        if not result.get('post', {}).get('id'):
            with db() as c:
                c.execute("UPDATE publications SET status='rejected',error='Buffer가 게시 생성을 거절했습니다. 계정에서 상세 오류를 확인하세요.' WHERE id=?", (pub_id,))
            raise ValueError('Buffer가 게시 생성을 거절했습니다. 게시 목록에서 확인하세요.')
        post = result['post']
        with db() as c:
            c.execute('UPDATE publications SET status=?,remote_id=?,checked_at=? WHERE id=?', (post['status'], post['id'], now(), pub_id))
        return {'publication_id': pub_id, 'status': post['status']}
    except Exception:
        with db() as c:
            c.execute("UPDATE publications SET status='unknown',error='응답 불확실. 자동 재시도하지 않습니다. Buffer에서 확인 후 게시 ID를 연결하세요.' WHERE id=? AND status='sending'", (pub_id,))
        raise


def read_post(remote_id):
    return graphql('query($input:PostInput!){post(input:$input){id channelId text status dueAt externalLink metrics{type name value unit} metricsUpdatedAt}}', {'input': {'id': remote_id}})['post']


def sync(pub_id, remote_id=None):
    pub = one('SELECT * FROM publications WHERE id=?', (pub_id,))
    remote_id = remote_id or pub['remote_id']
    if not remote_id:
        raise ValueError('Buffer에서 게시물을 확인한 뒤 게시 ID를 입력하세요.')
    post = read_post(remote_id)
    original = json.loads(pub['payload'])
    if post['channelId'] != original['channelId'] or post['text'] != original['text']:
        raise ValueError('게시 채널 또는 내용이 요청 당시와 달라 연결하지 않았습니다.')
    with db() as c:
        c.execute('UPDATE publications SET status=?,remote_id=?,remote_url=?,checked_at=?,error=NULL WHERE id=?', (post['status'], remote_id, post.get('externalLink'), now(), pub_id))
        if post['status'] == 'sent' and post.get('metrics') is not None:
            c.execute('INSERT INTO metrics VALUES(?,?,?,?,?)', (uid(), pub_id, now(), post.get('metricsUpdatedAt'), dumps(post['metrics'])))
    return post
