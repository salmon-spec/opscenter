"""Image generation over the already configured provider; no SDK or CLI install."""
import base64
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpx

MAX_IMAGE = 16 * 1024 * 1024


def raster_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png', '.png'
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg', '.jpg'
    if data[:6] in {b'GIF87a',b'GIF89a'}: return 'image/gif', '.gif'
    if data[:4]==b'RIFF' and data[8:12]==b'WEBP': return 'image/webp', '.webp'
    raise ValueError('图片服务未返回支持的图片文件')


def read_bounded(response, limit):
    data=bytearray()
    for chunk in response.iter_bytes():
        data.extend(chunk)
        if len(data)>limit: raise ValueError('图片服务返回内容超过大小限制')
    return bytes(data)


def generate(base_url, key, model, prompt, ratio, client=None):
    own=client is None
    client=client or httpx.Client(trust_env=False,timeout=httpx.Timeout(20,read=360),follow_redirects=False)
    try:
        with client.stream('POST',base_url.rstrip('/')+'/images/generations',
            headers={'Authorization':'Bearer '+key},
            json={'model':model,'prompt':prompt,'size':'1K','ratio':ratio,'n':1,'return_base64':True}) as r:
            if r.status_code!=200:
                retry=r.headers.get('retry-after')
                raw=read_bounded(r,64*1024).decode(errors='replace').replace(key,'[REDACTED]')
                try: message=json.loads(raw).get('error',{}).get('message',raw)
                except (ValueError,AttributeError): message=raw
                return {'error':str(message)[:1200],'status':r.status_code,'retry_after':retry}
            body=json.loads(read_bounded(r,24*1024*1024))
        item=body.get('data',[{}])[0]
        encoded=item.get('b64_json')
        if encoded:
            if encoded.startswith('data:'): encoded=encoded.split(',',1)[1]
            data=base64.b64decode(encoded,validate=True)
        else:
            url=item.get('url','');parts=urlsplit(url)
            if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.port not in {None,443}:
                raise ValueError('图片服务未返回有效的图片内容')
            addresses=socket.getaddrinfo(parts.hostname,443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('图片下载地址无效')
            with client.stream('GET',url) as r:
                r.raise_for_status();data=read_bounded(r,MAX_IMAGE)
        if not data or len(data)>MAX_IMAGE: raise ValueError('图片为空或超过16 MiB')
        mime,suffix=raster_type(data)
        return {'data':data,'mime':mime,'suffix':suffix}
    finally:
        if own: client.close()
