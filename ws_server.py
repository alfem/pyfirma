"""
Servidor WebSocket para el protocolo AutoFirma de PyFirma.

Implementa el lado del servidor del protocolo afirma:// en modo WebSocket.
Cuando el navegador encuentra un enlace afirma://, su script auxiliar
(autoscript.js) abre una conexión WebSocket a los puertos indicados
y envía las operaciones de firma/guardado por este canal.

El servidor:
  1. Escucha en el primer puerto disponible de la lista recibida.
  2. Responde al echo inicial de autoscript.js para confirmar la conexión.
  3. Recibe y parsea URLs afirma:// que contienen las operaciones.
  4. Notifica a la GUI mediante callbacks para que procese cada operación.
  5. Soporta tanto WS (plano) como WSS (TLS) si hay certificados disponibles.

También incluye parse_afirma_url(), usado tanto por el servidor WebSocket
como por el servidor HTTP (JSSocket).
"""

import asyncio
import websockets
import threading
from urllib.parse import urlparse, parse_qs


def parse_afirma_url(url):
    """
    Parsea una URL afirma:// y extrae la operación, parámetros y datos.

    Formato de la URL:
      afirma://<operacion>?<param1>=<val1>&<param2>=<val2>&dat=<datos_base64>

    El parámetro 'dat' usa avoidEncoding=true en autoscript.js,
    por lo que su valor es base64 URL-safe SIN codificación URL.
    'dat' es siempre el ÚLTIMO parámetro de la URL.

    Args:
        url: La URL afirma:// completa (puede contener saltos de línea).

    Returns:
        tuple: (operacion, params, raw_dat) donde:
               - operacion (str): Tipo de operación ('sign', 'save', etc.).
               - params (dict): Diccionario de parámetros parseados.
               - raw_dat (str|None): Valor crudo del parámetro 'dat', o None.
    """
    # Limpiar la URL: eliminar espacios, saltos de línea y marcador @EOF
    url = url.strip()
    if url.endswith('@EOF'):
        url = url[:-4].strip()

    parsed = urlparse(url)

    # La operación está en el netloc (afirma://sign?...) o en el path (afirma:///sign?...)
    op = parsed.netloc or parsed.path.lstrip('/')

    # Parsear los parámetros de la query string (excepto 'dat' que es crudo)
    params = parse_qs(parsed.query, keep_blank_values=True)
    flat = {k: v[0] for k, v in params.items()}

    # Extraer el valor crudo del parámetro 'dat' (NO codificado en URL)
    # 'dat' siempre aparece al final, precedido por '&dat=' o '?dat='
    raw_dat = None
    for prefix in ('&dat=', '?dat='):
        idx = url.find(prefix)
        if idx != -1:
            raw_dat = url[idx + len(prefix):]
            break

    return op, flat, raw_dat


