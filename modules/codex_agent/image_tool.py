"""Installed image tool for native Codex terminal calls; credentials stay local."""
import argparse
import json
import os
from pathlib import Path
import sys

import httpx


def main():
    parser=argparse.ArgumentParser(description='Generate a real image with the configured Agent API')
    parser.add_argument('--prompt',required=True)
    parser.add_argument('--ratio',default='1:1',choices=['1:1','3:4','4:3','16:9','9:16','2:3','3:2','21:9'])
    args=parser.parse_args()
    ident=os.getenv('CODEX_THREAD_ID','')
    call_id=os.getenv('CODEX_TOOL_CALL_ID','')
    if not ident or not call_id: raise ValueError('请从 Codex 对话中调用图片工具')
    key=Path(os.environ['CODEX_API_KEY_FILE']).read_text().strip()
    r=httpx.post('http://127.0.0.1:8080/provider/v1/images',
        headers={'Authorization':'Bearer '+key},
        json={'thread_id':ident,'call_id':call_id,'prompt':args.prompt,'ratio':args.ratio},
        timeout=httpx.Timeout(20,read=390),trust_env=False)
    body=r.json()
    if r.status_code!=200: raise ValueError(str(body.get('detail',body.get('error','图片生成失败'))).replace(key,'[REDACTED]'))
    print(json.dumps(body,ensure_ascii=False))
    print('![生成的图片]('+body['path']+')')


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('图片生成失败：'+str(exc),file=sys.stderr)
        sys.exit(1)
