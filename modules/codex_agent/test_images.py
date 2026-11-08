import base64
import httpx
import pytest
from images import generate,raster_type


def test_same_provider_generation_saves_real_binary_without_sdk():
    png=b'\x89PNG\r\n\x1a\n'+b'image'
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png).decode()}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result=generate('https://api.agnes-ai.cn/v1','private','agnes-image-2.5-flash','A dog','1:1',client)
    assert result['data']==png and result['mime']=='image/png'
    assert str(seen[0].url)=='https://api.agnes-ai.cn/v1/images/generations'
    assert b'"return_base64":true' in seen[0].content


def test_image_errors_are_bounded_and_keys_not_returned():
    def respond(request):return httpx.Response(429,headers={'Retry-After':'20'},json={'error':{'message':'limit private-key'}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        r=generate('https://api.agnes-ai.cn/v1','private-key','agnes-image-2.5-flash','A dog','1:1',client)
    assert r['status']==429 and r['retry_after']=='20' and 'private-key' not in r['error']
    with pytest.raises(ValueError):raster_type(b'<html>error</html>')