class AfirmaWebSocketServer:
    """
    Servidor WebSocket para el protocolo AutoFirma.

    Escucha conexiones WebSocket del navegador (autoscript.js),
    recibe operaciones afirma:// y las notifica a la GUI
    mediante callbacks para su procesamiento.

    Atributos:
        ports: Lista de puertos donde intentar iniciar el servidor.
        gui_callback: Función callback(event_type, message) para notificar a la GUI.
        server: Instancia del servidor websockets.
        loop: Event loop de asyncio donde corre el servidor.
        active_websocket: WebSocket activo para enviar respuestas.
    """

    def __init__(self, ports, gui_callback=None):
        """
        Inicializa el servidor WebSocket.

        Args:
            ports: Lista de puertos (int) donde intentar escuchar.
            gui_callback: Función a llamar para notificar eventos a la GUI.
                          Recibe (event_type: str, message: str).
        """
        self.ports = ports
        self.gui_callback = gui_callback
        self.server = None
        self.loop = None
        self.active_websocket = None

    @staticmethod
    def _extract_dat_bytes(raw_bytes):
        """
        Extrae el valor crudo de 'dat' de un mensaje WebSocket binario.

        Busca las marcas b'&dat=' o b'?dat=' en los bytes crudos y
        devuelve todo lo que viene después. Esto preserva los datos
        binarios originales del PDF sin corromperlos al decodificar
        a UTF-8.

        Args:
            raw_bytes: Mensaje WebSocket completo en bytes.

        Returns:
            bytes or None: Los bytes después de 'dat=', o None si no se encuentra.
        """
        for sep in (b'&dat=', b'?dat='):
            idx = raw_bytes.find(sep)
            if idx != -1:
                return raw_bytes[idx + len(sep):]
        return None

    def send_response(self, message):
        """
        Envía una respuesta al navegador a través del WebSocket activo.

        Este método es seguro para llamar desde cualquier hilo:
        usa asyncio.run_coroutine_threadsafe() para encolar el envío
        en el event loop del servidor.

        Args:
            message: Mensaje a enviar (str o bytes).
        """
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
                self.gui_callback(
                    "error", f"send_response failed: {type(e).__name__}: {e}"
                )

    async def handle_client(self, websocket):
        """
        Maneja una conexión WebSocket entrante del navegador.

        Es el handler principal: recibe mensajes, los clasifica por
        tipo de operación y notifica a la GUI mediante callbacks.

        Protocolo:
          1. Echo: El navegador envía "echo=..." y espera "echo" de vuelta.
          2. Operación: El navegador envía una URL afirma:// completa.
             Se parsea y se notifica a la GUI según el tipo de operación.

        Args:
            websocket: La conexión WebSocket entrante.
        """
        self.active_websocket = websocket
        if self.gui_callback:
            self.gui_callback(
                "event",
                f"Connected to browser autoscript from {websocket.remote_address}",
            )
        try:
            # Iterar sobre los mensajes recibidos asíncronamente
            async for message in websocket:
                # Decodificar el mensaje a texto si viene en bytes
                text = message.decode('utf-8') if isinstance(message, bytes) else message

                # --- 1. Echo check ---
                # autoscript.js envía "echo=-idsession=SESSIONID@EOF"
                # Hay que responder "echo" para confirmar la conexión.
                if text.strip().startswith("echo"):
                    await websocket.send("echo")
                    if self.gui_callback:
                        self.gui_callback("event", "Echo received, sent echo response")
                    continue

                # --- 2. Parsear como operación afirma:// ---
                if text.strip().startswith("afirma://"):
                    op, params, raw_dat = parse_afirma_url(text.strip())
                    if self.gui_callback:
                        self.gui_callback(
                            "event",
                            f"Operation: {op}, params: {list(params.keys())}, "
                            f"dat_len: {len(raw_dat) if raw_dat else 0}",
                        )

                    if op == "save":
                        # Operación de guardado: el navegador envía el PDF
                        # binario en el parámetro 'dat'.
                        # Si el mensaje llegó como bytes, extraer 'dat'
                        # directamente para evitar corrupción UTF-8.
                        raw_dat_bytes = None
                        if isinstance(message, bytes):
                            raw_dat_bytes = self._extract_dat_bytes(message)
                        if self.gui_callback:
                            self.gui_callback(
                                "save_operation",
                                (text.strip(), raw_dat_bytes),
                            )

                    elif op in ("sign", "cosign", "countersign"):
                        # Operación de firma: el navegador envía datos
                        # y espera el resultado firmado de vuelta.
                        if self.gui_callback:
                            self.gui_callback("sign_operation", text.strip())

                    elif op == "signandsave":
                        # Operación de firma y guardado combinados.
                        if self.gui_callback:
                            self.gui_callback("sign_operation", text.strip())

                    elif op == "batch":
                        # Operación de firma por lotes.
                        if self.gui_callback:
                            self.gui_callback("batch_operation", text.strip())

                    elif op == "selectcert":
                        # Selección de certificado.
                        if self.gui_callback:
                            self.gui_callback("message", text.strip())

                    elif op == "load":
                        # Operación de carga de archivo.
                        if self.gui_callback:
                            self.gui_callback("message", text.strip())

                    else:
                        # Operación desconocida — registrar para depuración.
                        if self.gui_callback:
                            self.gui_callback("message", text.strip())
                else:
                    # Mensaje que no es una URL afirma:// — registrar.
                    if self.gui_callback:
                        self.gui_callback("message", text.strip())

        except websockets.exceptions.ConnectionClosed as e:
            if self.gui_callback:
                self.gui_callback(
                    "event",
                    f"Connection closed: code={e.code}, reason={e.reason!r}",
                )
        except websockets.exceptions.ConnectionClosedError as e:
            if self.gui_callback:
                self.gui_callback(
                    "event",
                    f"Connection closed with error: code={e.code}, reason={e.reason!r}",
                )
        except Exception as e:
            if self.gui_callback:
                self.gui_callback(
                    "error", f"WebSocket error: {type(e).__name__}: {str(e)}"
                )

    async def run_server(self):
        """
        Inicia el servidor WebSocket en el primer puerto disponible.

        Itera sobre la lista de puertos recibidos del navegador.
        Si un puerto está ocupado (OSError), prueba el siguiente.
        Si hay certificados TLS disponibles (cert.pem y key.pem),
        inicia en modo WSS (WebSocket Secure); si no, en modo WS plano.

        El servidor se mantiene ejecutándose indefinidamente
        (await asyncio.Future()) una vez iniciado correctamente.
        """
        import ssl
        import os

        # Configurar TLS si hay certificados disponibles
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
                f"en puertos: {self.ports}",
            )

        # Probar cada puerto hasta encontrar uno disponible
        for port in self.ports:
            try:
                if self.gui_callback:
                    self.gui_callback("info", f"Probando puerto {port}...")

                # Iniciar el servidor websockets
                self.server = await websockets.serve(
                    self.handle_client,
                    "0.0.0.0",
                    port,
                    ssl=ssl_context,
                    ping_interval=30,         # Mantener viva la conexión cada 30s
                    ping_timeout=30,          # Esperar 30s el pong antes de cerrar
                    close_timeout=5,          # Tiempo de cierre graceful
                    max_size=50 * 1024 * 1024,  # 50 MB — el save embebe PDF en URL
                )
                if self.gui_callback:
                    self.gui_callback(
                        "event",
                        f"✓ Servidor {'WSS' if using_tls else 'WS'} iniciado en puerto {port} "
                        f"(de {len(self.ports)} puertos recibidos: {self.ports})",
                    )
                # Mantener el servidor corriendo indefinidamente
                await asyncio.Future()
                return
            except OSError as e:
                if self.gui_callback:
                    self.gui_callback(
                        "info", f"Puerto {port} ocupado, intentando siguiente..."
                    )
                continue
            except Exception as e:
                if self.gui_callback:
                    self.gui_callback(
                        "error", f"Fallo en puerto {port}: {str(e)}"
                    )
                continue

        # Si ningún puerto funcionó, notificar el error
        if self.gui_callback:
            self.gui_callback(
                "error",
                f"No se pudo iniciar el servidor en ninguno de los puertos: {self.ports}",
            )


def start_server_thread(ports, gui_callback):
    """
    Inicia el servidor WebSocket en un hilo en segundo plano.

    Crea un nuevo event loop de asyncio en un hilo separado
    para no bloquear el hilo principal de la GUI (tkinter).

    Args:
        ports: Lista de puertos donde intentar escuchar.
        gui_callback: Función callback para notificar eventos a la GUI.

    Returns:
        AfirmaWebSocketServer: Instancia del servidor iniciado.
    """
    server = AfirmaWebSocketServer(ports, gui_callback)

    def run():
        """
        Función objetivo del hilo: crea un event loop asyncio
        independiente y ejecuta el servidor en él.
        """
        loop = asyncio.new_event_loop()
        server.loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.run_server())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return server
