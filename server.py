import asyncio
import websockets
import threading
from urllib.parse import urlparse, parse_qs


def parse_afirma_url(url):
    """Parse an afirma:// URL and return (operation, params_dict, raw_dat).

    The dat parameter uses avoidEncoding=true in autoscript.js,
    so its value is raw URL-safe base64, NOT URL-encoded.
    The dat parameter is always the LAST parameter in the URL.
    """
    # Strip any trailing @EOF markers or whitespace
    url = url.strip()
    if url.endswith('@EOF'):
        url = url[:-4].strip()

    parsed = urlparse(url)
    # Operation is the netloc (afirma://sign?...) or path (afirma:///sign?...)
    op = parsed.netloc or parsed.path.lstrip('/')

    # Parse query params (except dat which is raw and may be multi-line)
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: v[0] for k, v in params.items()}

    # Extract raw dat value (NOT URL-encoded, may span multiple lines)
    raw_dat = None
    for prefix in ('&dat=', '?dat='):
        idx = url.find(prefix)
        if idx != -1:
            raw_dat = url[idx + len(prefix):]
            break

    return op, flat, raw_dat


class AfirmaWebSocketServer:
    def __init__(self, ports, gui_callback=None):
        self.ports = ports
        self.gui_callback = gui_callback
        self.server = None
        self.loop = None
        self.active_websocket = None

    @staticmethod
    def _extract_dat_bytes(raw_bytes):
        """Extract raw dat value from binary WebSocket message.

        Searches for b'&dat=' or b'?dat=' in the raw bytes and returns
        everything after it. This preserves the original binary PDF data
        without corrupting it through UTF-8 decoding.
        """
        for sep in (b'&dat=', b'?dat='):
            idx = raw_bytes.find(sep)
            if idx != -1:
                return raw_bytes[idx + len(sep):]
        return None

    def send_response(self, message):
        if not self.loop:
            if self.gui_callback:
                self.gui_callback("error", "send_response: event loop is None")
            return
        if not self.active_websocket:
            if self.gui_callback:
                self.gui_callback("error", "send_response: no active websocket")
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.active_websocket.send(message), self.loop
            )
        except Exception as e:
            if self.gui_callback:
                self.gui_callback("error", f"send_response failed: {type(e).__name__}: {e}")

    async def handle_client(self, websocket):
        self.active_websocket = websocket
        if self.gui_callback:
            self.gui_callback(
                "event",
                f"Connected to browser autoscript from {websocket.remote_address}"
            )
        try:
            async for message in websocket:
                text = message.decode('utf-8') if isinstance(message, bytes) else message

                # 1. Echo check — autoscript.js sends "echo=-idsession=SESSIONID@EOF"
                if text.strip().startswith("echo"):
                    await websocket.send("echo")
                    if self.gui_callback:
                        self.gui_callback("event", "Echo received, sent echo response")
                    continue

                # 2. Parse as afirma:// operation
                if text.strip().startswith("afirma://"):
                    op, params, raw_dat = parse_afirma_url(text.strip())
                    if self.gui_callback:
                        self.gui_callback("event",
                            f"Operation: {op}, params: {list(params.keys())}, "
                            f"dat_len: {len(raw_dat) if raw_dat else 0}")

                    if op == "save":
                        # Save operation: browser sends binary PDF in dat parameter.
                        # If the message arrived as bytes, extract the binary dat
                        # directly to avoid UTF-8 decoding corruption.
                        raw_dat = None
                        if isinstance(message, bytes):
                            raw_dat = self._extract_dat_bytes(message)
                        if self.gui_callback:
                            self.gui_callback("save_operation", (text.strip(), raw_dat))

                    elif op in ("sign", "cosign", "countersign"):
                        # Sign operation: browser sends data, expects signed result back
                        if self.gui_callback:
                            self.gui_callback("sign_operation", text.strip())

                    elif op == "signandsave":
                        # Sign and save: browser sends data, expects it signed and saved
                        if self.gui_callback:
                            self.gui_callback("sign_operation", text.strip())

                    elif op == "batch":
                        # Batch signing operation
                        if self.gui_callback:
                            self.gui_callback("batch_operation", text.strip())

                    elif op == "selectcert":
                        # Certificate selection
                        if self.gui_callback:
                            self.gui_callback("message", text.strip())

                    elif op == "load":
                        # File load operation
                        if self.gui_callback:
                            self.gui_callback("message", text.strip())

                    else:
                        # Unknown operation — log for debugging
                        if self.gui_callback:
                            self.gui_callback("message", text.strip())
                else:
                    # Non-afirma message — log for debugging
                    if self.gui_callback:
                        self.gui_callback("message", text.strip())

        except websockets.exceptions.ConnectionClosed as e:
            if self.gui_callback:
                self.gui_callback(
                    "event",
                    f"Connection closed: code={e.code}, reason={e.reason!r}"
                )
        except websockets.exceptions.ConnectionClosedError as e:
            if self.gui_callback:
                self.gui_callback(
                    "event",
                    f"Connection closed with error: code={e.code}, reason={e.reason!r}"
                )
        except Exception as e:
            if self.gui_callback:
                self.gui_callback("error", f"WebSocket error: {type(e).__name__}: {str(e)}")

    async def run_server(self):
        import ssl
        import os
        ssl_context = None
        using_tls = False
        if os.path.exists('cert.pem') and os.path.exists('key.pem'):
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain('cert.pem', 'key.pem')
            using_tls = True

        if self.gui_callback:
            self.gui_callback(
                "info",
                f"Intentando iniciar servidor {'WSS (TLS)' if using_tls else 'WS (plano)'} "
                f"en puertos: {self.ports}"
            )

        for port in self.ports:
            try:
                if self.gui_callback:
                    self.gui_callback("info", f"Probando puerto {port}...")
                self.server = await websockets.serve(
                    self.handle_client, "127.0.0.1", port, ssl=ssl_context,
                    ping_interval=30,       # Keep connection alive every 30s
                    ping_timeout=30,        # Wait 30s for pong before closing
                    close_timeout=5,        # Graceful close timeout
                    max_size=50 * 1024 * 1024,  # 50 MB — save operations embed PDF in URL
                )
                if self.gui_callback:
                    self.gui_callback(
                        "event", f"✓ Servidor {'WSS' if using_tls else 'WS'} iniciado en puerto {port} "
                        f"(de {len(self.ports)} puertos recibidos: {self.ports})"
                    )
                await asyncio.Future()  # run forever
                return
            except OSError as e:
                if self.gui_callback:
                    self.gui_callback("info", f"Puerto {port} ocupado, intentando siguiente...")
                continue
            except Exception as e:
                if self.gui_callback:
                    self.gui_callback(
                        "error", f"Fallo en puerto {port}: {str(e)}"
                    )
                continue

        if self.gui_callback:
            self.gui_callback(
                "error",
                f"No se pudo iniciar el servidor en ninguno de los puertos: {self.ports}"
            )


def start_server_thread(ports, gui_callback):
    """Starts the WebSocket server in a background thread."""
    server = AfirmaWebSocketServer(ports, gui_callback)

    def run():
        loop = asyncio.new_event_loop()
        server.loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.run_server())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server
