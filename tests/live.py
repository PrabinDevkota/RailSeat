"""Run a node on a free local port so the puller can use real HTTP."""

from __future__ import annotations

import socket
import threading
import time

import httpx
import uvicorn


class LiveServer:
    def __init__(self, app):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                response = httpx.get(self.url + "/health", timeout=0.5)
                if response.status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.05)
        raise RuntimeError("server did not start")

    def __exit__(self, *_args):
        self.server.should_exit = True
        self.thread.join(timeout=5)
