"""Isolated rendered evidence; all network goes through the pinned public fetcher."""
import asyncio
import base64
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from . import assistant_inspection as inspection


async def review(url, source_id):
    # No browser socket is allowed to resolve or contact a supplied host directly.
    await asyncio.to_thread(inspection.public_target, url)
    final_url, mime, raw = await asyncio.to_thread(inspection.fetch_public, url)
    if mime not in ('text/html', 'application/xhtml+xml'):
        name = urlsplit(final_url).path.split('/')[-1] or 'download'
        metadata, parts = inspection.document(name, raw, source_id)
        return {'url':final_url, **metadata}, parts
    url = final_url
    pages, media, blocked, actions = [], [], [], []
    requests = 0
    resource_cache = {}
    total_bytes = 0
    gate = asyncio.Semaphore(6)
    async with async_playwright() as driver:
        browser = await driver.chromium.launch(args=['--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        try:
            context = await browser.new_context(service_workers='block', accept_downloads=False,
                                                viewport={'width':1365,'height':900})
            await context.route_web_socket('**/*', lambda socket: socket.close())
            async def route_request(route):
                nonlocal requests, total_bytes
                request = route.request
                if request.method == 'GET' and request.url in resource_cache:
                    mime, raw = resource_cache[request.url]
                    await route.fulfill(status=200, content_type=mime, body=raw); return
                requests += 1
                if request.method != 'GET' or requests > 90 or total_bytes > 20 * inspection.MIB:
                    blocked.append('Request blocked: method or inspection budget')
                    await route.abort(); return
                try:
                    async with gate:
                        final, mime, raw = await asyncio.wait_for(asyncio.to_thread(inspection.fetch_public, request.url), 12)
                    if total_bytes + len(raw) > 20 * inspection.MIB:
                        raise ValueError('Total resource budget exceeded')
                    total_bytes += len(raw)
                    if final != request.url:
                        # Let Chromium resolve relative resources using the final public URL.
                        await route.fulfill(status=302, headers={'location':final}); return
                    resource_cache[request.url] = (mime, raw)
                    await route.fulfill(status=200, content_type=mime, body=raw)
                except Exception:
                    blocked.append('Resource unavailable or blocked: ' + request.url[:200])
                    await route.abort()
            await context.route('**/*', route_request)
            page = await context.new_page()
            page.set_default_timeout(2500)
            page.on('dialog', lambda dialog: dialog.dismiss())
            async def capture(target, label, width, navigate=True):
                await page.set_viewport_size({'width':width,'height':900})
                if navigate:
                    response = await page.goto(target, wait_until='domcontentloaded', timeout=15000)
                    if not response or response.status >= 400:
                        raise ValueError('Page navigation did not succeed')
                await page.wait_for_timeout(900)
                # Expand native disclosures without submitting data or clicking arbitrary actions.
                count = await page.locator('details:not([open])').count()
                await page.locator('details').evaluate_all('(nodes)=>nodes.forEach(n=>n.open=true)')
                if count: actions.append(f'Expanded {count} native disclosure sections on {target}')
                evidence = await page.evaluate("""() => ({title:document.title,text:document.body.innerText.slice(0,18000),
                    links:Array.from(document.querySelectorAll('a[href]')).slice(0,60).map(a=>({text:a.innerText,url:a.href})),
                    forms:Array.from(document.forms).slice(0,10).map(f=>({fields:Array.from(f.elements).map(e=>({type:e.type,name:e.name,required:e.required})),validWithoutInput:f.checkValidity()})),
                    horizontalOverflow:document.documentElement.scrollWidth>innerWidth})""")
                shots = []
                for position in ('top','bottom'):
                    await page.evaluate('position => scrollTo(0,position === "top" ? 0 : document.body.scrollHeight)',position)
                    raw = await page.screenshot(type='jpeg', quality=65, timeout=5000)
                    shot_id=f'{source_id}-{label}-{position}'
                    shots.append(shot_id)
                    media.extend([{'text':f'Untrusted website screenshot {shot_id}: {page.url}, viewport {width}x900, {position}.'},
                                  {'inlineData':{'mimeType':'image/jpeg','data':base64.b64encode(raw).decode()}}])
                pages.append(dict(url=page.url, viewport_width=width, screenshots=shots, **evidence))
                return evidence['links']
            links = await capture(url,'desktop',1365)
            try: await capture(url,'mobile',390,navigate=False)
            except Exception: blocked.append('Mobile view could not be inspected; desktop evidence is retained.')
            origin=urlsplit(page.url)
            candidates=[]
            for item in links:
                target=item['url']; parsed=urlsplit(target)
                if (parsed.scheme,parsed.netloc)==(origin.scheme,origin.netloc) and parsed.path!=origin.path and not parsed.query and not parsed.fragment:
                    if any(word in (parsed.path+' '+item['text']).lower() for word in ('about','contact','menu','service','portfolio','pricing')) and target not in candidates:
                        candidates.append(target)
            for index,target in enumerate(candidates[:2]):
                try: await capture(target,f'linked-{index+1}',1365)
                except Exception: blocked.append('Linked page could not be inspected: '+target)
            return {'url':url,'inspection':'Rendered Chromium desktop/mobile review', 'pages':pages,
                    'actions':actions,'blocked_resources':list(dict.fromkeys(blocked))[:12],
                    'limitations':'Up to three public pages; top/bottom screenshots, DOM and native required-field checks only. No form submission, login, purchase, backend delivery, arbitrary button clicks or exhaustive functional testing. Blocked resources can affect rendering.'}, media
        finally:
            await browser.close()


async def inspect(url, source_id):
    try:
        return await asyncio.wait_for(review(url, source_id),timeout=55)
    except Exception as error:
        return {'url':url,'inspection':'Browser inspection unavailable',
                'error':'Rendered inspection could not finish; no browser-based verification is claimed.',
                'limitations':type(error).__name__}, []
