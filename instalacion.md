# Instalación y configuración de PyFirma en Ubuntu 24.04

Guía para instalar (manualmente) PyFirma en Ubuntu 24.04 y configurarlo
como aplicación nativa de firma electrónica compatible con el
protocolo `afirma://` en Firefox y Chrome/Chromium.

---

## 1. Requisitos del sistema

- Ubuntu 24.04 LTS (Noble Numbat)
- Python 3.12 (incluido por defecto)
- Certificado digital PKCS#12 (`.p12` o `.pfx`)
- Conexión a Internet para instalar dependencias pip

---

## 2. Instalación de dependencias del sistema

```bash
sudo apt update
sudo apt install -y python3-tk python3-venv python3-pip
```

| Paquete | Propósito |
|---|---|
| `python3-tk` | Soporte de Tkinter para la interfaz gráfica (customtkinter) |
| `python3-venv` | Entorno virtual de Python |
| `python3-pip` | Gestor de paquetes de Python |

---

## 3. Descarga y preparación de un entorno virtual

```bash
# Clonar o copiar el proyecto en ~/pyfirma
git clone <repositorio> ~/pyfirma
cd ~/pyfirma

# Crear y activar entorno virtual
python3 -m venv venv
source venv/bin/activate

# Instalar dependencias Python
pip install -r requirements.txt
```

Contenido de `requirements.txt`:
```
customtkinter
endesive
cryptography
Pillow
pypdf
reportlab
websockets
```

**Nota:** `lxml` y `asn1crypto` son dependencias transitivas que se
instalan automáticamente al instalar `endesive`.

---

## 4. Certificados TLS para WSS/HTTPS

El navegador exige TLS (`wss://` y `https://`) para conectar con
PyFirma. Hay que generar un certificado autofirmado:

```bash
cd ~/pyfirma
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 3650 -nodes \
  -subj "/CN=localhost/O=PyFirma/OU=AutoFirma" \
  -addext "subjectAltName=IP:127.0.0.1"
```

Esto genera `cert.pem` y `key.pem` en el directorio del proyecto.
PyFirma los detecta automáticamente y arranca en modo TLS.

> **Importante:** El certificado es autofirmado. La primera vez que
> el navegador conecte, mostrará un aviso de seguridad. El usuario
> deberá aceptarlo manualmente (ver sección 6).

---

## 5. Registro del protocolo `afirma://` en el sistema

Ubuntu 24.04 usa GNOME/Wayland por defecto. El registro se hace
mediante un archivo `.desktop`:

### 5.1. Crear el lanzador

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/pyfirma.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=PyFirma
Comment=Firmador de PDF compatible con AutoFirma
Exec=bash -c 'cd $HOME/pyfirma && ./venv/bin/python main.py %u'
Terminal=false
Categories=Office;Security;
MimeType=x-scheme-handler/afirma;
NoDisplay=false
StartupNotify=true
EOF
```

### 5.2. Registrar el esquema de URL

```bash
xdg-mime default pyfirma.desktop x-scheme-handler/afirma
update-desktop-database ~/.local/share/applications/
```

### 5.3. Verificar el registro

```bash
# Debería devolver "pyfirma.desktop"
xdg-mime query default x-scheme-handler/afirma

# Probar que el lanzador es válido
desktop-file-validate ~/.local/share/applications/pyfirma.desktop
```

---

## 6. Configuración del navegador

### 6.1. Firefox

#### Aceptar certificado TLS autofirmado

La primera vez que PyFirma arranque en modo interceptor, Firefox
intentará conectar por HTTPS/WSS a `127.0.0.1`. Al ser un
certificado autofirmado, Firefox bloqueará la conexión.

Hay que aceptar el certificado manualmente:

1. Arrancar PyFirma en modo "espera" para que el servidor HTTPS
   esté activo (lanzarlo con una URL de prueba, ver sección 7).
2. Abrir Firefox e ir a `https://127.0.0.1:PUERTO/afirma`
   (el puerto aparece en los logs de PyFirma).
3. Firefox mostrará `Advertencia: Riesgo potencial de seguridad`.
4. Pulsar **Avanzado** → **Aceptar el riesgo y continuar**.
5. Repetir para `wss://127.0.0.1:PUERTO` si Firefox lo solicita.

> **Alternativa:** Importar `cert.pem` como autoridad de confianza
> en Firefox: `Ajustes` → `Privacidad y seguridad` → `Certificados` →
> `Ver certificados` → `Autoridades` → `Importar` → seleccionar
> `cert.pem` y marcar "Confiar en este CA para identificar sitios web".

#### Configuración adicional

Si el protocolo `afirma://` no se abre automáticamente:

1. En Firefox, ir a `about:config`
2. Buscar `network.protocol-handler.expose.afirma`
3. Si existe y está a `true`, cambiarlo a `false`
4. Buscar `network.protocol-handler.external.afirma`
5. Si no existe, crearlo como `Boolean` = `true`

### 6.2. Chrome / Chromium

Chrome usa el registro MIME del sistema (`xdg-mime`), por lo que
suele funcionar directamente tras el paso 5.

#### Aceptar certificado TLS autofirmado

1. Arrancar PyFirma (ver sección 7)
2. Ir a `chrome://flags/#allow-insecure-localhost`
3. Activar la opción **Allow invalid certificates for resources loaded from localhost**
4. Relanzar Chrome

> **Alternativa:** Igual que en Firefox, navegar manualmente a
> `https://127.0.0.1:PUERTO/afirma`, pulsar **Avanzado** y
> **Acceder a 127.0.0.1 (sitio no seguro)**.

---

## 7. Verificación de la instalación

