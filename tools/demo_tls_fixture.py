"""Loopback-only TLS proxy for disposable demo acceptance tests."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import ssl
import subprocess
from threading import Thread
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


@contextmanager
def tls_proxy(backend, directory):
    executable = shutil.which("openssl")
    if executable is None:
        git_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        executable = str(git_openssl) if git_openssl.is_file() else None
    if executable is None:
        raise RuntimeError("The local TLS fixture requires OpenSSL")
    cert, key = Path(directory) / "localhost.crt", Path(directory) / "localhost.key"
    subprocess.run(
        [executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-keyout", str(key), "-out", str(cert), "-subj", "/CN=localhost",
         "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    key.chmod(0o600)
    opener = build_opener(NoRedirect())

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def forward(self):
            if self.headers.get("Host", "").lower() != self.server.authority:
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if not 0 <= length <= 1024 * 1024 or not self.path.startswith("/") or self.path.startswith("//"):
                self.send_error(413)
                return
            headers = {
                key: value for key, value in self.headers.items()
                if key.lower() not in {"host", "connection", "transfer-encoding", "accept-encoding"}
                and not key.lower().startswith(("x-forwarded-", "proxy-"))
            }
            headers.update({
                "Host": self.server.authority,
                "X-Forwarded-Host": self.server.authority,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": self.client_address[0],
            })
            body = self.rfile.read(length) if length else None
            request = Request(backend + self.path, data=body, headers=headers, method=self.command)
            try:
                source = opener.open(request, timeout=20)
            except HTTPError as error:
                source = error
            with source:
                payload = source.read()
                self.send_response(source.status)
                for name, value in source.headers.items():
                    if name.lower() not in {"content-length", "connection", "transfer-encoding", "server", "date"}:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (ConnectionError, ssl.SSLError):
                    # Navigation may abandon an in-flight static response.
                    pass

        do_GET = forward
        do_POST = forward
        do_HEAD = forward

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    server.authority = f"localhost:{server.server_port}"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield "https://" + server.authority
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
