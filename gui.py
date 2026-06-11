"""
Interfaz gráfica de PyFirma.

Implementa la aplicación de escritorio con customtkinter que permite:
  1. Seleccionar un documento PDF a firmar.
  2. Cargar un certificado digital .p12/.pfx.
  3. Introducir la contraseña del certificado.
  4. Configurar opciones de firma visible (marca de agua).
  5. Ejecutar la firma y guardar el resultado.

Además, en modo interceptor (cuando se invoca desde el navegador
con una URL afirma://), actúa como servidor WebSocket/HTTP que:
  - Recibe operaciones de firma y guardado desde el navegador.
  - Procesa las peticiones automáticamente (modo online).
  - También permite firma manual cuando el navegador envía
    peticiones sin datos incrustados.
"""

import customtkinter
import os
import json
import base64
import tempfile
from urllib.parse import parse_qs, urlparse
from tkinter import filedialog, messagebox
import threading
from signer import sign_pdf


# --- Archivos de control en el directorio temporal del sistema ---

#LOCK_FILE: evita que se abran múltiples instancias de PyFirma.
LOCK_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.lock")

#REDIRECT_FILE: usado para pasar URLs afirma:// entre instancias.
REDIRECT_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.redirect")


# --- Importaciones condicionales para modo interceptor ---
# Si server.py y http_server.py están disponibles, se usan
# para el modo AutoFirma (WebSocket + HTTP JSSocket).

try:
    import ws_server
    from ws_server import parse_afirma_url
except ImportError:
    ws_server = None
    parse_afirma_url = None

try:
    from http_server import start_http_server_thread
except ImportError:
    start_http_server_thread = None


# ---------------------------------------------------------------------------
# Configuración de la apariencia de customtkinter
# ---------------------------------------------------------------------------

customtkinter.set_appearance_mode("System")   # Seguir el tema del sistema (claro/oscuro)
customtkinter.set_default_color_theme("blue") # Tema de color azul


# ===========================================================================
# Clase principal de la aplicación
# ===========================================================================