### 7.1. Prueba en modo GUI normal

```bash
cd ~/pyfirma
source venv/bin/activate
python main.py
```

Debe abrirse la ventana de PyFirma con los controles de selección
de PDF, certificado y contraseña.

### 7.2. Prueba del protocolo `afirma://`

Simular una llamada del navegador:

```bash
cd ~/pyfirma
source venv/bin/activate
python main.py "afirma://websocket?ports=51234,51235,51236"
```

Debe abrirse la ventana en **modo interceptor** (título azul
"PyFirma - Interceptor de AutoFirma") con el visor de logs:

```
URL: afirma://websocket?ports=51234,51235,51236
Puertos recibidos del navegador: [51234, 51235, 51236]
✓ Servidor WSS iniciado en puerto 51234
✓ HTTPS server started on port 51234 (JSSocket mode)
Esperando conexión del navegador...
```

### 7.3. Prueba con una webapp real

1. Cargar el certificado `.p12` en PyFirma (botón "Seleccionar Certificado")
2. Abrir el navegador y acceder a la aplicación de firma (p.ej. Valide, Portafirmas)
3. Iniciar un proceso de firma
4. El navegador debe invocar PyFirma automáticamente
5. Introducir la contraseña, pulsar **Aceptar**
6. La firma debe completarse

---

## 8. Firma desde línea de comandos (opcional)

PyFirma también puede usarse sin interfaz gráfica:

```bash
cd ~/pyfirma
source venv/bin/activate
python main.py -i documento.pdf -c certificado.p12 -o firmado.pdf
```

Parámetros disponibles:

| Parámetro | Descripción |
|---|---|
| `-i`, `--input` | PDF de entrada |
| `-c`, `--cert` | Certificado `.p12` o `.pfx` |
| `-p`, `--password` | Contraseña (si no se pasa, la pide interactivamente) |
| `-o`, `--output` | PDF de salida (por defecto: `<input>_signed.pdf`) |
| `--visible` | Añadir marca de agua visible |
| `--vertical-left` | Marca de agua en margen izquierdo vertical |
| `--all-pages` | Aplicar marca a todas las páginas |

---

## 9. Configuración de caché de contraseña

PyFirma guarda en `config.json` (en el directorio del proyecto):

- `last_cert_path`: Ruta del último certificado usado
- `last_password`: Contraseña (solo si se marcó "Recordar contraseña")

Para mayor seguridad, se recomienda **no** activar "Recordar
contraseña" en entornos compartidos.

---

## 10. Uso con lector de DNIe / SmartCard

Si el certificado está en una tarjeta inteligente (DNIe, FNMT en
tarjeta), es necesario extraerlo previamente a un archivo `.p12`:

```bash
# Exportar certificado de la tarjeta (requiere opensc)
sudo apt install -y opensc pcscd
pkcs15-tool --read-certificate <id> --output certificado.cer
pkcs15-tool --read-public-key <id> --output clave_publica.pem

# O usar el software oficial de la FNMT / DNIe para exportar a .p12
```

> **Nota:** PyFirma no firma directamente contra la tarjeta
> inteligente. El certificado y la clave privada deben estar en
> un archivo `.p12` accesible desde el sistema de archivos.

---

## 11. Solución de problemas

### El navegador no abre PyFirma al pulsar "Firmar"

1. Verificar que `xdg-mime query default x-scheme-handler/afirma`
   devuelve `pyfirma.desktop`
2. En Firefox, revisar `about:config` para las claves
   `network.protocol-handler.external.afirma` y
   `network.protocol-handler.expose.afirma`
3. Probar desde terminal: `xdg-open "afirma://test"`
4. Revisar que el archivo `.desktop` usa rutas absolutas

### Error "Conexión no segura" en el navegador

1. Aceptar manualmente el certificado en `https://127.0.0.1:PUERTO/afirma`
2. En Chrome, activar `chrome://flags/#allow-insecure-localhost`
3. Verificar que `cert.pem` y `key.pem` existen en `~/pyfirma/`

### Error "No module named '_tkinter'"

```bash
sudo apt install -y python3-tk
```

### Error "Permission denied" al ejecutar python

```bash
chmod +x ~/pyfirma/venv/bin/python
```

### El visor de logs no muestra actividad

- Verificar que el cortafuegos no bloquea los puertos locales:
  ```bash
  sudo ufw status
  ```
  (Las conexiones a `127.0.0.1` no deberían verse afectadas por ufw)

- Verificar que otro proceso no ocupa los puertos:
  ```bash
  ss -tlnp | grep -E "PUERTO"
  ```

### Archivo de bloqueo obsoleto

Si PyFirma no arranca porque cree que ya hay una instancia activa:

```bash
rm /tmp/pyfirma.lock
```

---

## 12. Archivos generados en tiempo de ejecución

| Archivo | Ubicación | Propósito |
|---|---|---|
| `config.json` | `~/pyfirma/` | Configuración persistente (certificado, contraseña) |
| `pyfirma.lock` | `/tmp/` | Bloqueo de instancia única |
| `pyfirma.redirect` | `/tmp/` | Redirección de URLs entre instancias |
| `/tmp/pyfirma_http.log` | `/tmp/` | Log de depuración del servidor HTTP |

---

## 13. Desinstalación

```bash
# Eliminar el lanzador del sistema
rm ~/.local/share/applications/pyfirma.desktop
update-desktop-database ~/.local/share/applications/

# Eliminar el proyecto
rm -rf ~/pyfirma

# Limpiar archivos temporales
rm -f /tmp/pyfirma.lock /tmp/pyfirma.redirect /tmp/pyfirma_http.log
```
