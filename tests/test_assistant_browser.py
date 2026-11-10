import asyncio
from fastapi import HTTPException
from app import assistant_browser as browser
from app import assistant_inspection as inspection


def test_rendered_javascript_mobile_linked_pages_and_blocked_writes(monkeypatch):
    fetched=[]
    monkeypatch.setattr(inspection,'public_target',lambda url: None)
    def fetch(url):
        fetched.append(url)
        html=b'''<html><head><title>Bakery</title></head><body>
        <h1>Bakery</h1><div id="rendered"></div><script>
        document.querySelector('#rendered').textContent='JavaScript menu: Cakes';
        fetch('/write',{method:'POST',body:'no'}).catch(()=>{});
        </script><a href="/contact">Contact</a><details><summary>Hours</summary>Open daily</details>
        <form><input name="email" type="email" required><button>Send</button></form>
        </body></html>'''
        return url,'text/html',html
    monkeypatch.setattr(inspection,'fetch_public',fetch)
    result,media=asyncio.run(browser.inspect('https://bakery.test/','website-1'))
    assert result['inspection']=='Rendered Chromium desktop/mobile review',result
    assert len(result['pages'])==3
    assert fetched.count('https://bakery.test/')==2  # preflight plus desktop; mobile must reuse the page
    assert 'JavaScript menu: Cakes' in result['pages'][0]['text']
    assert result['pages'][1]['viewport_width']==390
    assert result['pages'][0]['forms'][0]['validWithoutInput'] is False
    assert result['actions'] and result['blocked_resources']
    assert all('/write' not in url for url in fetched)
    assert len([part for part in media if 'inlineData' in part])==6
    assert all(part['inlineData']['data'] for part in media if 'inlineData' in part)


def test_browser_private_target_fails_before_launch(monkeypatch):
    def deny(url): raise HTTPException(422,'Private target')
    monkeypatch.setattr(inspection,'public_target',deny)
    result,media=asyncio.run(browser.inspect('http://127.0.0.1/','website-1'))
    assert result['inspection']=='Browser inspection unavailable'
    assert not media


def test_rendered_dispatch_is_explicit(monkeypatch):
    calls=[]
    async def render(url,sid):
        calls.append((url,sid));return {'inspection':'Rendered'},[]
    monkeypatch.setattr(browser,'inspect',render)
    sources,media=asyncio.run(inspection.inspect([],['https://example.com'],rendered=True))
    assert sources['website-1']['inspection']=='Rendered'
    assert calls==[('https://example.com','website-1')]


def test_linked_page_failure_keeps_desktop_mobile_evidence(monkeypatch):
    monkeypatch.setattr(inspection,'public_target',lambda url:None)
    def fetch(url):
        if url.endswith('/contact'): raise HTTPException(422,'Unavailable')
        return url,'text/html',b'<html><body><h1>Bakery</h1><a href="/contact">Contact</a></body></html>'
    monkeypatch.setattr(inspection,'fetch_public',fetch)
    result,media=asyncio.run(browser.inspect('https://bakery.test/','website-1'))
    assert result['inspection']=='Rendered Chromium desktop/mobile review'
    assert len(result['pages'])==2
    assert sum('inlineData' in p for p in media)==4
    assert any('Linked page could not be inspected' in note for note in result['blocked_resources'])
