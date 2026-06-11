"""
Servidor HTTPS local de PyFirma — implementa el protocolo AppAfirmaJSSocket.

Algunos sitios web utilizan el modo JSSocket en lugar de WebSocket para
comunicarse con AutoFirma. En este modo, el navegador envía peticiones
HTTP POST a https://127.0.0.1:PUERTO/afirma en lugar de abrir una
conexión WebSocket.

Protocolo JSSocket:
  1. Echo inicial:
     POST /afirma  body="echo=-idsession=SESSIONID@EOF"
     → 200 OK con base64("OK") como cuerpo.

  2. Fragmentos de URL (datos enviados por partes):
     POST /afirma  body="cmd=FRAGMENTO_BASE64_URLSAFE&idsession=SESSIONID@EOF"
     → 200 OK con base64("MORE_DATA_NEED") o base64("OK").

  3. Ejecución (el navegador solicita el resultado):
     POST /afirma  body="firm=idsession=SESSIONID@EOF"
     → 200 OK con base64(resultado).

  4. Solicitud de fragmento de resultado (el navegador recoge partes):
     POST /afirma  body="send=@PARTE@TOTALidsession=SESSIONID@EOF"
     → 200 OK con el fragmento de resultado en base64.

  5. CORS preflight:
     OPTIONS /afirma → 204 con cabeceras CORS.

Todas las respuestas incluyen cabeceras CORS:
  Access-Control-Allow-Origin: *
"""

import base64
import json
import os
import ssl
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


# ===========================================================================
# Manejador de peticiones HTTP para el endpoint /afirma
# ===========================================================================


