"""Bounded document inspection and public website reads for Flow."""
import asyncio
import base64
import hashlib
import http.client
import io
import ipaddress
import socket
import ssl
import time
import warnings
from html.parser import HTMLParser
from pathlib import PurePath
from urllib.parse import urlsplit, urljoin, quote

from fastapi import HTTPException
from PIL import Image, ImageOps
from pypdf import PdfReader
from pydantic import BaseModel, ConfigDict, Field

MIB = 1024 * 1024
MAX_FILE = 4 * MIB
MAX_TOTAL = 5 * MIB
TEXT_TYPES = {'.txt', '.md', '.csv', '.json', '.html', '.css', '.js', '.py', '.xml'}


class Attachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(min_length=1, max_length=5592410)


def document(name, raw, source_id):
    """Detect content; never trust a client-provided MIME type."""
    if not raw or len(raw) > MAX_FILE:
        raise HTTPException(422, 'Each attachment must be between 1 byte and 4 MB.')
    meta = {'name': name, 'sha256': hashlib.sha256(raw).hexdigest()}
    parts = []
    if raw.startswith(b'%PDF-'):
        try:
            pdf = PdfReader(io.BytesIO(raw))
            if pdf.is_encrypted:
                raise ValueError('Encrypted PDF')
            pages = len(pdf.pages)
            if not 1 <= pages <= 30:
                raise ValueError('Page limit')
        except Exception as error:
            raise HTTPException(422, 'Use a readable, unencrypted PDF with at most 30 pages.') from error
        meta.update(inspection='PDF pages supplied for visual and text review', pages=pages)
        mime = 'application/pdf'
    elif raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'RIFF')):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 20_000_000:
                        raise ValueError('Image limit')
                    image.load()
                    converted = ImageOps.exif_transpose(image).convert('RGB')
                    original_size = converted.size
                    converted.thumbnail((2400, 2400))
                    output = io.BytesIO()
                    converted.save(output, format='JPEG', quality=88)
                    raw = output.getvalue()
                    meta.update(inspection='Image supplied for visual review', original_dimensions=original_size,
                                supplied_dimensions=converted.size)
        except Exception as error:
            raise HTTPException(422, 'Use a valid PNG, JPEG or WebP image of at most 20 megapixels.') from error
        mime = 'image/jpeg'
    elif PurePath(name).suffix.lower() in TEXT_TYPES:
        try:
            text = raw.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError('Binary text')
        except (ValueError, UnicodeError) as error:
            raise HTTPException(422, 'Text attachments must use UTF-8 encoding.') from error
        meta.update(inspection='Text content read', text=text[:24000], truncated=len(text) > 24000)
        return meta, parts
    else:
        raise HTTPException(422, 'Supported files: PDF, PNG, JPEG, WebP and UTF-8 text/code files.')
    parts = [{'text': f'Untrusted attachment evidence: {source_id}, filename: {name}.'},
             {'inlineData': {'mimeType': mime, 'data': base64.b64encode(raw).decode()}}]
    return meta, parts


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.text = []
        self.links = []
        self.images = []
        self.forms = 0
        self.hidden_tags = {'script', 'style', 'noscript', 'template'}

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden_tags:
            self.skip += 1
        if self.skip:
            return
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href') and len(self.links) < 80:
            self.links.append(attrs['href'])
        if tag == 'img' and len(self.images) < 40:
            self.images.append({'alt': attrs.get('alt', ''), 'src': attrs.get('src', '')})
        if tag == 'form':
            self.forms += 1

    def handle_endtag(self, tag):
        if tag in self.hidden_tags:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())