class App(customtkinter.CTk):
    """
    Ventana principal de PyFirma.

    Soporta dos modos:
      - Modo normal: interfaz sencilla para seleccionar PDF,
        certificado y firmar.
      - Modo interceptor: arranca servidores WebSocket/HTTP para
        recibir operaciones desde el navegador (protocolo afirma://).
    """

    def __init__(self, afirma_url=None, afirma_ports=None):
        """
        Inicializa la aplicación.

        Args:
            afirma_url: URL afirma:// recibida del navegador (modo interceptor).
            afirma_ports: Lista de puertos donde arrancar los servidores.
        """
        super().__init__()

        # --- Configuración de la ventana ---
        self.title("PyFirma")
        self.geometry("700x500")
        # Configurar qué hacer al cerrar la ventana (limpiar lock file)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # --- Estado del modo interceptor ---
        self.afirma_url = afirma_url
        self.afirma_ports = afirma_ports
        self.ws_server = None   # Servidor WebSocket (modo interceptor)
        self.http_server = None # Servidor HTTPS (modo JSSocket)

        # --- Estado de la firma ---
        self.input_file = None          # Ruta del PDF seleccionado
        self.cert_file = None           # Ruta del certificado seleccionado
        self.pending_sign_request = False  # True cuando el navegador pide firma sin datos

        # Archivo de configuración para persistencia
        self.config_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.json"
        )

        # --- Variables thread-safe para el manejador HTTP (JSSocket) ---
        # Se sincronizan desde el hilo principal para evitar accesos
        # a tkinter desde hilos secundarios.
        self._http_cert_file = None
        self._http_password = ""

        # --- Inicialización ---
        self.load_config()   # Cargar configuración persistente
        self.setup_ui()      # Construir la interfaz gráfica

        if self.afirma_url:
            # Modo interceptor: ampliar ventana y arrancar servidores
            self.setup_interceptor_addons()
            if self.afirma_ports and ws_server:
                # Iniciar servidor WebSocket
                self.ws_server = ws_server.start_server_thread(
                    self.afirma_ports, self.on_ws_event
                )
                # También iniciar servidor HTTPS para modo JSSocket
                if start_http_server_thread:
                    self.http_server = start_http_server_thread(
                        self.afirma_ports, self.on_ws_event,
                        op_handler=self.process_http_operation,
                    )
            # Iniciar el polling de redirecciones (instancia única)
            self.check_redirect_file()

        # Si se cargó un certificado desde la configuración, sincronizar
        if self.cert_file and os.path.exists(self.cert_file):
            self.cert_path_label.configure(
                text=os.path.basename(self.cert_file), text_color="white"
            )
            self._http_cert_file = self.cert_file
            self.check_ready()

    # =======================================================================
    # Persistencia de configuración
    # =======================================================================

    def load_config(self):
        """
        Carga la configuración persistente desde config.json.

        Recupera la última ruta de certificado usada y, opcionalmente,
        la contraseña almacenada en caché.
        """
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    config = json.load(f)
                    self.cert_file = config.get("last_cert_path")
                    self.cached_password = config.get("last_password", "")
            except Exception:
                self.cached_password = ""
        else:
            self.cached_password = ""

    def save_config(self):
        """
        Guarda la configuración actual en config.json.

        Almacena la ruta del certificado y, si el checkbox de
        recordar contraseña está marcado, también la contraseña.
        """
        try:
            config = {"last_cert_path": self.cert_file}
            if self.cache_pass_var.get() and self.pass_entry.get():
                config["last_password"] = self.pass_entry.get()
            else:
                config["last_password"] = ""
            with open(self.config_file, "w") as f:
                json.dump(config, f)
        except Exception:
            pass

    # =======================================================================
    # Construcción de la interfaz gráfica
    # =======================================================================

    def setup_ui(self):
        """
        Construye la interfaz de usuario principal.

        Estructura:
          - Cabecera con el título de la aplicación.
          - Marco principal con los controles de selección.
          - Botón de firma.
          - Etiqueta de estado.
        """
        # --- Cabecera ---
        self.header_label = customtkinter.CTkLabel(
            self,
            text="PyFirma - Firmador de PDF",
            font=customtkinter.CTkFont(size=20, weight="bold"),
        )
        self.header_label.pack(pady=20)

        # --- Marco principal (contiene los controles) ---
        self.frame = customtkinter.CTkFrame(self)
        self.frame.pack(pady=20, padx=20, fill="both", expand=True)

        # --- Fila 0: Selección de documento PDF ---
        self.file_label = customtkinter.CTkLabel(
            self.frame, text="Documento:"
        )
        self.file_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        self.file_path_label = customtkinter.CTkLabel(
            self.frame,
            text="Ningún archivo seleccionado",
            text_color="gray",
            wraplength=300,
        )
        self.file_path_label.grid(row=0, column=1, padx=10, pady=10, sticky="w")

        self.file_button = customtkinter.CTkButton(
            self.frame, text="Seleccionar PDF", command=self.select_file
        )
        self.file_button.grid(row=0, column=2, padx=10, pady=10)

        # --- Fila 1: Selección de certificado ---
        self.cert_label = customtkinter.CTkLabel(
            self.frame, text="Certificado (.p12):"
        )
        self.cert_label.grid(row=1, column=0, padx=10, pady=10, sticky="w")

        self.cert_path_label = customtkinter.CTkLabel(
            self.frame,
            text="Ningún certificado seleccionado",
            text_color="gray",
            wraplength=300,
        )
        self.cert_path_label.grid(row=1, column=1, padx=10, pady=10, sticky="w")

        self.cert_button = customtkinter.CTkButton(
            self.frame, text="Seleccionar Certificado", command=self.select_cert
        )
        self.cert_button.grid(row=1, column=2, padx=10, pady=10)

        # --- Fila 2: Contraseña del certificado ---
        self.pass_label = customtkinter.CTkLabel(
            self.frame, text="Contraseña:"
        )
        self.pass_label.grid(row=2, column=0, padx=10, pady=10, sticky="w")

        self.pass_entry = customtkinter.CTkEntry(
            self.frame, show="*", width=200
        )
        self.pass_entry.grid(row=2, column=1, padx=10, pady=10, sticky="w")

        # Rellenar la contraseña si estaba en caché
        if self.cached_password:
            self.pass_entry.insert(0, self.cached_password)

        # Sincronizar la variable thread-safe para el manejador HTTP
        self._http_password = self.cached_password
        self.pass_entry.bind(
            "<KeyRelease>", lambda e: self._on_password_changed()
        )

        # Checkbox para recordar contraseña
        self.cache_pass_var = customtkinter.BooleanVar(
            value=bool(self.cached_password)
        )
        self.cache_pass_checkbox = customtkinter.CTkCheckBox(
            self.frame,
            text="Recordar contraseña",
            variable=self.cache_pass_var,
            command=self.on_cache_pass_toggle,
        )
        self.cache_pass_checkbox.grid(
            row=2, column=2, padx=10, pady=10, sticky="w"
        )

        # --- Fila 3: Opción de firma visible ---
        self.visible_var = customtkinter.BooleanVar(value=False)
        self.visible_checkbox = customtkinter.CTkCheckBox(
            self.frame,
            text="Añadir firma visible",
            variable=self.visible_var,
            command=self.toggle_visible_options,
        )
        self.visible_checkbox.grid(
            row=3, column=0, columnspan=2, padx=10, pady=10, sticky="w"
        )

        # --- Margen izquierdo vertical (inicialmente deshabilitado) ---
        self.vertical_left_var = customtkinter.BooleanVar(value=False)
        self.vertical_left_checkbox = customtkinter.CTkCheckBox(
            self.frame,
            text="Margen izquierdo vertical",
            variable=self.vertical_left_var,
        )
        self.vertical_left_checkbox.grid(
            row=3, column=2, padx=10, pady=10, sticky="w"
        )
        self.vertical_left_checkbox.configure(state="disabled")

        # --- Fila 4: Firmar todas las páginas (inicialmente deshabilitado) ---
        self.all_pages_var = customtkinter.BooleanVar(value=False)
        self.all_pages_checkbox = customtkinter.CTkCheckBox(
            self.frame,
            text="Firmar todas las páginas",
            variable=self.all_pages_var,
        )
        self.all_pages_checkbox.grid(
            row=4, column=0, columnspan=2, padx=10, pady=10, sticky="w"
        )
        self.all_pages_checkbox.configure(state="disabled")

        # --- Botón de firma (inicialmente deshabilitado) ---
        self.sign_button = customtkinter.CTkButton(
            self,
            text="Firmar Documento",
            command=self.start_signing,
            height=40,
            font=customtkinter.CTkFont(size=16, weight="bold"),
            state="disabled",
        )
        self.sign_button.pack(pady=20)

        # --- Etiqueta de estado ---
        self.status_label = customtkinter.CTkLabel(
            self, text="Listo", text_color="gray"
        )
        self.status_label.pack(side="bottom", pady=10)

    # =======================================================================
    # Modo interceptor (afirma://)
    # =======================================================================

    def setup_interceptor_addons(self):
        """
        Configura los elementos adicionales de la interfaz para el modo
        interceptor: muestra la URL afirma://, los puertos, y un visor
        de logs para seguir las operaciones en tiempo real.
        """
        # Ampliar la ventana para acomodar los logs
        self.geometry("900x700")

        # Cambiar el título de la cabecera
        self.header_label.configure(
            text="PyFirma - Interceptor de AutoFirma", text_color="#1E90FF"
        )

        # --- Campo de URL (solo lectura, seleccionable) ---
        self.url_frame = customtkinter.CTkFrame(self)
        self.url_frame.pack(pady=5, padx=20, fill="x")

        self.url_label_title = customtkinter.CTkLabel(
            self.url_frame, text="URL:", text_color="gray"
        )
        self.url_label_title.pack(side="left", padx=(0, 5))

        self.url_textbox = customtkinter.CTkTextbox(
            self.url_frame,
            height=28,
            font=customtkinter.CTkFont(family="Courier", size=11),
        )
        self.url_textbox.pack(side="left", fill="x", expand=True)
        self.url_textbox.insert("1.0", self.afirma_url)
        self.url_textbox.configure(state="disabled")  # Solo lectura

        # Mostrar los puertos recibidos del navegador
        if self.afirma_ports:
            self.log_event(
                "info", f"Puertos recibidos del navegador: {self.afirma_ports}"
            )
        else:
            self.log_event(
                "error",
                "¡No se detectaron puertos en la URL! El navegador no podrá conectar.",
            )

        # --- Visor de logs ---
        self.logs_textbox = customtkinter.CTkTextbox(
            self, height=200,
            font=customtkinter.CTkFont(family="Courier", size=12),
        )
        self.logs_textbox.pack(pady=10, padx=20, fill="both", expand=True)
        self.logs_textbox.insert("end", "Esperando conexión del navegador...\n\n")
        self.logs_textbox.configure(state="disabled")

        # Si no hay puertos, mostrar los parámetros de la URL decodificados
        if not self.afirma_ports:
            parsed = urlparse(self.afirma_url)
            params = parse_qs(parsed.query)
            try:
                self.log_event(
                    "info",
                    "No se han recibido puertos WebSocket. Parámetros de la URL:",
                )
                for k, v in params.items():
                    val = v[0]
                    try:
                        decoded = base64.b64decode(val).decode('utf-8')
                        self.log_event(
                            "param",
                            f"{k} = {decoded} (decodificado base64)",
                        )
                    except Exception:
                        self.log_event("param", f"{k} = {val}")
            except Exception as e:
                self.log_event("error", str(e))

    def on_ws_event(self, event_type, message):
        """
        Callback invocado desde los servidores (WebSocket/HTTP) al recibir
        una operación del navegador.

        Se ejecuta en un hilo secundario — usa self.after() para delegar
        al hilo principal de tkinter, que es el único que puede manipular
        la interfaz gráfica.

        Args:
            event_type: Tipo de evento ('save_operation', 'sign_operation',
                        'message', 'info', 'error', 'event', etc.).
            message: Datos del evento (str, bytes o tuple).
        """
        if event_type == "save_operation":
            self.after(0, lambda m=message: self.handle_save_operation(m))
        elif event_type == "sign_operation":
            self.after(0, lambda m=message: self.handle_sign_operation(m))
        else:
            self.after(0, lambda: self.log_event(event_type, message))

    # =======================================================================
    # Control de instancia única y redirección
    # =======================================================================

    def on_close(self):
        """
        Cierra la aplicación limpiando el archivo de bloqueo.

        Se llama cuando el usuario cierra la ventana (WM_DELETE_WINDOW).
        """
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
        self.destroy()

    def check_redirect_file(self):
        """
        Comprueba periódicamente si otra instancia de PyFirma ha escrito
        una URL afirma:// en REDIRECT_FILE para redirigirla aquí.

        Cuando autoscript.js relanza PyFirma con nuevos puertos aleatorios
        (porque el WebSocket se cerró), el segundo proceso main.py escribe
        la URL en REDIRECT_FILE y sale. La instancia original la recoge aquí
        y arranca un nuevo servidor WS en los puertos solicitados.

        Se llama cada 1 segundo mediante self.after().
        """
        try:
            if os.path.exists(REDIRECT_FILE):
                with open(REDIRECT_FILE) as f:
                    url = f.read().strip()
                os.remove(REDIRECT_FILE)

                if url.startswith("afirma://"):
                    parsed = urlparse(url)
                    params = parse_qs(parsed.query)
                    if 'ports' in params:
                        ports = [
                            int(p.strip())
                            for p in params['ports'][0].split(',')
                            if p.strip().isdigit()
                        ]
                        if ports and ws_server:
                            self.afirma_ports = ports
                            # Arrancar nuevo servidor WebSocket en los puertos
                            self.ws_server = server.start_server_thread(
                                ports, self.on_ws_event
                            )
                            # También servidor HTTP (JSSocket)
                            if start_http_server_thread:
                                self.http_server = start_http_server_thread(
                                    ports, self.on_ws_event,
                                    op_handler=self.process_http_operation,
                                )
                            self.log_event(
                                "event",
                                f"Petición redirigida: nuevos servidores en puertos {ports}",
                            )
        except Exception:
            pass

        # Revisar de nuevo en 1 segundo
        self.after(1000, self.check_redirect_file)

    # =======================================================================
    # Manejo de operaciones desde el navegador
    # =======================================================================

    def handle_save_operation(self, message):
        """
        Procesa una operación de guardado (afirma://save) recibida del
        navegador.

        Parsea la URL, extrae los datos binarios (PDF), muestra un diálogo
        para que el usuario elija dónde guardar, y escribe el archivo.

        Soporta dos modos de recepción de datos:
          1. Bytes crudos desde mensaje WebSocket binario (tuple).
          2. Texto con datos en base64 URL-safe desde mensaje de texto.

        Args:
            message: Puede ser un str con la URL afirma://, o un tuple
                     (url_str, raw_bytes) si los datos vienen en binario.
        """
        from urllib.parse import urlparse, parse_qs

        self.log_event("event", "Operación de guardado solicitada por el navegador")

        # Desempaquetar: el servidor puede pasar una tupla (texto, bytes_crudos)
        # cuando el mensaje WebSocket llegó como trama binaria (PDF crudo).
        raw_dat_bytes = None
        if isinstance(message, tuple):
            message, raw_dat_bytes = message

        try:
            # Parsear SOLO la primera línea para metadatos (op, filename, ext, title)
            first_line = message.split('\n')[0]
            parsed = urlparse(first_line)
            params = parse_qs(parsed.query, encoding='utf-8', errors='replace')

            filename = params.get('filename', ['firma'])[0]
            extension = params.get('ext', ['pdf'])[0]
            title = params.get('title', ['Guardar fichero'])[0]

            # ---- Caso A: Datos binarios crudos (trama WebSocket binaria) ----
            # Esto evita la corrupción de PDF binario al pasar por UTF-8.
            if raw_dat_bytes is not None:
                data = raw_dat_bytes
                self.log_event(
                    "info",
                    f"Datos extraídos directamente de bytes crudos: "
                    f"{len(data)} bytes, comienza por {repr(data[:30])}",
                )
            else:
                # ---- Caso B: Datos en texto (trama WebSocket de texto) ----
                dat_content = self._extract_dat_raw(message)
                if dat_content is None:
                    self.log_event(
                        "error",
                        "Falta el parámetro 'dat' en la operación de guardado",
                    )
                    if self.ws_server:
                        self.ws_server.send_response("SAF_NO_DATA")
                    return

                self.log_event(
                    "info",
                    f"Longitud del texto dat extraído={len(dat_content)}, "
                    f"es_ascii={dat_content.isascii()}, "
                    f"comienza por {repr(dat_content[:30])}",
                )

                # autoscript.js construye la URL con avoidEncoding=true para dat,
                # lo que significa que inserta el valor tal cual, sin encodeURIComponent.
                # - Si Valide pasa base64 estándar, dat es ASCII URL-safe base64.
                # - Si Valide pasa binario crudo, los caracteres tienen codepoints 0-255.

                if dat_content.isascii():
                    # Estrategia 1: base64 URL-safe (A-Z, a-z, 0-9, -, _, =)
                    try:
                        b64 = dat_content.strip().replace('-', '+').replace('_', '/')
                        b64 += '=' * (-len(b64) % 4)
                        data = base64.b64decode(b64)
                        self.log_event(
                            "info", "Descodificado dat como base64 URL-safe"
                        )
                    except Exception as e:
                        self.log_event(
                            "info",
                            f"Falló la decodificación base64 ({e}), tratando como bytes ASCII",
                        )
                        data = dat_content.encode('ascii')
                else:
                    # Estrategia 2: binario crudo sin codificación URL
                    data = self._str_to_bytes(dat_content)
                    self.log_event(
                        "info",
                        "Descodificado dat como binario crudo (caracteres no-ASCII)",
                    )

            # ---- Mostrar diálogo de guardado ----
            default_name = (
                f"{filename}.{extension}"
                if '.' not in filename
                else filename
            )
            filetypes = [
                (f"Archivos {extension.upper()}", f"*.{extension}"),
                ("Todos los archivos", "*.*"),
            ]

            save_path = filedialog.asksaveasfilename(
                title=title,
                initialfile=default_name,
                filetypes=filetypes,
            )

            if not save_path:
                self.log_event("event", "Guardado cancelado por el usuario")
                if self.ws_server:
                    self.ws_server.send_response("CANCEL")
                return

            # Escribir los datos al archivo elegido
            with open(save_path, 'wb') as f:
                f.write(data)

            self.log_event(
                "event", f"Guardados {len(data)} bytes en: {save_path}"
            )
            if self.ws_server:
                self.ws_server.send_response("OK")

        except Exception as e:
            import traceback
            self.log_event(
                "error",
                f"Fallo en la operación de guardado: {e}\n{traceback.format_exc()}",
            )
            if self.ws_server:
                self.ws_server.send_response("SAF_ERROR")

    def _extract_dat_raw(self, message):
        """
        Extrae el valor crudo del parámetro 'dat' de un mensaje afirma://
        que puede ocupar múltiples líneas.

        Busca '&dat=' o '?dat=' y devuelve todo lo que viene a continuación.

        Args:
            message: El mensaje completo (posiblemente multi-línea).

        Returns:
            str or None: El valor de 'dat', o None si no se encuentra.
        """
        for sep in ['&dat=', '?dat=']:
            idx = message.find(sep)
            if idx != -1:
                return message[idx + len(sep):]
        return None

    def _str_to_bytes(self, s):
        """
        Reconstruye bytes binarios desde una cadena Python que se originó
        a partir de datos binarios enviados por una trama de texto WebSocket.

        El viaje completo de los datos es:
          1. Bytes originales (0-255).
          2. JS string: el codepoint del carácter == valor del byte (Latin-1).
          3. Trama de texto WebSocket: JS codifica el string como UTF-8.
          4. Python recibe: la biblioteca websockets decodifica UTF-8 → str.
          5. Este método: codifica de vuelta con latin-1 → bytes originales.

        Para codepoints > 255 (artefactos de decodificación UTF-8 de
        secuencias inválidas), se usa surrogateescape como fallback.

        Args:
            s: Cadena Python potencialmente con datos binarios.

        Returns:
            bytes: Los bytes originales reconstruidos.
        """
        try:
            return s.encode('latin-1')
        except UnicodeEncodeError:
            pass
        try:
            return s.encode('utf-8', errors='surrogateescape')
        except UnicodeEncodeError:
            pass
        # Fallback manual: carácter por carácter
        result = bytearray()
        for c in s:
            cp = ord(c)
            if cp <= 0xFF:
                result.append(cp)
            else:
                result.extend(c.encode('utf-8'))
        return bytes(result)

    def _decode_urlsafe_b64(self, raw):
        """
        Decodifica datos en base64 URL-safe a bytes.

        autoscript.js envía 'dat' como base64 URL-safe (usando - y _
        en lugar de + y /) sin codificación URL. El valor crudo puede
        contener saltos de línea al final.

        Args:
            raw: Cadena base64 URL-safe (posiblemente con whitespace).

        Returns:
            bytes or None: Los bytes decodificados, o None si falla.
        """
        try:
            b64 = raw.strip().replace('-', '+').replace('_', '/')
            b64 += '=' * (-len(b64) % 4)  # Añadir padding si falta
            return base64.b64decode(b64)
        except Exception:
            return None

    def _encode_urlsafe_b64(self, data):
        """
        Codifica bytes como base64 URL-safe (usando - y _ en lugar de + y /).

        autoscript.js espera base64 URL-safe en las respuestas de firma.

        Args:
            data: Datos binarios a codificar.

        Returns:
            str: Representación base64 URL-safe.
        """
        b64 = base64.b64encode(data).decode('utf-8')
        return b64.replace('+', '-').replace('/', '_')

    def handle_sign_operation(self, message):
        """
        Procesa una operación de firma (afirma://sign y variantes)
        recibida del navegador.

        Parsea la URL afirma://, extrae los datos PDF en base64,
        firma con el certificado cargado y envía el resultado
        de vuelta por WebSocket.

        Si la petición no incluye datos (dat= ausente), marca la
        solicitud como pendiente para que el usuario firme manualmente.

        Args:
            message: URL afirma:// completa (str).
        """
        self.log_event("event", "Operación de firma solicitada por el navegador")

        try:
            if parse_afirma_url is None:
                self.log_event("error", "Módulo ws_server no disponible")
                if self.ws_server:
                    self.ws_server.send_response("SAF_ERROR")
                return

            op, params, raw_dat = parse_afirma_url(message)

            if not raw_dat:
                # El navegador envió una petición de firma sin datos incrustados
                # (dat=). Esto ocurre cuando el sitio web espera que la
                # aplicación nativa cargue el archivo por un mecanismo separado
                # (ej. el flujo de Valide).
                # Se marca como pendiente y se permite al usuario firmar
                # manualmente desde la GUI.
                self.pending_sign_request = True
                self.after(
                    0,
                    lambda: self.log_event(
                        "event",
                        "Petición de firma sin datos incrustados. "
                        "Seleccione un PDF, certifique y pulse Firmar Documento "
                        "para completar la operación.",
                    ),
                )
                self.after(
                    0,
                    lambda: self.status_label.configure(
                        text="Pendiente: seleccione PDF y pulse Firmar",
                        text_color="orange",
                    ),
                )
                return

            # Decodificar los datos PDF desde base64 URL-safe
            pdf_data = self._decode_urlsafe_b64(raw_dat)
            if pdf_data is None:
                self.log_event(
                    "error",
                    "No se pudo decodificar 'dat' como base64 URL-safe",
                )
                if self.ws_server:
                    self.ws_server.send_response("SAF_ERROR")
                return

            # Datos incrustados en la petición → flujo automático de firma online
            self.pending_sign_request = False
            self.log_event(
                "info",
                f"Petición de firma: op={op}, "
                f"format={params.get('format', 'N/A')}, "
                f"algorithm={params.get('algorithm', 'N/A')}, "
                f"pdf_size={len(pdf_data)} bytes",
            )

            # Leer contraseña en el hilo principal (tkinter NO es thread-safe)
            password = self.pass_entry.get()
            cert_file = self.cert_file

            # Firmar en hilo secundario para no bloquear el event loop WebSocket
            threading.Thread(
                target=self._perform_online_sign,
                args=(pdf_data, params, op, password, cert_file),
                daemon=True,
            ).start()

        except Exception as e:
            import traceback
            self.log_event(
                "error",
                f"Fallo en operación de firma: {e}\n{traceback.format_exc()}",
            )
            if self.ws_server:
                self.ws_server.send_response("SAF_ERROR")

    def _perform_online_sign(self, pdf_data, params, op, password, cert_file):
        """
        Firma datos PDF recibidos del navegador y envía el resultado
        de vuelta por WebSocket.

        Se ejecuta en un hilo secundario para no bloquear el event loop
        del WebSocket.

        IMPORTANTE: password y cert_file se leen en el hilo PRINCIPAL
        antes de lanzar este hilo, porque tkinter no es thread-safe.

        Soporta múltiples formatos de firma:
          - PAdES (PDF): firma estándar de PDF con endesive.
          - XAdES (XML): firma XML avanzada con xades_signer.
          - CAdES (CMS): firma CMS con endesive.plain + asn1crypto.

        Args:
            pdf_data: Datos binarios del documento a firmar.
            params: Diccionario de parámetros de la URL afirma://.
            op: Tipo de operación ('sign', 'cosign', 'signandsave', etc.).
            password: Contraseña del certificado.
            cert_file: Ruta al archivo de certificado .p12/.pfx.
        """
        try:
            # Validar que hay contraseña
            if not password:
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Error",
                        "Por favor, introduzca la contraseña del certificado.",
                    ),
                )
                if self.ws_server:
                    self.ws_server.send_response("CANCEL")
                return

            # Validar que hay certificado cargado
            if not cert_file or not os.path.exists(cert_file):
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Error",
                        "No se ha cargado ningún certificado. "
                        "Seleccione un archivo .p12/.pfx.",
                    ),
                )
                if self.ws_server:
                    self.ws_server.send_response("CANCEL")
                return

            # Cargar certificado y clave privada
            from signer import load_certificate
            private_key, certificate, additional_certificates = \
                load_certificate(cert_file, password)

            # Determinar el formato de firma solicitado
            fmt = params.get('format', 'PAdES').upper()

            # ================================================================
            # XAdES (XML Advanced Electronic Signature)
            # ================================================================
            if fmt.startswith('XADES'):
                from xades_signer import sign_xades

                signed_data = sign_xades(pdf_data, private_key, certificate)
                b64_urlsafe = self._encode_urlsafe_b64(signed_data)
                b64_response = "|" + b64_urlsafe

                self.after(
                    0,
                    lambda d=signed_data, b=b64_urlsafe: self.log_event(
                        "event",
                        f"XAdES firmado y enviado ({len(d)} bytes, "
                        f"{len(b)} chars base64)",
                    ),
                )

                if self.ws_server:
                    self.ws_server.send_response(b64_response)

            # ================================================================
            # CAdES (CMS Advanced Electronic Signature) - BES
            # ================================================================
            elif fmt.startswith('CADES'):
                from endesive import plain
                from asn1crypto import cms

                # Firmar con endesive.plain (produce CMS detached)
                signed_data = plain.sign(
                    pdf_data, private_key, certificate,
                    additional_certificates, hashalgo='sha256', attrs=True,
                )

                # Embeber los datos originales en el CMS
                # (convierte detached → attached/explícito)
                ci = cms.ContentInfo.load(signed_data)
                ci['content']['encap_content_info']['content'] = pdf_data
                signed_data = ci.dump()

                b64_urlsafe = self._encode_urlsafe_b64(signed_data)
                b64_response = "|" + b64_urlsafe

                self.after(
                    0,
                    lambda d=signed_data: self.log_event(
                        "event",
                        f"CAdES firmado y enviado ({len(d)} bytes)",
                    ),
                )

                if self.ws_server:
                    self.ws_server.send_response(b64_response)

            # ================================================================
            # PAdES (PDF Advanced Electronic Signature)
            # ================================================================
            elif fmt.startswith('PADES') or fmt == 'PDF':
                import datetime
                import endesive.pdf.cms

                # Preparar la fecha de firma
                date = datetime.datetime.now(datetime.timezone.utc)
                date_str = date.strftime('D:%Y%m%d%H%M%SZ')

                # Metadatos de la firma PAdES
                dct = {
                    "sigflags": 3,          # 3 = certificación + aprobación
                    "sigpage": 0,           # Página donde va la firma invisible
                    "contact": "",          # Contacto del firmante
                    "location": "",         # Ubicación de la firma
                    "signingdate": date_str,
                    "reason": "Signed with PyFirma",
                }

                # Firmar el PDF
                signature = endesive.pdf.cms.sign(
                    pdf_data, dct, private_key, certificate,
                    additional_certificates, 'sha256',
                )

                # El PDF firmado = datos originales + firma CMS
                signed_data = pdf_data + signature
                b64_urlsafe = self._encode_urlsafe_b64(signed_data)
                b64_response = "|" + b64_urlsafe

                self.after(
                    0,
                    lambda d=signed_data, b=b64_urlsafe: self.log_event(
                        "event",
                        f"PDF firmado enviado de vuelta ({len(d)} bytes, "
                        f"{len(b)} chars base64)",
                    ),
                )

                # Para signandsave: enviar resultado y mostrar diálogo de guardado
                if op == "signandsave":
                    if self.ws_server:
                        self.ws_server.send_response(b64_response)
                    self.after(
                        0,
                        lambda: self._trigger_save_dialog(
                            signed_data,
                            params.get('filename', 'firma'),
                            'pdf',
                        ),
                    )
                    return

                if self.ws_server:
                    self.ws_server.send_response(b64_response)

            else:
                # Formato de firma no reconocido
                self.after(
                    0,
                    lambda: self.log_event(
                        "error",
                        f"Formato de firma desconocido: {fmt}",
                    ),
                )
                if self.ws_server:
                    self.ws_server.send_response(
                        f"SAF_ERROR:Unknown format {fmt}"
                    )
                return

        except ValueError as e:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Error",
                    f"Contraseña incorrecta o certificado inválido: {e}",
                ),
            )
            if self.ws_server:
                self.ws_server.send_response("CANCEL")
        except Exception as e:
            import traceback
            self.after(
                0,
                lambda: self.log_event(
                    "error",
                    f"Fallo en firma online: {e}\n{traceback.format_exc()}",
                ),
            )
            if self.ws_server:
                self.ws_server.send_response(f"SAF_ERROR:{e}")

    def _trigger_save_dialog(self, data, filename, extension):
        """
        Muestra un diálogo de guardado para datos firmados.

        Usado por el flujo 'signandsave', que firma y guarda en un solo paso.

        Args:
            data: Datos binarios a guardar.
            filename: Nombre de archivo sugerido (sin extensión).
            extension: Extensión del archivo (ej. 'pdf').
        """
        default_name = (
            f"{filename}.{extension}"
            if '.' not in filename
            else filename
        )
        filetypes = [
            (f"Archivos {extension.upper()}", f"*.{extension}"),
            ("Todos los archivos", "*.*"),
        ]
        save_path = filedialog.asksaveasfilename(
            title="Guardar documento firmado",
            initialfile=default_name,
            filetypes=filetypes,
        )
        if save_path:
            with open(save_path, 'wb') as f:
                f.write(data)
            self.log_event(
                "event", f"Guardados {len(data)} bytes en: {save_path}"
            )
        else:
            self.log_event("event", "Guardado cancelado por el usuario")

    # =======================================================================
    # Visor de logs
    # =======================================================================

    def log_event(self, event_type, message):
        """
        Añade una entrada al visor de logs de la interfaz.

        Formatea el mensaje según el tipo de evento y lo inserta
        en el textbox de logs. Si no hay visor de logs (modo normal),
        no hace nada.

        Args:
            event_type: Tipo de evento ('info', 'error', 'event',
                        'message', 'param', 'save_operation',
                        'sign_operation').
            message: Contenido del mensaje a registrar.
        """
        # Las operaciones de firma y guardado se manejan aparte
        if event_type in ("save_operation", "sign_operation"):
            return
        if not hasattr(self, 'logs_textbox'):
            return

        self.logs_textbox.configure(state="normal")

        if event_type == "message":
            try:
                if isinstance(message, bytes):
                    message = message.decode('utf-8')

                if message.startswith("afirma://"):
                    # Formatear URL afirma:// con sus parámetros
                    self.logs_textbox.insert(
                        "end", f"> [URL de carga útil WS]: {message}\n"
                    )
                    parsed = urlparse(message)
                    params = parse_qs(parsed.query)
                    self.logs_textbox.insert(
                        "end", "  [Parámetros analizados]:\n"
                    )
                    for k, v in params.items():
                        val = v[0]
                        # Intentar decodificar parámetros en base64
                        try:
                            decoded = base64.b64decode(val).decode('utf-8')
                            self.logs_textbox.insert(
                                "end",
                                f"    - {k}: {decoded} (decodificado b64)\n",
                            )
                        except Exception:
                            self.logs_textbox.insert(
                                "end", f"    - {k}: {val}\n"
                            )
                    self.logs_textbox.insert("end", "\n")
                else:
                    # Intentar parsear como JSON para formatearlo
                    data = json.loads(message)
                    formatted = json.dumps(data, indent=2)
                    self.logs_textbox.insert(
                        "end",
                        f"> [Carga útil WS recibida]:\n{formatted}\n\n",
                    )
            except Exception:
                self.logs_textbox.insert(
                    "end", f"> [Carga útil WS recibida]: {message}\n\n"
                )
        else:
            # Eventos simples: tipo + mensaje
            self.logs_textbox.insert(
                "end", f"* [{event_type.upper()}] {message}\n"
            )

        # Auto-scroll al final
        self.logs_textbox.see("end")
        self.logs_textbox.configure(state="disabled")

    # =======================================================================
    # Procesamiento HTTP síncrono (modo JSSocket)
    # =======================================================================

    def process_http_operation(self, op, url):
        """
        Procesa una operación afirma:// de forma síncrona para el modo
        HTTP (JSSocket).

        Se llama desde el hilo del servidor HTTP. Devuelve bytes o string
        como resultado, que el servidor HTTP codifica en base64 y envía
        al navegador.

        NOTA: _http_password y _http_cert_file se mantienen sincronizados
        desde el hilo principal (mediante el binding KeyRelease y
        select_cert), por lo que no se accede a tkinter aquí.

        Args:
            op: Tipo de operación ('sign', 'save', 'cosign', etc.).
            url: La URL afirma:// completa.

        Returns:
            str or bytes: El resultado de la operación:
                          - "|" + base64_urlsafe para firmas.
                          - "OK" para guardados exitosos.
                          - "SAF_ERROR:..." para errores.
        """
        import urllib.parse

        # Función auxiliar para depuración en archivo
        def _dbg(msg):
            try:
                with open("/tmp/pyfirma_http.log", "a") as f:
                    f.write(f"[PROC_HTTP] {msg}\n")
            except Exception:
                pass

        _dbg(f"START op={op} url_len={len(url)}")

        # En modo JSSocket, 'dat' SÍ está codificado en URL.
        raw_dat = None
        try:
            parsed = urllib.parse.urlparse(url.split('\n')[0])
            qs_params = urllib.parse.parse_qs(parsed.query)
            if 'dat' in qs_params:
                raw_dat = qs_params['dat'][0]
            _dbg(f"parse_qs dat: {raw_dat[:80] if raw_dat else 'None'}...")
        except Exception as e:
            _dbg(f"parse_qs exception: {e}")
            # Fallback: extraer 'dat' manualmente
            for prefix in ('&dat=', '?dat='):
                idx = url.find(prefix)
                if idx != -1:
                    raw_dat = url[idx + len(prefix):]
                    break

        data_to_sign = None

        if raw_dat:
            if raw_dat.startswith('http://') or raw_dat.startswith('https://'):
                # 'dat' es una URL → descargar el documento
                _dbg(f"dat is URL, downloading...")
                try:
                    import urllib.request
                    with urllib.request.urlopen(raw_dat, timeout=30) as resp:
                        data_to_sign = resp.read()
                    _dbg(f"downloaded {len(data_to_sign)} bytes")
                except Exception as e:
                    _dbg(f"download failed: {e}")
                    return f"SAF_ERROR:Download failed: {e}"
            else:
                # 'dat' es base64 URL-safe del documento
                _dbg(f"decoding dat as base64...")
                data_to_sign = self._decode_urlsafe_b64(raw_dat)
                _dbg(
                    f"decoded: {len(data_to_sign) if data_to_sign else 'None'} bytes"
                )
                if data_to_sign is None:
                    return "SAF_ERROR:Failed to decode dat"
        else:
            _dbg("raw_dat is None")

        _dbg(
            f"data_to_sign={len(data_to_sign) if data_to_sign else 'None'}, "
            f"cert={bool(self._http_cert_file)}, pwd_len={len(self._http_password)}"
        )

        # --- Operación de guardado ---
        if op == 'save':
            if data_to_sign:
                # Mostrar diálogo de guardado en el hilo principal
                self.after(
                    0,
                    lambda: self._trigger_save_dialog(
                        data_to_sign, 'firma', 'pdf'
                    ),
                )
                return "OK"
            return "SAF_NO_DATA"

        # --- Operaciones de firma ---
        if data_to_sign is None:
            self.pending_sign_request = True
            return ""  # El navegador vuelve a preguntar más tarde

        password = self._http_password
        cert_file = self._http_cert_file
        if not password or not cert_file:
            return "SAF_ERROR:No certificate loaded"

        # Cargar certificado
        from signer import load_certificate
        private_key, certificate, additional_certs = \
            load_certificate(cert_file, password)

        # Obtener el formato de la URL
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url.split('\n')[0])
        params = parse_qs(parsed.query)
        fmt = params.get('format', ['PAdES'])[0].upper()

        _dbg(f"signing with fmt='{fmt}'")

        # --- Firmar según formato ---
        if fmt.startswith('XADES'):
            # XAdES: firma XML avanzada
            from xades_signer import sign_xades
            signed_data = sign_xades(data_to_sign, private_key, certificate)

        elif fmt.startswith('CADES'):
            # CAdES-BES: firma CMS con datos embeebidos
            from endesive import plain
            from asn1crypto import cms

            # Firmar (produce CMS detached)
            detached = plain.sign(
                data_to_sign, private_key, certificate,
                additional_certs, hashalgo='sha256', attrs=True,
            )

            # Parsear y embeber los datos en EncapsulatedContentInfo
            ci = cms.ContentInfo.load(detached)
            sd = ci['content']
            eci = sd['encap_content_info']
            eci['content'] = data_to_sign  # Embeber los datos originales
            signed_data = ci.dump()
            _dbg(f"CAdES signed (attached): {len(signed_data)} bytes")

        elif fmt.startswith('PADES') or fmt == 'PDF':
            # PAdES: firma estándar de PDF
            import datetime
            import endesive.pdf.cms

            date = datetime.datetime.now(datetime.timezone.utc)
            dct = {
                "sigflags": 3,
                "sigpage": 0,
                "contact": "",
                "location": "",
                "signingdate": date.strftime('D:%Y%m%d%H%M%SZ'),
                "reason": "Signed with PyFirma",
            }
            sig = endesive.pdf.cms.sign(
                data_to_sign, dct, private_key, certificate,
                additional_certs, 'sha256',
            )
            signed_data = data_to_sign + sig

        else:
            return f"SAF_ERROR:Unsupported format: {fmt}"

        # Codificar en base64 URL-safe y devolver con prefijo '|'
        # (mismo formato que el modo WebSocket)
        b64 = self._encode_urlsafe_b64(signed_data)
        return "|" + b64

    # =======================================================================
    # Eventos de la interfaz gráfica
    # =======================================================================

    def toggle_visible_options(self):
        """
        Habilita o deshabilita las opciones de firma visible según el
        estado del checkbox principal.

        Si se activa "Añadir firma visible", se habilitan:
          - Margen izquierdo vertical.
          - Firmar todas las páginas.
        Si se desactiva, se deshabilitan y se resetean sus valores.
        """
        if self.visible_var.get():
            self.vertical_left_checkbox.configure(state="normal")
            self.all_pages_checkbox.configure(state="normal")
        else:
            self.vertical_left_checkbox.configure(state="disabled")
            self.vertical_left_var.set(False)
            self.all_pages_checkbox.configure(state="disabled")
            self.all_pages_var.set(False)

    def select_file(self):
        """
        Abre un diálogo para seleccionar el archivo PDF a firmar.

        Filtra solo archivos .pdf. Al seleccionar uno, actualiza
        la etiqueta con el nombre del archivo y comprueba si ya
        se puede habilitar el botón de firma.
        """
        filename = filedialog.askopenfilename(
            filetypes=[("Archivos PDF", "*.pdf")]
        )
        if filename:
            self.input_file = filename
            self.file_path_label.configure(
                text=os.path.basename(filename), text_color="white"
            )
            self.check_ready()

    def select_cert(self):
        """
        Abre un diálogo para seleccionar el archivo de certificado.

        Filtra archivos .p12 y .pfx. Al seleccionar uno, actualiza
        la etiqueta, guarda la configuración y comprueba si ya se
        puede habilitar el botón de firma.
        """
        filename = filedialog.askopenfilename(
            filetypes=[("Archivos de Certificado", "*.p12 *.pfx")]
        )
        if filename:
            self.cert_file = filename
            self._http_cert_file = filename  # Sincronizar para manejador HTTP
            self.cert_path_label.configure(
                text=os.path.basename(filename), text_color="white"
            )
            self.save_config()
            self.check_ready()

    def _on_password_changed(self):
        """
        Mantiene sincronizada la variable thread-safe de contraseña
        con el contenido del campo de entrada.

        Se llama en cada pulsación de tecla (KeyRelease) para que
        el manejador HTTP siempre tenga la contraseña actualizada.
        """
        self._http_password = self.pass_entry.get()

    def on_cache_pass_toggle(self):
        """
        Guarda o limpia la contraseña en caché cuando se marca/desmarca
        el checkbox de 'Recordar contraseña'.
        """
        self.save_config()

    def check_ready(self):
        """
        Habilita el botón de firma si se han seleccionado tanto
        un PDF como un certificado.
        """
        if self.input_file and self.cert_file:
            self.sign_button.configure(state="normal")

    # =======================================================================
    # Flujo de firma manual (GUI)
    # =======================================================================

    def start_signing(self):
        """
        Inicia el proceso de firma manual desde la GUI.

        Valida que haya contraseña, deshabilita el botón de firma,
        muestra el estado 'Firmando...' y lanza la firma en un hilo
        secundario para no congelar la interfaz.
        """
        password = self.pass_entry.get()
        if not password:
            messagebox.showerror(
                "Error",
                "Por favor, introduzca la contraseña del certificado.",
            )
            return

        # Deshabilitar botón y mostrar progreso
        self.sign_button.configure(state="disabled")
        self.status_label.configure(text="Firmando...", text_color="orange")
        self.update()

        # Leer opciones de firma visible
        visible = self.visible_var.get()
        vertical_left = self.vertical_left_var.get()
        all_pages = self.all_pages_var.get()

        # Ejecutar en hilo secundario para no congelar la UI
        threading.Thread(
            target=self.perform_signing,
            args=(password, visible, vertical_left, all_pages),
            daemon=True,
        ).start()

    def perform_signing(self, password, visible, vertical_left, all_pages):
        """
        Ejecuta la firma del PDF en un hilo secundario.

        Args:
            password: Contraseña del certificado.
            visible: Si se añade marca de agua visible.
            vertical_left: Si la marca va en el margen izquierdo.
            all_pages: Si la marca se aplica a todas las páginas.
        """
        try:
            # Generar nombre de archivo de salida: <original>_signed.pdf
            base, ext = os.path.splitext(self.input_file)
            output_file = f"{base}_signed{ext}"

            sign_pdf(
                self.input_file, self.cert_file, password, output_file,
                visible=visible,
                vertical_left=vertical_left,
                all_pages=all_pages,
            )

            # Notificar éxito en el hilo principal
            self.after(0, lambda: self.signing_success(output_file))
        except Exception as e:
            error_msg = str(e)
            # Notificar error en el hilo principal
            self.after(0, lambda: self.signing_error(error_msg))

    def signing_success(self, output_file):
        """
        Maneja la finalización exitosa de la firma.

        Muestra un mensaje de éxito, restaura el botón de firma y,
        si hay una petición pendiente del navegador (modo interceptor
        sin datos incrustados), envía el resultado por WebSocket.

        Args:
            output_file: Ruta del archivo PDF firmado.
        """
        messagebox.showinfo(
            "Éxito",
            f"¡Archivo firmado con éxito!\nGuardado como: {os.path.basename(output_file)}",
        )
        self.status_label.configure(
            text="Finalizado con éxito", text_color="green"
        )
        self.sign_button.configure(state="normal")

        # Decidir si enviar el resultado por WebSocket.
        # En modo interceptor (self.afirma_url), solo se envía si hay
        # una petición de firma del navegador pendiente (es decir, el
        # sitio web envió 'sign' sin datos incrustados).
        # Si los datos venían incrustados, _perform_online_sign ya
        # gestionó la respuesta WebSocket directamente.
        should_send_ws = (
            self.ws_server
            and (not self.afirma_url or self.pending_sign_request)
        )
        if should_send_ws:
            try:
                with open(output_file, 'rb') as f:
                    pdf_data = f.read()

                b64 = self._encode_urlsafe_b64(pdf_data)
                # El prefijo '|' imita el formato de Java AutoFirma:
                # signature|certificate. autoscript.js divide por '|':
                #   parte1 → certificate (2º param callback),
                #   parte2 → signature (1er param callback).
                # Con "|b64", los datos firmados llegan como 1er param
                # y se mantienen como texto base64 (sin decodificar
                # a binario), evitando corrupción UTF-8 en la URL de guardado.
                self.ws_server.send_response("|" + b64)
                self.pending_sign_request = False
                self.log_event(
                    "info",
                    "Documento firmado enviado de vuelta por WebSocket.",
                )
            except Exception as e:
                self.log_event(
                    "error", f"Fallo al enviar respuesta WS: {e}"
                )
        elif self.ws_server and self.afirma_url:
            self.log_event(
                "info",
                "Documento firmado localmente. Las peticiones del navegador "
                "con datos incrustados se procesan automáticamente.",
            )

    def signing_error(self, error_msg):
        """
        Maneja un error durante la firma.

        Muestra un mensaje de error y restaura la interfaz.

        Args:
            error_msg: Mensaje descriptivo del error.
        """
        messagebox.showerror("Error de firma", error_msg)
        self.status_label.configure(
            text="Ha ocurrido un error", text_color="red"
        )
        self.sign_button.configure(state="normal")


# ---------------------------------------------------------------------------
# Punto de entrada para pruebas directas
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = App()
    app.mainloop()
