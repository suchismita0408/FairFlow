import asyncio
import base64
import io
import socket

import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfWriter

from app import assistant_inspection as inspect


def test_pdf_and_image_supply_real_media():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO(); writer.write(output)
    metadata, parts = inspect.document('work.pdf', output.getvalue(), 'upload-1')
    assert metadata['pages'] == 1
    assert parts[1]['inlineData']['mimeType'] == 'application/pdf'
    output = io.BytesIO(); Image.new('RGB', (40, 20), 'red').save(output, 'PNG')
    metadata, parts = inspect.document('work.png', output.getvalue(), 'upload-2')
    assert metadata['supplied_dimensions'] == (40, 20)
    assert parts[1]['inlineData']['mimeType'] == 'image/jpeg'


def test_invalid_media_and_page_limits():
    with pytest.raises(HTTPException): inspect.document('fake.pdf', b'not a PDF', 'x')
    with pytest.raises(HTTPException): inspect.document('fake.png', b'not an image', 'x')
    writer = PdfWriter()
    for _ in range(31): writer.add_blank_page(width=10, height=10)
    output = io.BytesIO(); writer.write(output)
    with pytest.raises(HTTPException): inspect.document('large.pdf', output.getvalue(), 'x')
    with pytest.raises(HTTPException):
        asyncio.run(inspect.inspect([inspect.Attachment(name='a.txt', content_base64='invalid')], []))


@pytest.mark.parametrize('address', ['127.0.0.1','10.0.0.1','169.254.169.254','::1','192.168.1.2','0.0.0.0','100.64.0.1'])
def test_private_dns_is_blocked(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address,80))])
    with pytest.raises(HTTPException): inspect.public_target('https://example.com')


def test_redirect_to_private_network_is_blocked(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host,*a,**kw: [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1' if host=='private.test' else '93.184.216.34',443))])
    class Connection:
        def __init__(self,*a,**kw): pass
        def request(self,*a,**kw): pass
        def getresponse(self): return self
        status=302
        def getheader(self,name): return 'https://private.test'
        def close(self): pass
    monkeypatch.setattr(inspect.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(HTTPException): inspect.fetch_public('https://example.com')


def test_connection_is_pinned_to_checked_ip(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a,**kw: [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    seen=[]
    monkeypatch.setattr(socket,'create_connection',lambda addr,*a: seen.append(addr))
    class Connection:
        def __init__(self,host,*a,**kw): assert host=='example.com'
        def request(self,*a,**kw): self._create_connection(('attacker-rebind.test',443),6)
        def getresponse(self): return self
        status=200
        def getheader(self,name,default=None): return 'text/html' if name=='Content-Type' else default
        def read1(self,size): return b''
        def close(self): pass
    monkeypatch.setattr(inspect.http.client, 'HTTPSConnection', Connection)
    inspect.fetch_public('https://example.com')
    assert seen==[('93.184.216.34',443)]


def test_website_extracts_content_with_explicit_limits(monkeypatch):
    monkeypatch.setattr(inspect,'fetch_public',lambda url:(url,'text/html',b'<h1>Portfolio</h1><script>do not trust this</script><a href="/about">About</a><form></form>'))
    meta,parts=inspect.inspect_link('https://example.com','website-1')
    assert 'Portfolio' in meta['text'] and 'do not trust' not in meta['text']
    assert meta['links']==['/about'] and meta['forms']==1
    assert 'not tested' in meta['limitations'] and not parts


def test_failed_fetch_is_evidence_of_failure_only(monkeypatch):
    def fail(*args): raise HTTPException(422, 'Website unavailable')
    monkeypatch.setattr(inspect,'inspect_link',fail)
    sources,media=asyncio.run(inspect.inspect([],['https://example.com']))
    assert sources['website-1']['inspection']=='Not inspected'
    assert not media