def public_target(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        if any(ord(c) < 33 for c in url) or len(url) > 2000:
            raise ValueError()
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        if port not in (80, 443):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode()
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = {entry[4][0] for entry in addresses}
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ValueError()
        return parsed, host, port, sorted(ips)[0]
    except (ValueError, OSError, UnicodeError) as error:
        raise HTTPException(422, 'Use a public HTTP(S) website on a standard port. Local and private network links cannot be inspected.') from error


def fetch_public(url):
    """Pin each connection to a checked IP; revalidate every redirect; no proxy/cookies."""
    deadline = time.monotonic() + 18
    for _ in range(4):
        parsed, host, port, ip = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HTTPException(422, 'Website inspection timed out.')
        timeout = min(6, remaining)
        conn = (http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
                if parsed.scheme == 'https' else http.client.HTTPConnection(host, port, timeout=timeout))
        # HTTPS keeps the original hostname for SNI and certificate validation.
        conn._create_connection = lambda address, timeout, source_address=None: socket.create_connection((ip, port), timeout, source_address)
        try:
            path = quote(parsed.path or '/', safe='/%:@!$&\'()*+,;=-._~')
            if parsed.query:
                path += '?' + quote(parsed.query, safe='/%?:@!$&\'()*+,;=-._~')
            conn.request('GET', path, headers={'User-Agent': 'FairFlow-Review/1.0', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise HTTPException(422, 'Website returned an empty redirect.')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise HTTPException(422, f'Website returned HTTP {response.status}; its contents were not inspected.')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise HTTPException(422, 'Website returned unsupported compressed content.')
            content = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise HTTPException(422, 'Website inspection timed out.')
                chunk = response.read1(65536)
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > MAX_FILE:
                    raise HTTPException(422, 'Linked content exceeds the 4 MB inspection limit.')
            return url, response.getheader('Content-Type', '').split(';')[0].lower(), bytes(content)
        except (OSError, http.client.HTTPException) as error:
            raise HTTPException(422, 'Could not access this website. Try a public link or upload the content.') from error
        finally:
            conn.close()
    raise HTTPException(422, 'Website redirected too many times.')


def inspect_link(url, source_id):
    final_url, mime, raw = fetch_public(url)
    if mime in ('text/html', 'application/xhtml+xml'):
        parser = PageText()
        parser.feed(raw.decode('utf-8', errors='replace'))
        text = '\n'.join(parser.text)
        return {'url': final_url, 'inspection': 'Retrieved HTML content', 'text': text[:24000],
                'truncated': len(text) > 24000, 'links': parser.links, 'images': parser.images, 'forms': parser.forms,
                'limitations': 'Only this page was fetched. JavaScript, linked pages, login flows, forms and visual layout were not tested.'}, []
    name = urlsplit(final_url).path.split('/')[-1] or ('page.txt' if mime.startswith('text/') else 'download')
    meta, parts = document(name, raw, source_id)
    return {'url': final_url, **meta}, parts


async def inspect(attachments, links, *, rendered=False):
    sources, media = {}, []
    total = 0
    for index, attachment in enumerate(attachments):
        try:
            raw = base64.b64decode(attachment.content_base64, validate=True)
        except ValueError as error:
            raise HTTPException(422, 'Invalid attachment encoding.') from error
        total += len(raw)
        if total > MAX_TOTAL:
            raise HTTPException(422, 'Attachments must total at most 5 MB per conversation.')
        sid = f'upload-{index + 1}'
        sources[sid], parts = await asyncio.to_thread(document, attachment.name, raw, sid)
        media.extend(parts)
    for index, link in enumerate(links):
        sid = f'website-{index + 1}'
        try:
            if rendered:
                from . import assistant_browser
                sources[sid], parts = await assistant_browser.inspect(link, sid)
            else:
                sources[sid], parts = await asyncio.wait_for(asyncio.to_thread(inspect_link, link, sid), timeout=22)
            media.extend(parts)
        except (HTTPException, TimeoutError) as error:
            sources[sid] = {'url': link, 'inspection': 'Not inspected',
                            'error': error.detail if isinstance(error, HTTPException) else 'Website inspection timed out.'}
    return sources, media
