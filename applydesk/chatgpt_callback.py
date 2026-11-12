"""A short-lived loopback callback accepts only the pending OAuth transaction."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class CallbackHandler(BaseHTTPRequestHandler):
    """Never log OAuth codes, state, tokens, or callback query strings."""

    def log_message(self, *_):
        """Suppress the standard request logger because callback URLs contain credentials."""

    def do_GET(self):
        """Require the exact callback host/path; consume valid state only once."""
        parsed = urlsplit(self.path)
        owner = self.server.owner
        host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != host or parsed.path != "/auth/callback":
            self.send_error(404)
            return
        try:
            query = parse_qs(parsed.query, max_num_fields=12)
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("Invalid callback.")
            owner.complete({key: values[0] for key, values in query.items()})
            message, status = "Connected. Return to ApplyDesk; you can close this tab.", 200
        except Exception:
            message, status = "Sign-in was not completed. Return to ApplyDesk for details.", 400
        body = ("<!doctype html><title>ApplyDesk</title><p>" + message + "</p>").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)


def start_callback(owner):
    """Bind before opening the system browser; a fresh ephemeral port is supported."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    server.daemon_threads = True
    server.owner = owner
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
