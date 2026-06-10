"""Local HTTPS server for PyFirma — handles the AppAfirmaJSSocket protocol.

Some websites use the JSSocket mode instead of WebSocket. In this mode,
the browser sends HTTP POST requests to https://127.0.0.1:PORT/afirma
instead of opening a WebSocket connection.

Protocol:
  1. Echo:   POST /afirma  body="echo=-idsession=SESSIONID@EOF"
             → 200 OK with base64("OK") as body
  2. Fragments: POST /afirma  body="cmd=BASE64_URL&idsession=SESSIONID@EOF"
             → 200 OK with base64("MORE_DATA_NEED") or base64("OK")
  3. Execute: POST /afirma  body="firm=idsession=SESSIONID@EOF"
             → 200 OK with base64(result)
  4. CORS:   OPTIONS /afirma → 204 with CORS headers

All responses include CORS headers: Access-Control-Allow-Origin: *
"""

import base64
import json
import os
import ssl
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


# ----- Main HTTP request handler -----


class AfirmaHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the /afirma endpoint (JSSocket protocol)."""

    # Session state (shared across requests)
    url_fragments = ""
    session_id = None
    gui_callback = None
    op_handler = None  # sync operation handler: func(url) -> bytes|str|None

    # CORS headers
    CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    def _send_cors_response(self, status=200, body=None):
        """Send an HTTP response with CORS headers."""
        if body is None:
            body = b""
        elif isinstance(body, str):
            body = body.encode("utf-8")

        self.send_response(status)
        for key, value in self.CORS_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _log(self, *args):
        # Accept both _log("msg") and _log("level", "msg")
        if len(args) == 1:
            level, msg = "info", args[0]
        elif len(args) == 2:
            level, msg = args
        else:
            return
        # Log to GUI callback (tkinter thread)
        if AfirmaHTTPHandler.gui_callback:
            AfirmaHTTPHandler.gui_callback(level, msg)
        # Also log to file for debugging
        try:
            with open("/tmp/pyfirma_http.log", "a") as f:
                f.write(f"[HTTP] [{level.upper()}] {msg}\n")
        except Exception:
            pass

    def do_OPTIONS(self):
        """CORS preflight."""
        self._send_cors_response(204)

    def do_POST(self):
        """Handle POST /afirma."""
        if self.path != "/afirma":
            self._send_cors_response(404, b"Not Found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("ascii", errors="replace")

        # ---- Echo ----
        if body.startswith("echo="):
            self._log("HTTP echo received")
            self._send_cors_response(200, base64.b64encode(b"OK"))
            return

        # ---- URL fragment (cmd=) ----
        if body.startswith("cmd="):
            eof_pos = body.find("@EOF")
            if eof_pos > 0:
                cmd_data = body[4:eof_pos]
            else:
                cmd_data = body[4:].strip()

            # Decode URL-safe base64 fragment
            try:
                fragment = base64.urlsafe_b64decode(
                    cmd_data + "=" * (-len(cmd_data) % 4)
                ).decode("ascii", errors="replace")
            except Exception:
                fragment = cmd_data

            AfirmaHTTPHandler.url_fragments += fragment
            self._log(
                f"HTTP cmd fragment ({len(fragment)} chars), "
                f"total accumulated: {len(AfirmaHTTPHandler.url_fragments)}"
            )

            # Respond with the number of result parts.
            # The browser expects a base64-encoded number (e.g., "1").
            # It will then request each part via send=@PART@TOTAL.
            self._send_cors_response(200, base64.b64encode(b"1"))
            return

        # ---- Execute (firm=) — for multi-fragment URL sends ----
        if body.startswith("firm="):
            self._log(
                f"HTTP firm — processing accumulated URL "
                f"({len(AfirmaHTTPHandler.url_fragments)} chars)"
            )
            # Same as cmd=: respond with number of result parts
            self._send_cors_response(200, base64.b64encode(b"1"))
            return

        # ---- Result fragment request (send=@PART@TOTALidsession=...) ----
        if body.startswith("send="):
            # Extract part and total
            # Format: send=@PART@TOTALidsession=SESSIONID@EOF
            send_data = body[5:]  # after "send="
            at1 = send_data.find("@")
            at2 = send_data.find("@", at1 + 1)
            eof_pos = send_data.find("@EOF")

            try:
                part = int(send_data[:at1])
                total = int(send_data[at1 + 1:at2])
            except (ValueError, IndexError):
                part = 1
                total = 1

            self._log(f"HTTP send part {part}/{total}")

            # Process the accumulated URL and return the result
            url = AfirmaHTTPHandler.url_fragments.strip()

            if not url.startswith("afirma://"):
                if part == total:
                    AfirmaHTTPHandler.url_fragments = ""
                self._send_cors_response(200, b"")
                return

            # Dispatch and get result
            result = self._dispatch_afirma_url(url)

            # Unconditional log for debugging
            self._log("info",
                f"HTTP dispatch result: type={type(result).__name__}, "
                f"truthy={bool(result)}, "
                f"preview={repr(result)[:120]}")

            # Clear stored URL after last part
            if part == total:
                AfirmaHTTPHandler.url_fragments = ""

            # Encode result as base64
            if result is None:
                result_b64 = ""
            elif isinstance(result, str):
                result_b64 = base64.b64encode(result.encode()).decode()
            elif isinstance(result, bytes):
                result_b64 = base64.b64encode(result).decode()
            else:
                result_b64 = str(result)

            self._log(
                f"HTTP result part {part}/{total}: {len(result_b64)} chars base64"
            )
            self._send_cors_response(
                200,
                result_b64.encode() if result_b64 else b""
            )
            return

        # Unknown body
        self._log(f"HTTP unknown body: {body[:80]!r}")
        self._send_cors_response(200, base64.b64encode(b"OK"))

    def _dispatch_afirma_url(self, url):
        """Parse an afirma:// URL and process it synchronously.

        Uses op_handler if available (returns result directly).
        Otherwise falls back to async GUI callback (no result returned).
        """
        from server import parse_afirma_url

        op, params, raw_dat = parse_afirma_url(url)

        if AfirmaHTTPHandler.gui_callback:
            AfirmaHTTPHandler.gui_callback(
                "event",
                f"HTTP operation: {op}, params: {list(params.keys())}, "
                f"dat_len: {len(raw_dat) if raw_dat else 0}, "
                f"dat_preview: {(raw_dat[:60] if raw_dat else 'None')!r}",
            )

        # Use synchronous op_handler for sign and save operations
        if AfirmaHTTPHandler.op_handler:
            if op in ("sign", "cosign", "countersign", "signandsave", "save"):
                try:
                    if AfirmaHTTPHandler.gui_callback:
                        AfirmaHTTPHandler.gui_callback(
                            "info",
                            f"HTTP calling op_handler for {op}, "
                            f"url_len={len(url)}"
                        )
                    result = AfirmaHTTPHandler.op_handler(op, url)
                    if AfirmaHTTPHandler.gui_callback:
                        AfirmaHTTPHandler.gui_callback(
                            "info",
                            f"HTTP op_handler returned: "
                            f"type={type(result).__name__}, "
                            f"len={len(result) if result else 0}"
                        )
                    return result
                except Exception as e:
                    if AfirmaHTTPHandler.gui_callback:
                        AfirmaHTTPHandler.gui_callback(
                            "error",
                            f"HTTP op_handler exception: {type(e).__name__}: {e}"
                        )
                    return f"SAF_ERROR:{e}"

        # Fallback: dispatch asynchronously (result not returned via HTTP)
        if op in ("sign", "cosign", "countersign", "signandsave"):
            if AfirmaHTTPHandler.gui_callback:
                AfirmaHTTPHandler.gui_callback("sign_operation", url)
        elif op == "save":
            if AfirmaHTTPHandler.gui_callback:
                AfirmaHTTPHandler.gui_callback("save_operation", url)
        elif op == "selectcert":
            if AfirmaHTTPHandler.gui_callback:
                AfirmaHTTPHandler.gui_callback("message", url)
        elif op == "batch":
            if AfirmaHTTPHandler.gui_callback:
                AfirmaHTTPHandler.gui_callback("batch_operation", url)
        else:
            if AfirmaHTTPHandler.gui_callback:
                AfirmaHTTPHandler.gui_callback("message", url)

        return None

    def log_message(self, format, *args):
        """Suppress default HTTP logging to stderr."""
        pass


# ----- Server runner -----


class AfirmaHTTPServer:
    """HTTPS server wrapper for the JSSocket protocol."""

    def __init__(self, ports, gui_callback=None, op_handler=None):
        self.ports = ports
        self.gui_callback = gui_callback
        self.op_handler = op_handler  # sync operation handler: func(url) -> bytes|None
        self.httpd = None
        self.port = None

    def start(self):
        """Start the HTTPS server on the first available port."""
        AfirmaHTTPHandler.gui_callback = self.gui_callback
        AfirmaHTTPHandler.op_handler = self.op_handler
        AfirmaHTTPHandler.url_fragments = ""
        AfirmaHTTPHandler.session_id = None

        for port in self.ports:
            try:
                self.httpd = HTTPServer(("127.0.0.1", port), AfirmaHTTPHandler)

                # Wrap with SSL if cert files exist
                if os.path.exists("cert.pem") and os.path.exists("key.pem"):
                    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    ctx.load_cert_chain("cert.pem", "key.pem")
                    self.httpd.socket = ctx.wrap_socket(
                        self.httpd.socket, server_side=True
                    )
                    if self.gui_callback:
                        self.gui_callback(
                            "event",
                            f"HTTPS server started on port {port} "
                            f"(JSSocket mode, from {self.ports})",
                        )
                else:
                    if self.gui_callback:
                        self.gui_callback(
                            "event",
                            f"HTTP server started on port {port} "
                            f"(JSSocket mode, from {self.ports})",
                        )

                self.port = port
                self.httpd.serve_forever()
                return
            except OSError:
                continue

        if self.gui_callback:
            self.gui_callback(
                "error",
                f"Failed to start HTTPS server on any port: {self.ports}",
            )

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()


def start_http_server_thread(ports, gui_callback=None, op_handler=None):
    """Start the HTTPS server in a background thread."""
    server = AfirmaHTTPServer(ports, gui_callback, op_handler)

    def run():
        server.start()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server
