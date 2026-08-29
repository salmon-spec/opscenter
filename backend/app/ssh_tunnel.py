"""Short-lived local TCP forwarding over an existing managed SSH host."""
from __future__ import annotations

import select
import socketserver
import threading
from contextlib import contextmanager

from fastapi import HTTPException

from app.ssh_manager import get_ssh_client


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextmanager
def ssh_forward(server, target_host: str, target_port: int):
    client = get_ssh_client(server)
    if not client:
        raise HTTPException(status_code=400, detail="主机未配置可用的 SSH 凭证")
    transport = client.get_transport()
    if not transport or not transport.is_active():
        client.close()
        raise HTTPException(status_code=502, detail="SSH 连接不可用")

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            channel = transport.open_channel(
                "direct-tcpip",
                (target_host, int(target_port)),
                self.request.getpeername(),
            )
            if channel is None:
                return
            try:
                while True:
                    readable, _, _ = select.select([self.request, channel], [], [], 10)
                    if self.request in readable:
                        data = self.request.recv(65536)
                        if not data:
                            break
                        channel.sendall(data)
                    if channel in readable:
                        data = channel.recv(65536)
                        if not data:
                            break
                        self.request.sendall(data)
            finally:
                channel.close()

    forwarder = _ForwardServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=forwarder.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", forwarder.server_address[1]
    finally:
        forwarder.shutdown()
        forwarder.server_close()
        client.close()
