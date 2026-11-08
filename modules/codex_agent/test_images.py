import asyncio
import base64
import httpx
import pytest
from images import generate,raster_type,read_bounded


def request_image(handler,key='private'):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await generate('https://api.agnes-ai.cn/v1',key,'agnes-image-2.5-flash','A dog','1:1',client)
    return asyncio.run(run())


def test_same_provider_generation_saves_real_binary_without_sdk():
    png=b'\x89PNG\r\n\x1a\n'+b'image';seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png).decode()}]})
    result=request_image(respond)
    assert result['data']==png and result['mime']=='image/png'
    assert str(seen[0].url)=='https://api.agnes-ai.cn/v1/images/generations'
    assert b'"return_base64":true' in seen[0].content


def test_image_errors_are_bounded_and_keys_not_returned():
    def respond(request):return httpx.Response(429,headers={'Retry-After':'20'},json={'error':{'message':'limit private-key'}})
    r=request_image(respond,key='private-key')
    assert r['status']==429 and r['retry_after']=='20' and 'private-key' not in r['error']
    with pytest.raises(ValueError):raster_type(b'<html>error</html>')


def test_download_uses_validated_ip_with_original_tls_name(monkeypatch):
    import socket
    seen=[]
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))])
    def respond(request):
        seen.append(request)
        if request.method=='POST':return httpx.Response(200,json={'data':[{'url':'https://cdn.example/image.png'}]})
        return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nimage')
    request_image(respond)
    request=seen[-1]
    assert request.url.host=='93.184.216.34' and request.headers['host']=='cdn.example'
    assert request.extensions['sni_hostname']=='cdn.example' and 'authorization' not in request.headers


def test_private_download_address_is_rejected(monkeypatch):
    import socket
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'data':[{'url':'https://cdn.example/image.png'}]})
    with pytest.raises(ValueError):request_image(respond)
    assert len(seen)==1


def test_large_error_keeps_status_and_decoded_keys_are_redacted():
    def respond(request):return httpx.Response(429,headers={'Retry-After':'40'},content=b'{"error":{"message":"private\\u002dkey"}}')
    result=request_image(respond,key='private-key')
    assert result['status']==429 and 'private-key' not in result['error']
    result=request_image(lambda r:httpx.Response(429,content=b'x'*(128*1024)))
    assert result['status']==429 and len(result['error'])<=1200


def test_bounded_reader_stops_before_consuming_full_stream():
    class Stream(httpx.AsyncByteStream):
        yielded=0
        async def __aiter__(self):
            for _ in range(8):self.yielded+=1;yield b'x'*(64*1024)
    stream=Stream()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=stream))) as client:
            async with client.stream('GET','https://provider.example/image') as response:
                with pytest.raises(ValueError):await read_bounded(response,64*1024)
    asyncio.run(run())
    assert stream.yielded==2


def test_cancelling_generation_closes_stream_and_owned_client(monkeypatch):
    import images
    async def run():
        started=asyncio.Event();closed=asyncio.Event();never=asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set();await never.wait();yield b'not reached'
            async def aclose(self):closed.set()
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Stream())))
        monkeypatch.setattr(images.httpx,'AsyncClient',lambda **kwargs:client)
        task=asyncio.create_task(generate('https://api.agnes-ai.cn/v1','private','model','A dog','1:1'))
        await asyncio.wait_for(started.wait(),1);task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        assert client.is_closed and closed.is_set()
    asyncio.run(run())
