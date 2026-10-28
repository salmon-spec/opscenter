"""Editable runbook: submit public parameters; credentials stay in OpsCenter."""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    base = config['opscenter_url'].rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'web', 'localhost', '127.0.0.1'}):
        raise ValueError('OpsCenter requires HTTPS or the internal web service')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid OpsCenter URL')
    parameters = json.loads(sys.stdin.read(16001))
    login = {name: Path(config[name + '_file']).read_text().strip() for name in ('username', 'password')}
    opener = build_opener(ProxyHandler({}))
    def call(path, body, token=''):
        request = Request(base + path, data=json.dumps(body).encode(), headers={
            'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            with opener.open(request, timeout=420) as response:
                return json.loads(response.read(128000))
        except HTTPError as exc:
            message = json.loads(exc.read(4000)).get('detail', '')
            raise RuntimeError(f'HTTP {exc.code}: {message}') from None
    token = call('/api/v2/auth/login', login)['access_token']
    result = call('/api/v2/wireguard/install', parameters, token)
    print(('WG_PLANNED ' if parameters.get('dry_run', True) else 'WG_CONNECTED ') + json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('WG_FAILED: ' + (str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__))
