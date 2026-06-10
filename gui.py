import customtkinter
import os
import json
import base64
import tempfile
from urllib.parse import parse_qs, urlparse
from tkinter import filedialog, messagebox
import threading
from signer import sign_pdf

LOCK_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.lock")
REDIRECT_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.redirect")
try:
    import server
    from server import parse_afirma_url
except ImportError:
    server = None
    parse_afirma_url = None

try:
    from http_server import start_http_server_thread
except ImportError:
    start_http_server_thread = None

customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

class App(customtkinter.CTk):
    def __init__(self, afirma_url=None, afirma_ports=None):
        super().__init__()

        self.title("PyFirma")
        self.geometry("700x500")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.afirma_url = afirma_url
        self.afirma_ports = afirma_ports
        self.ws_server = None

        self.input_file = None
        self.cert_file = None
        self.pending_sign_request = False  # True when browser sent sign w/o dat (manual flow)
        self.config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

        # Thread-safe storage for HTTP (JSSocket) handler
        self._http_cert_file = None
        self._http_password = ""

        self.load_config()
        self.setup_ui()
        self.http_server = None
        if self.afirma_url:
            self.setup_interceptor_addons()
            if self.afirma_ports and server:
                self.ws_server = server.start_server_thread(self.afirma_ports, self.on_ws_event)
                # Also start HTTPS server on another port for JSSocket mode
                if start_http_server_thread:
                    self.http_server = start_http_server_thread(
                        self.afirma_ports, self.on_ws_event,
                        op_handler=self.process_http_operation)
            # Poll for redirects from duplicate instance attempts
            self.check_redirect_file()
        
        # Update labels if loaded from config
        if self.cert_file and os.path.exists(self.cert_file):
            self.cert_path_label.configure(text=os.path.basename(self.cert_file), text_color="white")
            self._http_cert_file = self.cert_file  # sync for HTTP handler
            self.check_ready()

    def load_config(self):
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

    def setup_ui(self):
        # Header
        self.header_label = customtkinter.CTkLabel(self, text="PyFirma - Firmador de PDF", font=customtkinter.CTkFont(size=20, weight="bold"))
        self.header_label.pack(pady=20)

        # Main Container
        self.frame = customtkinter.CTkFrame(self)
        self.frame.pack(pady=20, padx=20, fill="both", expand=True)

        # File Section
        self.file_label = customtkinter.CTkLabel(self.frame, text="Documento:")
        self.file_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        
        self.file_path_label = customtkinter.CTkLabel(self.frame, text="Ningún archivo seleccionado", text_color="gray", wraplength=300)
        self.file_path_label.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        
        self.file_button = customtkinter.CTkButton(self.frame, text="Seleccionar PDF", command=self.select_file)
        self.file_button.grid(row=0, column=2, padx=10, pady=10)

        # Certificate Section
        self.cert_label = customtkinter.CTkLabel(self.frame, text="Certificado (.p12):")
        self.cert_label.grid(row=1, column=0, padx=10, pady=10, sticky="w")
        
        self.cert_path_label = customtkinter.CTkLabel(self.frame, text="Ningún certificado seleccionado", text_color="gray", wraplength=300)
        self.cert_path_label.grid(row=1, column=1, padx=10, pady=10, sticky="w")
        
        self.cert_button = customtkinter.CTkButton(self.frame, text="Seleccionar Certificado", command=self.select_cert)
        self.cert_button.grid(row=1, column=2, padx=10, pady=10)

        # Password Section
        self.pass_label = customtkinter.CTkLabel(self.frame, text="Contraseña:")
        self.pass_label.grid(row=2, column=0, padx=10, pady=10, sticky="w")

        self.pass_entry = customtkinter.CTkEntry(self.frame, show="*", width=200)
        self.pass_entry.grid(row=2, column=1, padx=10, pady=10, sticky="w")
        # Pre-fill password from cache
        if self.cached_password:
            self.pass_entry.insert(0, self.cached_password)
        # Auto-sync password to thread-safe variable for HTTP handler
        self._http_password = self.cached_password
        self.pass_entry.bind("<KeyRelease>", lambda e: self._on_password_changed())

        self.cache_pass_var = customtkinter.BooleanVar(value=bool(self.cached_password))
        self.cache_pass_checkbox = customtkinter.CTkCheckBox(
            self.frame, text="Recordar contraseña",
            variable=self.cache_pass_var,
            command=self.on_cache_pass_toggle)
        self.cache_pass_checkbox.grid(row=2, column=2, padx=10, pady=10, sticky="w")

        # Visible Signature Checkbox
        self.visible_var = customtkinter.BooleanVar(value=False)
        self.visible_checkbox = customtkinter.CTkCheckBox(self.frame, text="Añadir firma visible", variable=self.visible_var, command=self.toggle_visible_options)
        self.visible_checkbox.grid(row=3, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        
        # Vertical Left Checkbox (Initially Disabled/Hidden)
        self.vertical_left_var = customtkinter.BooleanVar(value=False)
        self.vertical_left_checkbox = customtkinter.CTkCheckBox(self.frame, text="Margen izquierdo vertical", variable=self.vertical_left_var)
        self.vertical_left_checkbox.grid(row=3, column=2, padx=10, pady=10, sticky="w")
        self.vertical_left_checkbox.configure(state="disabled")
        
        # All Pages Checkbox (Initially Disabled)
        self.all_pages_var = customtkinter.BooleanVar(value=False)
        self.all_pages_checkbox = customtkinter.CTkCheckBox(self.frame, text="Firmar todas las páginas", variable=self.all_pages_var)
        self.all_pages_checkbox.grid(row=4, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        self.all_pages_checkbox.configure(state="disabled")

        # Action Button
        self.sign_button = customtkinter.CTkButton(self, text="Firmar Documento", command=self.start_signing, height=40, font=customtkinter.CTkFont(size=16, weight="bold"), state="disabled")
        self.sign_button.pack(pady=20)
        
        # Status
        self.status_label = customtkinter.CTkLabel(self, text="Listo", text_color="gray")
        self.status_label.pack(side="bottom", pady=10)

    def setup_interceptor_addons(self):
        # Make window bigger for interceptor
        self.geometry("900x700")
        
        self.header_label.configure(text="PyFirma - Interceptor de AutoFirma", text_color="#1E90FF")

        # Use a read-only textbox for the URL so the user can select and copy it
        self.url_frame = customtkinter.CTkFrame(self)
        self.url_frame.pack(pady=5, padx=20, fill="x")
        self.url_label_title = customtkinter.CTkLabel(self.url_frame, text="URL:", text_color="gray")
        self.url_label_title.pack(side="left", padx=(0, 5))
        self.url_textbox = customtkinter.CTkTextbox(self.url_frame, height=28, font=customtkinter.CTkFont(family="Courier", size=11))
        self.url_textbox.pack(side="left", fill="x", expand=True)
        self.url_textbox.insert("1.0", self.afirma_url)
        self.url_textbox.configure(state="disabled")  # read-only but selectable in CTkTextbox

        # Also log the parsed ports prominently
        if self.afirma_ports:
            self.log_event("info", f"Puertos recibidos del navegador: {self.afirma_ports}")
        else:
            self.log_event("error", "¡No se detectaron puertos en la URL! El navegador no podrá conectar.")
        
        self.logs_textbox = customtkinter.CTkTextbox(self, height=200, font=customtkinter.CTkFont(family="Courier", size=12))
        self.logs_textbox.pack(pady=10, padx=20, fill="both", expand=True)
        self.logs_textbox.insert("end", "Esperando conexión del navegador...\n\n")
        self.logs_textbox.configure(state="disabled")

        if not self.afirma_ports:
            parsed = urlparse(self.afirma_url)
            params = parse_qs(parsed.query)
            try:
                self.log_event("info", "No se han recibido puertos WebSocket. Parámetros de la URL:")
                for k, v in params.items():
                    val = v[0]
                    try:
                        decoded = base64.b64decode(val).decode('utf-8')
                        self.log_event("param", f"{k} = {decoded} (decodificado base64)")
                    except Exception:
                        self.log_event("param", f"{k} = {val}")
            except Exception as e:
                self.log_event("error", str(e))

    def on_ws_event(self, event_type, message):
        # Must be called thread-safe
        if event_type == "save_operation":
            self.after(0, lambda m=message: self.handle_save_operation(m))
        elif event_type == "sign_operation":
            self.after(0, lambda m=message: self.handle_sign_operation(m))
        else:
            self.after(0, lambda: self.log_event(event_type, message))

    def on_close(self):
        """Clean up lock file and exit."""
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
        self.destroy()

    def check_redirect_file(self):
        """Periodically check for forwarded afirma:// URLs from duplicate instance attempts.

        When autoscript.js re-launches PyFirma with new random ports (because the
        WebSocket closed), the second main.py process writes the URL to REDIRECT_FILE
        and exits. The original instance picks it up here and starts a new WS server
        on the requested ports.
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
                        ports = [int(p.strip()) for p in params['ports'][0].split(',')
                                 if p.strip().isdigit()]
                        if ports and server:
                            self.afirma_ports = ports
                            self.ws_server = server.start_server_thread(
                                ports, self.on_ws_event)
                            if start_http_server_thread:
                                self.http_server = start_http_server_thread(
                                    ports, self.on_ws_event,
                                    op_handler=self.process_http_operation)
                            self.log_event("event",
                                f"Petición redirigida: nuevos servidores en puertos {ports}")
        except Exception:
            pass
        # Check again in 1 second
        self.after(1000, self.check_redirect_file)

    def handle_save_operation(self, message):
        """Parse afirma://save URL, let user choose where to save, write data, respond OK."""
        from urllib.parse import urlparse, parse_qs

        self.log_event("event", "Operación de guardado solicitada por el navegador")

        # Unpack: server may pass (text, raw_bytes) tuple when the WebSocket
        # message arrived as binary frame (contains raw PDF bytes).
        raw_dat_bytes = None
        if isinstance(message, tuple):
            message, raw_dat_bytes = message

        try:
            # Parse ONLY the first line for metadata (op, filename, ext, title)
            first_line = message.split('\n')[0]
            parsed = urlparse(first_line)
            params = parse_qs(parsed.query, encoding='utf-8', errors='replace')

            filename = params.get('filename', ['firma'])[0]
            extension = params.get('ext', ['pdf'])[0]
            title = params.get('title', ['Guardar fichero'])[0]

            # If we have raw bytes from a binary WebSocket frame, use them directly.
            # This avoids UTF-8 decoding corruption of binary PDF data.
            if raw_dat_bytes is not None:
                data = raw_dat_bytes
                self.log_event("info",
                    f"Datos extraídos directamente de bytes crudos: "
                    f"{len(data)} bytes, comienza por {repr(data[:30])}")
            else:
                # Fall back to text-based extraction (for text WebSocket frames)
                dat_content = self._extract_dat_raw(message)
                if dat_content is None:
                    self.log_event("error", "Falta el parámetro 'dat' en la operación de guardado")
                    if self.ws_server:
                        self.ws_server.send_response("SAF_NO_DATA")
                    return

                self.log_event("info", f"Longitud del texto dat extraído={len(dat_content)}, es_ascii={dat_content.isascii()}, comienza por {repr(dat_content[:30])}")

                # autoscript.js builds the URL with avoidEncoding=true for dat,
                # meaning it inserts the dat value as-is without encodeURIComponent.
                # If Valide passes base64 (standard), dat is ASCII URL-safe base64.
                # If Valide passes raw binary, dat chars have codepoints 0-255.
                if dat_content.isascii():
                    # Strategy 1: URL-safe base64 (A-Z, a-z, 0-9, -, _, =)
                    try:
                        b64 = dat_content.strip().replace('-', '+').replace('_', '/')
                        b64 += '=' * (-len(b64) % 4)
                        data = base64.b64decode(b64)
                        self.log_event("info", "Descodificado dat como base64 URL-safe")
                    except Exception as e:
                        self.log_event("info", f"Falló la decodificación base64 ({e}), tratando como bytes ASCII")
                        data = dat_content.encode('ascii')
                else:
                    # Strategy 2: raw binary embedded without URL-encoding
                    data = self._str_to_bytes(dat_content)
                    self.log_event("info", "Descodificado dat como binario crudo (caracteres no-ASCII)")


            # Build default filename
            default_name = f"{filename}.{extension}" if '.' not in filename else filename
            filetypes = [(f"Archivos {extension.upper()}", f"*.{extension}"), ("Todos los archivos", "*.*")]

            save_path = filedialog.asksaveasfilename(
                title=title,
                initialfile=default_name,
                filetypes=filetypes
            )

            if not save_path:
                self.log_event("event", "Guardado cancelado por el usuario")
                if self.ws_server:
                    self.ws_server.send_response("CANCEL")
                return

            with open(save_path, 'wb') as f:
                f.write(data)

            self.log_event("event", f"Guardados {len(data)} bytes en: {save_path}")
            if self.ws_server:
                self.ws_server.send_response("OK")

        except Exception as e:
            import traceback
            self.log_event("error", f"Fallo en la operación de guardado: {e}\n{traceback.format_exc()}")
            if self.ws_server:
                self.ws_server.send_response("SAF_ERROR")

    def _extract_dat_raw(self, message):
        """Extract raw dat value from a potentially multi-line afirma:// message."""
        for sep in ['&dat=', '?dat=']:
            idx = message.find(sep)
            if idx != -1:
                return message[idx + len(sep):]
        return None

    def _str_to_bytes(self, s):
        """Reconstruct binary bytes from a Python str that originated from binary data
        sent through a WebSocket text frame.

        The roundtrip is:
          1. Original bytes (0-255)
          2. JS string: char codepoint == byte value (Latin-1 mapping)
          3. WebSocket text frame: JS encodes string as UTF-8
          4. Python receives: websockets library auto-decodes UTF-8 → str
          5. This method: encode back with latin-1 → original bytes

        For codepoints > 255 (UTF-8 decoding artifacts from invalid sequences),
        fall back to re-encoding as UTF-8 with surrogateescape.
        """
        try:
            return s.encode('latin-1')
        except UnicodeEncodeError:
            pass
        try:
            return s.encode('utf-8', errors='surrogateescape')
        except UnicodeEncodeError:
            pass
        result = bytearray()
        for c in s:
            cp = ord(c)
            if cp <= 0xFF:
                result.append(cp)
            else:
                result.extend(c.encode('utf-8'))
        return bytes(result)

    def _decode_urlsafe_b64(self, raw):
        """Decode URL-safe base64 data to bytes.

        autoscript.js sends dat as URL-safe base64 (using - and _ instead of + and /)
        without URL-encoding. The raw value may contain trailing newlines.
        """
        try:
            b64 = raw.strip().replace('-', '+').replace('_', '/')
            b64 += '=' * (-len(b64) % 4)
            return base64.b64decode(b64)
        except Exception:
            return None

    def _encode_urlsafe_b64(self, data):
        """Encode bytes as URL-safe base64 (using - and _ instead of + and /).

        autoscript.js expects URL-safe base64 in sign responses.
        """
        b64 = base64.b64encode(data).decode('utf-8')
        return b64.replace('+', '-').replace('/', '_')

    def handle_sign_operation(self, message):
        """Handle afirma://sign (and cosign/countersign/signandsave) from the browser.

        Parses the afirm:// URL, extracts the base64 PDF data, signs it with the
        loaded certificate, and sends the signed PDF back via WebSocket.
        """
        self.log_event("event", "Operación de firma solicitada por el navegador")

        try:
            if parse_afirma_url is None:
                self.log_event("error", "Módulo server no disponible")
                if self.ws_server:
                    self.ws_server.send_response("SAF_ERROR")
                return

            op, params, raw_dat = parse_afirma_url(message)

            if not raw_dat:
                # The browser sent a sign request without embedded data (dat=).
                # This happens when the website expects the native app to load the
                # file via a separate mechanism (e.g., Valide's flow).
                # Mark the request as pending and let the user sign manually via the GUI.
                self.pending_sign_request = True
                self.after(0, lambda: self.log_event(
                    "event",
                    "Petición de firma sin datos incrustados. "
                    "Seleccione un PDF, certifique y pulse Firmar Documento "
                    "para completar la operación."))
                self.after(0, lambda: self.status_label.configure(
                    text="Pendiente: seleccione PDF y pulse Firmar", text_color="orange"))
                return

            pdf_data = self._decode_urlsafe_b64(raw_dat)
            if pdf_data is None:
                self.log_event("error", "No se pudo decodificar 'dat' como base64 URL-safe")
                if self.ws_server:
                    self.ws_server.send_response("SAF_ERROR")
                return

            # Data embedded in request → automatic online signing flow
            self.pending_sign_request = False
            self.log_event("info",
                f"Petición de firma: op={op}, "
                f"format={params.get('format', 'N/A')}, "
                f"algorithm={params.get('algorithm', 'N/A')}, "
                f"pdf_size={len(pdf_data)} bytes")

            # Read password on main thread (tkinter is NOT thread-safe)
            password = self.pass_entry.get()
            cert_file = self.cert_file

            # Sign in background thread to not block the WebSocket event loop
            threading.Thread(
                target=self._perform_online_sign,
                args=(pdf_data, params, op, password, cert_file),
                daemon=True
            ).start()

        except Exception as e:
            import traceback
            self.log_event("error", f"Fallo en operación de firma: {e}\n{traceback.format_exc()}")
            if self.ws_server:
                self.ws_server.send_response("SAF_ERROR")

    def _perform_online_sign(self, pdf_data, params, op, password, cert_file):
        """Sign PDF data received from the browser and send back via WebSocket.

        This runs in a background thread to avoid blocking the WebSocket event loop.
        IMPORTANT: password and cert_file are read on the MAIN thread before
        spawning this thread, because tkinter is not thread-safe.
        """
        try:
            if not password:
                self.after(0, lambda: messagebox.showerror(
                    "Error", "Por favor, introduzca la contraseña del certificado."))
                if self.ws_server:
                    self.ws_server.send_response("CANCEL")
                return

            if not cert_file or not os.path.exists(cert_file):
                self.after(0, lambda: messagebox.showerror(
                    "Error", "No se ha cargado ningún certificado. Seleccione un archivo .p12/.pfx."))
                if self.ws_server:
                    self.ws_server.send_response("CANCEL")
                return

            # Load certificate
            from signer import load_certificate
            private_key, certificate, additional_certificates = load_certificate(
                cert_file, password
            )

            fmt = params.get('format', 'PAdES').upper()

            if fmt.startswith('XADES'):
                # --- XAdES (XML) signing ---
                from xades_signer import sign_xades

                signed_data = sign_xades(pdf_data, private_key, certificate)
                b64_urlsafe = self._encode_urlsafe_b64(signed_data)
                b64_response = "|" + b64_urlsafe

                self.after(0, lambda d=signed_data, b=b64_urlsafe: self.log_event(
                    "event",
                    f"XAdES firmado y enviado ({len(d)} bytes, "
                    f"{len(b)} chars base64)"))

                if self.ws_server:
                    self.ws_server.send_response(b64_response)

            elif fmt.startswith('CADES'):
                # --- CAdES-BES via endesive.plain ---
                from endesive import plain
                from asn1crypto import cms
                signed_data = plain.sign(
                    pdf_data, private_key, certificate,
                    additional_certificates, hashalgo='sha256', attrs=True)
                # Embed data (converts detached → attached/explicit)
                ci = cms.ContentInfo.load(signed_data)
                ci['content']['encap_content_info']['content'] = pdf_data
                signed_data = ci.dump()

                b64_urlsafe = self._encode_urlsafe_b64(signed_data)
                b64_response = "|" + b64_urlsafe

                self.after(0, lambda d=signed_data: self.log_event(
                    "event", f"CAdES firmado y enviado ({len(d)} bytes)"))

                if self.ws_server:
                    self.ws_server.send_response(b64_response)

            elif fmt.startswith('PADES') or fmt == 'PDF':
                # --- PAdES (PDF) signing ---
                import datetime
                import endesive.pdf.cms

                date = datetime.datetime.now(datetime.timezone.utc)
                date_str = date.strftime('D:%Y%m%d%H%M%SZ')

                dct = {
                    "sigflags": 3,
                    "sigpage": 0,
                    "contact": "",
                    "location": "",
                    "signingdate": date_str,
                    "reason": "Signed with PyFirma",
                }

                signature = endesive.pdf.cms.sign(
                    pdf_data, dct, private_key, certificate,
                    additional_certificates, 'sha256'
                )

                signed_data = pdf_data + signature
                b64_urlsafe = self._encode_urlsafe_b64(signed_data)
                b64_response = "|" + b64_urlsafe

                self.after(0, lambda d=signed_data, b=b64_urlsafe: self.log_event(
                    "event",
                    f"PDF firmado enviado de vuelta ({len(d)} bytes, "
                    f"{len(b)} chars base64)"))

                # Handle signandsave for PAdES
                if op == "signandsave":
                    if self.ws_server:
                        self.ws_server.send_response(b64_response)
                    self.after(0, lambda: self._trigger_save_dialog(
                        signed_data,
                        params.get('filename', 'firma'),
                        'pdf'))
                    return

                if self.ws_server:
                    self.ws_server.send_response(b64_response)

            elif fmt == 'CADES':
                # --- CAdES (CMS) signing ---
                self.after(0, lambda: self.log_event(
                    "error", "CAdES no implementado aún"))
                if self.ws_server:
                    self.ws_server.send_response("SAF_ERROR:CAdES not yet implemented")
                return

            else:
                self.after(0, lambda: self.log_event(
                    "error", f"Formato de firma desconocido: {fmt}"))
                if self.ws_server:
                    self.ws_server.send_response(f"SAF_ERROR:Unknown format {fmt}")
                return

        except ValueError as e:
            self.after(0, lambda: messagebox.showerror(
                "Error", f"Contraseña incorrecta o certificado inválido: {e}"))
            if self.ws_server:
                self.ws_server.send_response("CANCEL")
        except Exception as e:
            import traceback
            self.after(0, lambda: self.log_event(
                "error", f"Fallo en firma online: {e}\n{traceback.format_exc()}"))
            if self.ws_server:
                self.ws_server.send_response(f"SAF_ERROR:{e}")

    def _trigger_save_dialog(self, data, filename, extension):
        """Show a save dialog for signed data (used by signandsave flow)."""
        default_name = f"{filename}.{extension}" if '.' not in filename else filename
        filetypes = [
            (f"Archivos {extension.upper()}", f"*.{extension}"),
            ("Todos los archivos", "*.*")
        ]
        save_path = filedialog.asksaveasfilename(
            title="Guardar documento firmado",
            initialfile=default_name,
            filetypes=filetypes
        )
        if save_path:
            with open(save_path, 'wb') as f:
                f.write(data)
            self.log_event("event", f"Guardados {len(data)} bytes en: {save_path}")
        else:
            self.log_event("event", "Guardado cancelado por el usuario")

    def log_event(self, event_type, message):
        if event_type in ("save_operation", "sign_operation"):
            return  # These are handled separately
        if not hasattr(self, 'logs_textbox'):
            return
            
        self.logs_textbox.configure(state="normal")
        
        if event_type == "message":
            try:
                if isinstance(message, bytes):
                    message = message.decode('utf-8')
                
                if message.startswith("afirma://"):
                    self.logs_textbox.insert("end", f"> [URL de carga útil WS]: {message}\n")
                    parsed = urlparse(message)
                    params = parse_qs(parsed.query)
                    self.logs_textbox.insert("end", "  [Parámetros analizados]:\n")
                    for k, v in params.items():
                        val = v[0]
                        # Try to base64 decode parameters like properties or ksb64
                        try:
                            decoded = base64.b64decode(val).decode('utf-8')
                            self.logs_textbox.insert("end", f"    - {k}: {decoded} (decodificado b64)\n")
                        except Exception:
                            self.logs_textbox.insert("end", f"    - {k}: {val}\n")
                    self.logs_textbox.insert("end", "\n")
                else:
                    data = json.loads(message)
                    formatted = json.dumps(data, indent=2)
                    self.logs_textbox.insert("end", f"> [Carga útil WS recibida]:\n{formatted}\n\n")
            except Exception:
                self.logs_textbox.insert("end", f"> [Carga útil WS recibida]: {message}\n\n")
        else:
            self.logs_textbox.insert("end", f"* [{event_type.upper()}] {message}\n")
            
        self.logs_textbox.see("end")
        self.logs_textbox.configure(state="disabled")


    def toggle_visible_options(self):
        if self.visible_var.get():
            self.vertical_left_checkbox.configure(state="normal")
            self.all_pages_checkbox.configure(state="normal")
        else:
            self.vertical_left_checkbox.configure(state="disabled")
            self.vertical_left_var.set(False)
            self.all_pages_checkbox.configure(state="disabled")
            self.all_pages_var.set(False)

    def select_file(self):
        filename = filedialog.askopenfilename(filetypes=[("Archivos PDF", "*.pdf")])
        if filename:
            self.input_file = filename
            self.file_path_label.configure(text=os.path.basename(filename), text_color="white")
            self.check_ready()

    def select_cert(self):
        filename = filedialog.askopenfilename(filetypes=[("Archivos de Certificado", "*.p12 *.pfx")])
        if filename:
            self.cert_file = filename
            self._http_cert_file = filename  # sync for HTTP handler
            self.cert_path_label.configure(text=os.path.basename(filename), text_color="white")
            self.save_config()
            self.check_ready()

    def _on_password_changed(self):
        """Keep thread-safe password var in sync with the entry widget."""
        self._http_password = self.pass_entry.get()

    def process_http_operation(self, op, url):
        """Process an afirma:// operation synchronously for HTTP (JSSocket) mode.

        Called from the HTTP server thread. Returns bytes or string result.
        NOTE: _http_password and _http_cert_file are kept in sync from the main
        thread (via KeyRelease binding and select_cert), so no tkinter calls here.
        """
        import urllib.parse

        def _dbg(msg):
            try:
                with open("/tmp/pyfirma_http.log", "a") as f:
                    f.write(f"[PROC_HTTP] {msg}\n")
            except Exception:
                pass

        _dbg(f"START op={op} url_len={len(url)}")

        # In JSSocket mode, dat IS URL-encoded.
        raw_dat = None
        try:
            parsed = urllib.parse.urlparse(url.split('\n')[0])
            qs_params = urllib.parse.parse_qs(parsed.query)
            if 'dat' in qs_params:
                raw_dat = qs_params['dat'][0]
            _dbg(f"parse_qs dat: {raw_dat[:80] if raw_dat else 'None'}...")
        except Exception as e:
            _dbg(f"parse_qs exception: {e}")
            for prefix in ('&dat=', '?dat='):
                idx = url.find(prefix)
                if idx != -1:
                    raw_dat = url[idx + len(prefix):]
                    break

        data_to_sign = None

        if raw_dat:
            if raw_dat.startswith('http://') or raw_dat.startswith('https://'):
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
                _dbg(f"decoding dat as base64...")
                data_to_sign = self._decode_urlsafe_b64(raw_dat)
                _dbg(f"decoded: {len(data_to_sign) if data_to_sign else 'None'} bytes")
                if data_to_sign is None:
                    return "SAF_ERROR:Failed to decode dat"
        else:
            _dbg("raw_dat is None")

        _dbg(f"data_to_sign={len(data_to_sign) if data_to_sign else 'None'}, "
             f"cert={bool(self._http_cert_file)}, pwd_len={len(self._http_password)}")

        if op == 'save':
            if data_to_sign:
                self.after(0, lambda: self._trigger_save_dialog(
                    data_to_sign, 'firma', 'pdf'))
                return "OK"
            return "SAF_NO_DATA"

        # Sign operations
        if data_to_sign is None:
            self.pending_sign_request = True
            return ""

        password = self._http_password
        cert_file = self._http_cert_file
        if not password or not cert_file:
            return "SAF_ERROR:No certificate loaded"

        # Sign based on format
        from signer import load_certificate
        private_key, certificate, additional_certs = load_certificate(
            cert_file, password
        )

        # Get format from URL
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(url.split('\n')[0])
        params = parse_qs(parsed.query)
        fmt = params.get('format', ['PAdES'])[0].upper()

        _dbg(f"signing with fmt='{fmt}'")

        if fmt.startswith('XADES'):
            from xades_signer import sign_xades
            signed_data = sign_xades(data_to_sign, private_key, certificate)

        elif fmt.startswith('CADES'):
            # CAdES-BES via endesive.plain (produces detached CMS).
            # Post-process to embed the data (attached/explicit mode).
            from endesive import plain
            from asn1crypto import cms

            detached = plain.sign(
                data_to_sign, private_key, certificate,
                additional_certs, hashalgo='sha256', attrs=True)

            # Parse and embed the data in EncapsulatedContentInfo
            ci = cms.ContentInfo.load(detached)
            sd = ci['content']
            eci = sd['encap_content_info']
            # Set the content field to embed the original data
            eci['content'] = data_to_sign
            signed_data = ci.dump()
            _dbg(f"CAdES signed (attached): {len(signed_data)} bytes")

        elif fmt.startswith('PADES') or fmt == 'PDF':
            import datetime
            import endesive.pdf.cms
            date = datetime.datetime.now(datetime.timezone.utc)
            dct = {
                "sigflags": 3, "sigpage": 0,
                "contact": "", "location": "",
                "signingdate": date.strftime('D:%Y%m%d%H%M%SZ'),
                "reason": "Signed with PyFirma",
            }
            sig = endesive.pdf.cms.sign(
                data_to_sign, dct, private_key, certificate, additional_certs, 'sha256'
            )
            signed_data = data_to_sign + sig

        else:
            return f"SAF_ERROR:Unsupported format: {fmt}"

        # Encode and return with '|' prefix (same as WSS mode)
        b64 = self._encode_urlsafe_b64(signed_data)
        return "|" + b64

    def on_cache_pass_toggle(self):
        """Save or clear cached password when checkbox is toggled."""
        self.save_config()

    def check_ready(self):
        if self.input_file and self.cert_file:
            self.sign_button.configure(state="normal")

    def start_signing(self):
        password = self.pass_entry.get()
        if not password:
            messagebox.showerror("Error", "Por favor, introduzca la contraseña del certificado.")
            return

        self.sign_button.configure(state="disabled")
        self.status_label.configure(text="Firmando...", text_color="orange")
        self.update()

        visible = self.visible_var.get()
        vertical_left = self.vertical_left_var.get()
        all_pages = self.all_pages_var.get()

        # Run in thread to not freeze UI
        threading.Thread(target=self.perform_signing, args=(password, visible, vertical_left, all_pages), daemon=True).start()

    def perform_signing(self, password, visible, vertical_left, all_pages):
        try:
            base, ext = os.path.splitext(self.input_file)
            output_file = f"{base}_signed{ext}"
            
            sign_pdf(self.input_file, self.cert_file, password, output_file, visible=visible, vertical_left=vertical_left, all_pages=all_pages)
            
            self.after(0, lambda: self.signing_success(output_file))
        except Exception as e:
            error_msg = str(e)
            self.after(0, lambda: self.signing_error(error_msg))

    def signing_success(self, output_file):
        messagebox.showinfo("Éxito", f"¡Archivo firmado con éxito!\nGuardado como: {os.path.basename(output_file)}")
        self.status_label.configure(text="Finalizado con éxito", text_color="green")
        self.sign_button.configure(state="normal")

        # Decide whether to send the result via WebSocket.
        # In interceptor mode (self.afirma_url), only send if a browser sign request
        # is pending (i.e., the website sent sign without embedded data).
        # If data was embedded, _perform_online_sign handles the WS response directly.
        should_send_ws = (
            self.ws_server and
            (not self.afirma_url or self.pending_sign_request)
        )
        if should_send_ws:
            try:
                with open(output_file, 'rb') as f:
                    pdf_data = f.read()

                b64 = self._encode_urlsafe_b64(pdf_data)
                # Prepend '|' to mimic Java AutoFirma's signature|certificate format.
                # autoscript.js splits by '|': part1→certificate(2nd cb param),
                # part2→signature(1st cb param). With "|b64", the signed data
                # arrives as the 1st callback param, and stays as base64 text
                # (not decoded to binary), avoiding UTF-8 corruption in the save URL.
                self.ws_server.send_response("|" + b64)
                self.pending_sign_request = False
                self.log_event("info", "Documento firmado enviado de vuelta por WebSocket.")
            except Exception as e:
                self.log_event("error", f"Fallo al enviar respuesta WS: {e}")
        elif self.ws_server and self.afirma_url:
            self.log_event("info",
                "Documento firmado localmente. Las peticiones del navegador con "
                "datos incrustados se procesan automáticamente.")

    def signing_error(self, error_msg):
        messagebox.showerror("Error de firma", error_msg)
        self.status_label.configure(text="Ha ocurrido un error", text_color="red")
        self.sign_button.configure(state="normal")

if __name__ == "__main__":
    app = App()
    app.mainloop()
