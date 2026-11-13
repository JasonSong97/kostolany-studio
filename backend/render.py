import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from .core import DATA, db, dumps, now, one, uid
from .schemas import Content

INK = '#172823'
PAPER = '#f4f0e6'
GOLD = '#c3a56d'


def font(size, serif=False):
    options = [Path('C:/Windows/Fonts/georgia.ttf' if serif else 'C:/Windows/Fonts/arial.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf' if serif else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    for path in options:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def wrap(text, draw, face, width):
    lines, current = [], ''
    for word in text.split():
        # Also break long unspaced tokens rather than drawing off canvas.
        if draw.textlength(word, font=face) > width:
            parts, part = [], ''
            for ch in word:
                if draw.textlength(part + ch, font=face) > width:
                    parts.append(part)
                    part = ''
                part += ch
            parts.append(part)
        else:
            parts = [word]
        for part in parts:
            candidate = f'{current} {part}'.strip()
            if current and draw.textlength(candidate, font=face) > width:
                lines.append(current)
                current = part
            else:
                current = candidate
    if current:
        lines.append(current)
    return '\n'.join(lines)


def text_box(draw, text, box, size, color, serif=False):
    x, y, w, h = box
    while size >= 23:
        face = font(size, serif)
        content = wrap(text, draw, face, w)
        bounds = draw.multiline_textbbox((0, 0), content, font=face, spacing=int(size * .3))
        if bounds[3] - bounds[1] <= h:
            draw.multiline_text((x, y - bounds[1]), content, font=face, fill=color, spacing=int(size * .3))
            return
        size -= 2
    raise ValueError('카드에 글이 너무 많습니다. 문구를 줄여 주세요.')


def card(headline, body, index, count, path, reel=False):
    h = 1920 if reel else 1350
    dark = index == 0 or reel
    bg, fg = (INK, PAPER) if dark else (PAPER, INK)
    image = Image.new('RGB', (1080, h), bg)
    d = ImageDraw.Draw(image)
    top = 260 if reel else 85
    d.text((80, top), 'KOSTOLANY WISDOM', font=font(26), fill=GOLD)
    d.line((80, top + 60, 1000, top + 60), fill=GOLD, width=2)
    d.text((80, top + 125), 'MONEY & MARKETS  /  FIELD NOTES', font=font(22), fill=GOLD)
    text_box(d, headline, (80, top + 205, 900, 430), 88 if index == 0 else 76, fg, True)
    text_box(d, body, (85, top + 680, 895, 330 if not reel else 395), 40 if not reel else 46, fg)
    base = h - (310 if reel else 90)
    d.line((80, base - 50, 1000, base - 50), fill=GOLD, width=1)
    d.text((80, base), 'OUR INTERPRETATION  /  SOURCES IN CAPTION', font=font(20), fill=GOLD)
    d.text((920, base), f'{index + 1:02d}/{count:02d}', font=font(23), fill=fg)
    if reel:
        d.rectangle((80, h - 240, 80 + 920 * ((index + 1) / count), h - 234), fill=GOLD)
    image.save(path)


def caption_for(draft):
    content = json.loads(draft['payload'])
    refs = json.loads(draft['references_json'])
    article = json.loads(draft['news_snapshot'])
    books = sorted(set({'money': '1', 'psychology': '2', 'practice': '3'}[r['book_id']] for r in refs))
    sources = '\n\nSources: ' + article['url'] + '\nKostolany three-book series: book(s) ' + ', '.join(books) + '.'
    if any(r['origin'] == 'provisional' for r in refs):
        sources += '\nBook basis: publicly available publisher excerpts, summarized.'
    sources += '\nOur modern interpretation. Unofficial. Educational content.'
    result = content['caption'] + sources
    if len(result) > 2200:
        raise ValueError('출처를 포함한 캡션이 2,200자를 초과합니다. 캡션을 줄여 주세요.')
    return result


def render(draft_id):
    draft = one('SELECT * FROM drafts WHERE id=?', (draft_id,))
    content = Content.model_validate_json(draft['payload'])
    version = draft['version']
    folder = DATA / 'artifacts' / draft_id / f'v{version}-{uid()[:8]}'
    folder.mkdir(parents=True)
    image_names = []
    for i, slide in enumerate(content.carousel):
        name = f'card-{i + 1:02d}.png'
        card(slide.headline, slide.body, i, len(content.carousel), folder / name)
        image_names.append(name)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    segments = []
    flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
    for i, scene in enumerate(content.reel):
        frame, segment = f'scene-{i + 1:02d}.png', f'segment-{i + 1:02d}.mp4'
        card(scene.headline, scene.body, i, len(content.reel), folder / frame, True)
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-loop', '1', '-framerate', '24', '-i', frame,
            '-t', str(scene.seconds), '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'stillimage', '-crf', '23', '-pix_fmt', 'yuv420p', '-r', '24', segment]
        subprocess.run(command, cwd=folder, check=True, capture_output=True, timeout=180, creationflags=flags)
        segments.append(segment)
    (folder / 'concat.txt').write_text(''.join(f"file '{name}'\n" for name in segments), encoding='utf-8')
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', 'concat.txt', '-c', 'copy', '-movflags', '+faststart', 'reel.mp4'], cwd=folder, check=True, capture_output=True, timeout=60, creationflags=flags)
    # Remove only temporary files created by this renderer, never source materials.
    for name in segments + ['concat.txt']:
        (folder / name).unlink()
    caption = caption_for(draft)
    (folder / 'caption.txt').write_text(caption, encoding='utf-8')
    source_info = {'news_url': json.loads(draft['news_snapshot'])['url'], 'references': [{k: r[k] for k in ('id', 'book_id', 'title', 'locator', 'origin', 'source_url')} for r in json.loads(draft['references_json'])]}
    (folder / 'sources.json').write_text(dumps(source_info), encoding='utf-8')
    with zipfile.ZipFile(folder / 'carousel.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for name in image_names + ['caption.txt', 'sources.json']:
            z.write(folder / name, name)
    base = '/media/' + folder.relative_to(DATA / 'artifacts').as_posix() + '/'
    manifest = {'version': version, 'carousel': [base + n for n in image_names], 'reel': base + 'reel.mp4', 'zip': base + 'carousel.zip', 'caption_url': base + 'caption.txt', 'sources_url': base + 'sources.json', 'caption': caption, 'reel_seconds': sum(s.seconds for s in content.reel), 'audio': 'none', 'hashes': {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in image_names + ['reel.mp4']}}
    with db() as c:
        updated = c.execute("UPDATE drafts SET manifest=?,rendered_version=?,approved_version=NULL,status='review',updated_at=? WHERE id=? AND version=?", (dumps(manifest), version, now(), draft_id, version)).rowcount
        if not updated:
            raise ValueError('제작 중 초안이 수정됐습니다. 최신 버전으로 다시 제작하세요.')
    return manifest