class AfirmaHTTPHandler(BaseHTTPRequestHandler):
    """
    Manejador de peticiones HTTP para el protocolo JSSocket.

    Atributos de clase (compartidos entre todas las peticiones):
        url_fragments: Acumulador de fragmentos de URL recibidos.
        session_id: ID de sesión actual.
        gui_callback: Función para notificar eventos a la GUI.
        op_handler: Función síncrona para procesar operaciones de firma/guardado.
    """

    # --- Estado de sesión (compartido entre peticiones HTTP) ---
    url_fragments = ""
    session_id = None
    gui_callback = None
    op_handler = None  # manejador síncrono: func(op, url) -> bytes|str|None

    # --- Cabeceras CORS para todas las respuestas ---
    CORS_HEADERS = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    def _send_cors_response(self, status=200, body=None):
        """
        Envía una respuesta HTTP con cabeceras CORS.

        Args:
            status: Código de estado HTTP (por defecto 200).
            body: Cuerpo de la respuesta (str, bytes o None).
        """
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
        """
        Registra un mensaje de log en la GUI y en el archivo de depuración.

        Acepta dos formas de llamada:
          _log("mensaje")                → nivel "info"
          _log("nivel", "mensaje")       → nivel especificado

        Escribe también en /tmp/pyfirma_http.log para depuración.
        """
        if len(args) == 1:
            level, msg = "info", args[0]
        elif len(args) == 2:
            level, msg = args
        else:
            return

        # Notificar a la GUI (hilo de tkinter)
        if AfirmaHTTPHandler.gui_callback:
            AfirmaHTTPHandler.gui_callback(level, msg)

        # También registrar en archivo para depuración
        try:
            with open("/tmp/pyfirma_http.log", "a") as f:
                f.write(f"[HTTP] [{level.upper()}] {msg}\n")
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Métodos HTTP
    # -----------------------------------------------------------------------

    def do_OPTIONS(self):
        """
        Maneja la petición CORS preflight (OPTIONS).

        Responde con 204 No Content y las cabeceras CORS necesarias
        para que el navegador permita la petición POST posterior.
        """
        self._send_cors_response(204)

    def do_POST(self):
        """
        Maneja las peticiones POST al endpoint /afirma.

        Clasifica la petición según el prefijo del cuerpo:
          - "echo=" → Echo inicial de conexión.
          - "cmd="  → Fragmento de URL (acumula hasta recibir "@EOF").
          - "firm=" → El navegador solicita ejecutar la operación acumulada.
          - "send=" → El navegador solicita un fragmento del resultado.

        Si la ruta no es /afirma, responde 404.
        """
        # Solo se atiende el endpoint /afirma
        if self.path != "/afirma":
            self._send_cors_response(404, b"Not Found")
            return

        # Leer el cuerpo de la petición
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("ascii", errors="replace")

        # ---- 1. Echo inicial ----
        # El navegador envía "echo=-idsession=SESSIONID@EOF"
        # para verificar que el servidor está activo.
        if body.startswith("echo="):
            self._log("HTTP echo received")
            # Responder con base64("OK")
            self._send_cors_response(200, base64.b64encode(b"OK"))
            return

        # ---- 2. Fragmento de URL (cmd=) ----
        # El navegador envía la URL afirma:// por partes en base64 URL-safe.
        # Cada fragmento se acumula en url_fragments.
        if body.startswith("cmd="):
            eof_pos = body.find("@EOF")
            if eof_pos > 0:
                cmd_data = body[4:eof_pos]
            else:
                cmd_data = body[4:].strip()

            # Decodificar el fragmento de base64 URL-safe
            try:
                fragment = base64.urlsafe_b64decode(
                    cmd_data + "=" * (-len(cmd_data) % 4)
                ).decode("ascii", errors="replace")
            except Exception:
                fragment = cmd_data

            # Acumular el fragmento recibido
            AfirmaHTTPHandler.url_fragments += fragment
            self._log(
                f"HTTP cmd fragment ({len(fragment)} chars), "
                f"total accumulated: {len(AfirmaHTTPHandler.url_fragments)}"
            )

            # Responder con el número de partes del resultado.
            # El navegador espera un número codificado en base64 (ej. "1").
            # Luego solicitará cada parte con send=@PARTE@TOTAL.
            self._send_cors_response(200, base64.b64encode(b"1"))
            return

        # ---- 3. Ejecución (firm=) ----
        # El navegador indica que ya ha enviado todos los fragmentos
        # y solicita que se procese la operación acumulada.
        if body.startswith("firm="):
            self._log(
                f"HTTP firm — processing accumulated URL "
                f"({len(AfirmaHTTPHandler.url_fragments)} chars)"
            )
            # Mismo comportamiento que cmd=: responder con número de partes
            self._send_cors_response(200, base64.b64encode(b"1"))
            return

        # ---- 4. Solicitud de fragmento de resultado (send=) ----
        # Formato: send=@PARTE@TOTALidsession=SESSIONID@EOF
        # El navegador solicita el fragmento N del resultado.
        if body.startswith("send="):
            # Extraer número de parte y total
            send_data = body[5:]  # después de "send="
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

            # Procesar la URL acumulada y devolver el resultado
            url = AfirmaHTTPHandler.url_fragments.strip()

            # Si la URL acumulada no es una URL afirma://, responder vacío
            if not url.startswith("afirma://"):
                if part == total:
                    AfirmaHTTPHandler.url_fragments = ""
                self._send_cors_response(200, b"")
                return

            # Despachar la URL afirma:// y obtener el resultado
            result = self._dispatch_afirma_url(url)

            # Registrar el resultado para depuración
            self._log(
                "info",
                f"HTTP dispatch result: type={type(result).__name__}, "
                f"truthy={bool(result)}, "
                f"preview={repr(result)[:120]}",
            )

            # Codificar el resultado en base64
            if result is None:
                result_b64 = ""
            elif isinstance(result, str):
                result_b64 = base64.b64encode(result.encode()).decode()
            elif isinstance(result, bytes):
                result_b64 = base64.b64encode(result).decode()
            else:
                result_b64 = str(result)

            # Solo limpiar la URL acumulada si hay un resultado real.
            # Si result_b64 está vacío (el usuario aún no ha desbloqueado
            # el certificado), mantenemos la URL para que el navegador
            # pueda reintentar en el siguiente poll.
            if part == total and result_b64:
                AfirmaHTTPHandler.url_fragments = ""

            self._log(
                f"HTTP result part {part}/{total}: {len(result_b64)} chars base64"
            )
            self._send_cors_response(
                200,
                result_b64.encode() if result_b64 else b"",
            )
            return

        # ---- Cuerpo desconocido ----
        self._log(f"HTTP unknown body: {body[:80]!r}")
        self._send_cors_response(200, base64.b64encode(b"OK"))

    def _dispatch_afirma_url(self, url):
        """
        Parsea una URL afirma:// y la procesa de forma síncrona.

        Si hay un op_handler disponible (modo GUI), lo usa para
        procesar operaciones de firma y guardado directamente.
        Si no, notifica a la GUI de forma asíncrona (sin devolver resultado).

        Args:
            url: La URL afirma:// completa a procesar.

        Returns:
            str, bytes o None: El resultado de la operación, o None
            si se despachó asíncronamente.
        """
        from ws_server import parse_afirma_url

        op, params, raw_dat = parse_afirma_url(url)

        # Notificar a la GUI sobre la operación recibida
        if AfirmaHTTPHandler.gui_callback:
            AfirmaHTTPHandler.gui_callback(
                "event",
                f"HTTP operation: {op}, params: {list(params.keys())}, "
                f"dat_len: {len(raw_dat) if raw_dat else 0}, "
                f"dat_preview: {(raw_dat[:60] if raw_dat else 'None')!r}",
            )

        # Usar el manejador síncrono para operaciones de firma y guardado
        if AfirmaHTTPHandler.op_handler:
            if op in ("sign", "cosign", "countersign", "signandsave", "save"):
                try:
                    if AfirmaHTTPHandler.gui_callback:
                        AfirmaHTTPHandler.gui_callback(
                            "info",
                            f"HTTP calling op_handler for {op}, "
                            f"url_len={len(url)}",
                        )
                    result = AfirmaHTTPHandler.op_handler(op, url)
                    if AfirmaHTTPHandler.gui_callback:
                        AfirmaHTTPHandler.gui_callback(
                            "info",
                            f"HTTP op_handler returned: "
                            f"type={type(result).__name__}, "
                            f"len={len(result) if result else 0}",
                        )
                    return result
                except Exception as e:
                    if AfirmaHTTPHandler.gui_callback:
                        AfirmaHTTPHandler.gui_callback(
                            "error",
                            f"HTTP op_handler exception: {type(e).__name__}: {e}",
                        )
                    return f"SAF_ERROR:{e}"

        # Fallback: despachar asíncronamente (sin devolver resultado por HTTP)
        # Esto se usa cuando la GUI necesita interactuar con el usuario
        # (ej. seleccionar archivo, introducir contraseña).
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
        """
        Suprime el logging HTTP por defecto a stderr.

        El servidor HTTP de Python imprime cada petición a stderr;
        lo anulamos para no ensuciar la salida.
        """
        pass


