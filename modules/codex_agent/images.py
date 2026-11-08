"""Image generation over the already configured provider; no SDK or CLI install."""
import base64
import asyncio
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


async def read_bounded(response, limit, *, truncate=False):
    # Request identity encoding and never inflate an unbounded compressed body.
    if response.headers.get('content-encoding','identity').lower() not in {'','identity'}:
        raise ValueError('图片服务返回了不支持的压缩响应')
    if response.is_stream_consumed:
        data=response.content
        if len(data)>limit and not truncate:raise ValueError('图片服务返回内容超过大小限制')
        return data[:limit]
    data=bytearray()
    async for chunk in response.aiter_raw(chunk_size=64*1024):
        remaining=limit-len(data)
        if len(chunk)>remaining:
            if not truncate:raise ValueError('图片服务返回内容超过大小限制')
            data.extend(chunk[:remaining]);break
        data.extend(chunk)
        if truncate and len(data)==limit:break
    return bytes(data)


async def generate(base_url, key, model, prompt, ratio, client=None):
    own=client is None
    client=client or httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(20,read=360),follow_redirects=False)
    try:
        async with client.stream('POST',base_url.rstrip('/')+'/images/generations',
            headers={'Authorization':'Bearer '+key,'Accept-Encoding':'identity'},
            json={'model':model,'prompt':prompt,'size':'1K','ratio':ratio,'n':1,'return_base64':True}) as r:
            if r.status_code!=200:
                retry=r.headers.get('retry-after')
                try:raw=(await read_bounded(r,64*1024,truncate=True)).decode(errors='replace').replace(key,'[REDACTED]')
                except ValueError:raw='图片服务返回 HTTP '+str(r.status_code)
                try: message=json.loads(raw).get('error',{}).get('message',raw)
                except (ValueError,AttributeError): message=raw
                return {'error':str(message).replace(key,'[REDACTED]')[:1200],'status':r.status_code,'retry_after':retry}
            body=json.loads(await read_bounded(r,24*1024*1024))
        if not isinstance(body,dict) or not isinstance(body.get('data'),list) or not body['data'] or not isinstance(body['data'][0],dict):
            raise ValueError('图片服务未返回有效的图片结果')
        item=body['data'][0]
        encoded=item.get('b64_json')
        if encoded:
            if not isinstance(encoded,str):raise ValueError('图片服务返回了无效的Base64结果')
            if encoded.startswith('data:'): encoded=encoded.split(',',1)[1]
            data=base64.b64decode(encoded,validate=True)
        else:
            url=item.get('url','')
            if not isinstance(url,str):raise ValueError('图片下载地址无效')
            parts=urlsplit(url)
            if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.port not in {None,443}:
                raise ValueError('图片服务未返回有效的图片内容')
            original=httpx.URL(url);host=original.raw_host.decode('ascii')
            addresses=await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,443,type=socket.SOCK_STREAM),10)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('图片下载地址无效')
            # Pin the validated address while retaining Host/TLS SNI. A second
            # DNS resolution must not turn an allowed URL into a private request.
            addresses.sort(key=lambda a:a[0]!=socket.AF_INET)
            pinned=original.copy_with(host=addresses[0][4][0])
            async with client.stream('GET',pinned,headers={'Host':host,'Accept-Encoding':'identity'},
                               extensions={'sni_hostname':host},follow_redirects=False) as r:
                r.raise_for_status();data=await read_bounded(r,MAX_IMAGE)
        if not data or len(data)>MAX_IMAGE: raise ValueError('图片为空或超过16 MiB')
        mime,suffix=raster_type(data)
        return {'data':data,'mime':mime,'suffix':suffix}
    finally:
        if own: await client.aclose()
