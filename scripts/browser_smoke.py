"""UI check using an existing Playwright installation. No paid API calls."""
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(__file__).resolve().parents[1] / 'data' / 'checks'
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='msedge')
    page = browser.new_page(viewport={'width': 1440, 'height': 1100}, device_scale_factor=1)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765', wait_until='networkidle')
    page.get_by_role('heading', name='생각을 콘텐츠로 만드는 곳').wait_for()
    page.screenshot(path=str(out / 'desktop.png'), full_page=True)
    for label, heading in [('뉴스 수집·선정','뉴스 수집·선정'), ('코스톨라니 자료실','코스톨라니 자료실'), ('콘텐츠 제작','콘텐츠 제작'), ('검수·예약 게시','검수·예약 게시'), ('성과 분석','성과 분석'), ('연결 설정','연결 설정')]:
        page.get_by_role('navigation').get_by_role('button', name=label, exact=True).click()
        page.get_by_role('heading', name=heading, exact=True).wait_for()
    page.get_by_role('navigation').get_by_role('button', name='코스톨라니 자료실').click()
    page.get_by_role('textbox', name='책 자료 검색').fill('경제')
    page.get_by_role('button', name='검색', exact=True).click()
    page.locator('.reference').first.wait_for()
    page.screenshot(path=str(out / 'library.png'), full_page=True)
    page.get_by_role('navigation').get_by_role('button', name='뉴스 수집·선정').click()
    page.locator('.news-row').first.wait_for()
    page.locator('.news-row').first.click()
    page.get_by_role('dialog').wait_for()
    assert page.get_by_role('button', name='영어 초안 생성').is_disabled()
    page.get_by_role('button', name='닫기', exact=True).click()
    page.set_viewport_size({'width': 390, 'height': 844})
    for label in ['작업실', '뉴스 수집·선정', '코스톨라니 자료실', '연결 설정']:
        page.get_by_role('navigation').get_by_role('button', name=label, exact=True).click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), label + ' horizontal overflow'
    page.get_by_role('navigation').get_by_role('button', name='작업실', exact=True).click()
    page.screenshot(path=str(out / 'mobile.png'), full_page=True)
    assert not errors, errors
    browser.close()
print('UI passed: 7 screens, source search, article modal, mobile width, no page errors.')