# ===========================================================================
# Envoltorio del servidor HTTPS
# ===========================================================================


class AfirmaHTTPServer:
    """
    Servidor HTTPS para el protocolo JSSocket.

    Envuelve http.server.HTTPServer con soporte TLS opcional
    y lo ejecuta en un hilo separado.

    Atributos:
        ports: Lista de puertos donde intentar iniciar el servidor.
        gui_callback: Función para notificar eventos a la GUI.
        op_handler: Función síncrona para procesar operaciones.
        httpd: Instancia del servidor HTTP de Python.
        port: Puerto en el que se inició el servidor.
    """

    def __init__(self, ports, gui_callback=None, op_handler=None):
        """
        Inicializa el servidor HTTPS.

        Args:
            ports: Lista de puertos (int) donde intentar escuchar.
            gui_callback: Función callback para notificar a la GUI.
            op_handler: Función síncrona op(op, url) -> bytes|str|None
                        para procesar operaciones de firma/guardado.
        """
        self.ports = ports
        self.gui_callback = gui_callback
        self.op_handler = op_handler
        self.httpd = None
        self.port = None

    def start(self):
        """
        Inicia el servidor HTTPS en el primer puerto disponible.

        Itera sobre la lista de puertos. Si hay archivos cert.pem
        y key.pem, envuelve el socket con TLS (HTTPS). Si no,
        inicia en HTTP plano.
        """
        # Configurar el manejador con los callbacks
        AfirmaHTTPHandler.gui_callback = self.gui_callback
        AfirmaHTTPHandler.op_handler = self.op_handler
        AfirmaHTTPHandler.url_fragments = ""
        AfirmaHTTPHandler.session_id = None

        for port in self.ports:
            try:
                self.httpd = HTTPServer(("127.0.0.1", port), AfirmaHTTPHandler)

                # Envolver con SSL si hay certificados disponibles
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
                # Iniciar el bucle de servicio (bloquea este hilo)
                self.httpd.serve_forever()
                return
            except OSError:
                # Puerto ocupado, probar el siguiente
                continue

        # Si ningún puerto funcionó, notificar el error
        if self.gui_callback:
            self.gui_callback(
                "error",
                f"Failed to start HTTPS server on any port: {self.ports}",
            )

    def stop(self):
        """
        Detiene el servidor HTTPS.
        """
        if self.httpd:
            self.httpd.shutdown()


def start_http_server_thread(ports, gui_callback=None, op_handler=None):
    """
    Inicia el servidor HTTPS en un hilo en segundo plano.

    Args:
        ports: Lista de puertos donde intentar escuchar.
        gui_callback: Función callback para notificar eventos a la GUI.
        op_handler: Función síncrona para procesar operaciones.

    Returns:
        AfirmaHTTPServer: Instancia del servidor iniciado.
    """
    server = AfirmaHTTPServer(ports, gui_callback, op_handler)

    def run():
        """Función objetivo del hilo: inicia el servidor."""
        server.start()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server
