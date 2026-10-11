"""A tiny in-process SMTP server for tests. Never relays anything anywhere."""

import base64
import socket
import socketserver
import ssl
import threading


class _Handler(socketserver.StreamRequestHandler):
    timeout = 10

    def setup(self):
        if self.server.tls_context is not None:
            self.request = self.server.tls_context.wrap_socket(self.request, server_side=True)
        super().setup()

    def reply(self, line):
        self.wfile.write((line + "\r\n").encode())
        self.wfile.flush()

    def handle(self):
        srv = self.server
        if srv.silent:
            # Accept the connection and say nothing, to provoke a client timeout.
            threading.Event().wait(srv.silent)
            return
        self.reply("220 fake.smtp ready")
        authed = not srv.require_auth
        mail_from, rcpts = None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            cmd = line.split(" ", 1)[0].upper()
            arg = line[len(cmd):].strip()
            if cmd in ("EHLO", "HELO"):
                self.wfile.write(b"250-fake.smtp\r\n250-AUTH PLAIN\r\n250 8BITMIME\r\n")
                self.wfile.flush()
            elif cmd == "AUTH":
                parts = arg.split(" ", 1)
                try:
                    _, user, password = base64.b64decode(parts[1]).decode().split("\0")
                except Exception:  # noqa: BLE001
                    self.reply("501 bad auth")
                    continue
                if (user, password) == (srv.username, srv.password):
                    authed = True
                    self.reply("235 ok")
                else:
                    self.reply("535 5.7.8 authentication failed")
            elif cmd == "MAIL":
                if not authed:
                    self.reply("530 auth required")
                    continue
                mail_from, rcpts = arg, []
                self.reply("250 ok")
            elif cmd == "RCPT":
                address = arg.split(":", 1)[-1].strip().strip("<>").lower()
                if address in srv.reject_rcpt:
                    self.reply("550 5.1.1 no such user")
                else:
                    rcpts.append(address)
                    self.reply("250 ok")
            elif cmd == "DATA":
                if not rcpts:
                    self.reply("503 no recipients")
                    continue
                self.reply("354 go ahead")
                lines = []
                while True:
                    data_line = self.rfile.readline().decode(errors="replace")
                    if data_line in (".\r\n", ".\n", ""):
                        break
                    lines.append(data_line[1:] if data_line.startswith("..") else data_line)
                srv.messages.append({"from": mail_from, "to": list(rcpts), "data": "".join(lines)})
                self.reply("250 queued")
            elif cmd == "RSET":
                mail_from, rcpts = None, []
                self.reply("250 ok")
            elif cmd == "NOOP":
                self.reply("250 ok")
            elif cmd == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("502 not implemented")


class FakeSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, username="obs", password="secret", require_auth=True, tls_context=None, silent=0):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.username, self.password = username, password
        self.require_auth = require_auth
        self.tls_context = tls_context
        self.silent = silent
        self.messages = []
        self.reject_rcpt = set()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    def handle_error(self, request, client_address):
        pass  # failed TLS handshakes are expected in the certificate test

    @property
    def port(self):
        return self.server_address[1]

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def self_signed_context(tmpdir):
    """A TLS server context with a self-signed certificate (fails verification)."""
    import subprocess
    cert, key = f"{tmpdir}/cert.pem", f"{tmpdir}/key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                    "-out", cert, "-days", "1", "-subj", "/CN=127.0.0.1"],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context
