# Protocolo `afirma://` — Documentación de PyFirma

Documento que recoge todo lo aprendido sobre el protocolo `afirma://`
durante la implementación de PyFirma: estructura de URL, modos de
transporte, comandos, formatos de firma, gestión de errores y
particularidades descubiertas mediante ingeniería inversa.

---

## 1. Visión general

`afirma://` es un protocolo personalizado registrado en el sistema
operativo para que el navegador pueda invocar a la aplicación nativa
**AutoFirma** (Java) — o en nuestro caso **PyFirma** (Python) —
cuando un sitio web necesita firmar digitalmente un documento.

El flujo típico es:

```
Sitio web (JS)
  └─ autoscript.js  ──▶  afirma://websocket?ports=XXXXX&...
                              │
                              ▼
                        Sistema operativo
                              │
                              ▼
                         PyFirma (main.py)
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
           Servidor WebSocket    Servidor HTTPS
           (ws_server.py)       (http_server.py)
                    │                   │
                    └─────────┬─────────┘
                              ▼
                         GUI (gui.py)
                              │
                              ▼
                        Firmador (signer.py)
```

---

## 2. Invocación del protocolo

### 2.1. Desde el sistema operativo

Cuando el navegador encuentra un enlace `afirma://`, el sistema
operativo lanza PyFirma con la URL completa como único argumento:

```bash
pyfirma "afirma://websocket?ports=55123,55124,55125"
```

### 2.2. Detección de instancia única

PyFirma usa un archivo de bloqueo para evitar múltiples instancias:

| Archivo | Ubicación | Propósito |
|---|---|---|
| `pyfirma.lock` | `tempfile.gettempdir()` | Contiene el PID de la instancia activa |
| `pyfirma.redirect` | `tempfile.gettempdir()` | Pasa URLs de una instancia nueva a la existente |

**Flujo:**
1. Si ya hay una instancia corriendo (`pyfirma.lock` con PID vivo),
   la nueva escribe la URL en `pyfirma.redirect` y sale.
2. La instancia original revisa `pyfirma.redirect` cada 1 segundo.
3. Si encuentra una URL, arranca nuevos servidores en los puertos
   indicados y continúa.

Esto permite que `autoscript.js` relance PyFirma con nuevos puertos
si la conexión WebSocket se cierra, sin abrir ventanas duplicadas.

### 2.3. Parseo inicial de la URL

```python
# Estructura de la URL:
afirma://<modo>?ports=<p1>,<p2>,<p3>

# Ejemplo:
afirma://websocket?ports=55123,55124,55125
afirma://service?ports=55123,55124,55125
```

| Componente | Valor | Significado |
|---|---|---|
| `netloc` o `path` | `websocket` | Usar modo WebSocket (WS/WSS) |
| | `service` | Usar modo servicio (mismos puertos, ambos transportes) |
| `ports` | `55123,55124,55125` | Lista de puertos efímeros donde escuchar |

---

## 3. Modos de transporte

PyFirma soporta dos modos de transporte simultáneos, que se ejecutan
en hilos independientes.

### 3.1. Asignación de puertos

El navegador (`autoscript.js`) genera 3 puertos aleatorios y los envía
en la URL `afirma://`. PyFirma los reparte de forma **determinista**:

```
Puertos recibidos:       [A, B, C]
                          │   └─┬──┘
                          ▼      ▼
Servidor WSS (WebSocket): A    HTTP (JSSocket): B, C
```

- **WSS** recibe `ports[0]` — el primer puerto, que es el primero que
  el navegador prueba al conectar.
- **HTTP (JSSocket)** recibe `ports[1:]` — los puertos restantes.

Este reparto evita la condición de carrera que ocurría al arrancar
ambos servidores en paralelo con la misma lista de puertos, donde el
servidor HTTP podía ocupar el primer puerto y el navegador fallaba al
intentar el handshake WebSocket contra un servidor que solo habla HTTP.

### 3.2. Modo WebSocket (`ws_server.py`)

El navegador abre una conexión WebSocket a `ws://127.0.0.1:PUERTO`
(o `wss://` si hay TLS) y envía las operaciones como mensajes de texto.

| Característica | Valor |
|---|---|
| Biblioteca | `websockets` (Python) |
| Puerto | `ports[0]` (primer puerto de la lista) |
| TLS | Activado si existen `cert.pem` y `key.pem` |
| Ping/Pong | Cada 30 s (keepalive) |
| Tamaño máximo mensaje | 50 MB (`max_size`) |
| Timeout cierre | 5 s (`close_timeout`) |
| Interfaz | `0.0.0.0` (todas las interfaces, necesario para Firefox snap) |

**Handshake inicial (echo):**
```
Cliente → Servidor:  echo=-idsession=ABC123@EOF
Servidor → Cliente:  echo
```

### 3.3. Modo HTTP / JSSocket (`http_server.py`)

Algunos sitios web (p.ej. Valide) usan el modo JSSocket en lugar de
WebSocket. En este modo, el navegador envía peticiones HTTP POST al
endpoint `https://127.0.0.1:PUERTO/afirma`.

| Característica | Valor |
|---|---|
| Puerto | `ports[1:]` (puertos restantes tras el WSS) |
| Interfaz | `0.0.0.0` (necesario para Firefox snap) |

Todas las respuestas incluyen cabeceras CORS:
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: POST, OPTIONS
Access-Control-Allow-Headers: Content-Type
```

**Fases del protocolo JSSocket:**

| Fase | Petición | Respuesta |
|---|---|---|
| 1. Echo | `POST /afirma` body=`echo=-idsession=SID@EOF` | `200 OK` body=`base64("OK")` |
| 2. Fragmentos | `POST /afirma` body=`cmd=FRAGMENTO_B64@EOF` | `200 OK` body=`base64("1")` (nº partes) |
| 3. Ejecución | `POST /afirma` body=`firm=idsession=SID@EOF` | `200 OK` body=`base64("1")` |
| 4. Resultado | `POST /afirma` body=`send=@PARTE@TOTALidsession=SID@EOF` | `200 OK` body=`base64(resultado)` |
| CORS | `OPTIONS /afirma` | `204 No Content` |

**Importante:** En JSSocket, la URL `afirma://` se envía por partes.
Cada fragmento se codifica en base64 URL-safe y se envía con `cmd=`.
El servidor acumula los fragmentos y, al recibir `send=`, procesa
la URL completa y devuelve el resultado.

---

## 4. Estructura de la URL `afirma://`

### 4.1. Formato general

```
afirma://<operacion>?<param1>=<val1>&<param2>=<val2>&dat=<datos>
```

### 4.2. La operación

Va en el `netloc` (`afirma://sign?...`) o en el `path` (`afirma:///sign?...`).

### 4.3. El parámetro `dat`

Es el parámetro más delicado del protocolo. Contiene los datos del
documento a firmar/guardar.

**Características críticas:**

1. **`avoidEncoding=true`:** `autoscript.js` inserta `dat` sin
   `encodeURIComponent()`, por lo que su valor NO está codificado
   en URL.

2. **Siempre es el ÚLTIMO parámetro** de la query string.

3. **Puede ocupar múltiples líneas** en el mensaje WebSocket.

4. **Codificación:** base64 URL-safe (usa `-` y `_` en lugar de
   `+` y `/`, sin relleno `=` al final).

5. **Dos modos de envío:**
   - WebSocket texto: `dat` viaja como parte del string de la URL.
   - WebSocket binario: `dat` viaja como bytes crudos tras `b'&dat='`
     (usado en operaciones `save` para evitar corrupción UTF-8).

**Decodificación de `dat` (estrategia en dos pasos):**

| Paso | Condición | Método |
|---|---|---|
| 1 | `dat_content.isascii()` | Base64 URL-safe → bytes |
| 2 | Caracteres no-ASCII (0-255) | Latin-1 → bytes originales |

---

## 5. Comandos (operaciones) del protocolo

### 5.1. `sign` — Firma de documento

El navegador envía el documento a firmar y espera el resultado firmado.

**URL de ejemplo:**
```
afirma://sign?format=PAdES&algorithm=sha256&dat=UEsDBBQAAAAIAL...
```

**Parámetros principales:**

| Parámetro | Valor | Descripción |
|---|---|---|
| `format` | `PAdES`, `XAdES`, `CADES` | Formato de firma solicitado |
| `algorithm` | `sha256`, `sha512` | Algoritmo de hash |
| `dat` | Base64 URL-safe | Documento a firmar |
| `docid` | String | Identificador del documento |
| `properties` | Base64 | Propiedades adicionales codificadas |

**Flujo:**
1. El servidor recibe la URL y extrae `dat`.
2. Decodifica `dat` de base64 URL-safe a bytes.
3. Firma según el formato solicitado.
4. Envía la respuesta por WebSocket (o HTTP).

**Formato de respuesta:**
```
<base64url_certificado_DER>|<base64url_documento_firmado>
```

Este formato replica el de Java AutoFirma: `signature|certificate`.
`autoscript.js` divide por `|` y pasa al callback JS:
- `parte[0]` → certificate (2º parámetro del callback)
- `parte[1]` → signature (1er parámetro del callback)

**Incluir el certificado es crítico para la firma por lotes** (batch).
`batchScript.js` recibe `certificateB64` en el callback `signMassive()`
y lo reenvía al callback final `BatchScript.callBackFunction()`.
Si el certificado está vacío, el sitio web puede rechazar la operación
completa (error HTTP 500 en `doSign`).

En versiones anteriores de PyFirma se enviaba `|<firma>` con el
certificado vacío, lo que causaba errores en webs con batch.

### 5.2. `cosign` — Cofirma

Añade una segunda firma a un documento ya firmado. Mismo
comportamiento que `sign`.

### 5.3. `countersign` — Contrafirma

Firma la firma existente (no el documento). Mismo comportamiento
que `sign`.

### 5.4. `signandsave` — Firma y guarda

Combina firma y guardado en una sola operación. Tras firmar:
1. Envía el resultado por WebSocket (igual que `sign`).
2. Muestra un diálogo de guardado al usuario.
3. Guarda el documento firmado en la ruta elegida.

### 5.5. `save` — Guardar documento

El navegador envía un documento para que el usuario lo guarde
localmente. No se realiza firma.

**URL de ejemplo:**
```
afirma://save?filename=documento&ext=pdf&title=Guardar+fichero&dat=...
```

**Parámetros específicos:**

| Parámetro | Valor por defecto | Descripción |
|---|---|---|
| `filename` | `firma` | Nombre sugerido para el archivo |
| `ext` | `pdf` | Extensión del archivo |
| `title` | `Guardar fichero` | Título del diálogo de guardado |

**Flujo:**
1. Extrae los metadatos (filename, ext, title) de la primera línea.
2. Extrae `dat` (bytes crudos si trama binaria, o base64 si texto).
3. Muestra diálogo `asksaveasfilename` al usuario.
4. Escribe los bytes y responde `OK` o `CANCEL`.

### 5.6. `batch` — Firma por lotes

La firma por lotes permite firmar múltiples documentos en una sola
operación. En escritorio, NO se usa el comando `batch` del protocolo
WebSocket — en su lugar, `batchScript.js` implementa un bucle recursivo
que envía operaciones `sign` individuales.

**Flujo en escritorio (`doMassiveSign` → `signMassive` recursivo):**

```
1. doMassiveSign()
     └─ massiveIndex = 0
     └─ AutoScript.setStickySignatory(true)
     └─ signMassive()

2. signMassive(undefined, undefined)          ← primera llamada (sin args)
     └─ AutoScript.sign(data[0], algo, fmt, params, signMassive, errorCb)

3. PyFirma firma → responde "cert_b64|sig_b64"

4. signMassive(sig1_b64, cert1_b64)          ← callback con resultado
     └─ massiveIndex++ → 1
     └─ "Firma masiva [1] OK"
     └─ massiveResult = sig1_b64
     └─ AutoScript.sign(data[1], algo, fmt, params, signMassive, errorCb)

5. PyFirma firma → responde "cert_b64|sig2_b64"

6. signMassive(sig2_b64, cert2_b64)          ← callback con resultado
     └─ massiveIndex++ → 2
     └─ "Firma masiva [2] OK"
     └─ massiveResult = "sig1:sig2"
     └─ data[2] == undefined → FIN

7. BatchScript.callBackFunction("sig1:sig2", cert2_b64)
     └─ El sitio web recibe las firmas concatenadas con ":"
     └─ El certificado viene del último signMassive (cert2_b64)
```

**Puntos clave descubiertos:**

- **`stickySignatory(true)`**: Fija el certificado para todas las firmas
  del lote. El usuario no tiene que reconfirmar para cada documento.
- **`certificateB64` en el callback**: `AutoScript.sign()` pasa
  `(firma_b64, cert_b64)` al callback. Si PyFirma no incluye el
  certificado en la respuesta, `cert_b64` queda vacío y el callback
  final recibe un certificado nulo, causando error en el servidor.
- **Concatenación con `:`**: Las firmas se unen con `:` antes de
  enviarse al servidor en `doSign`.
- **Modo móvil**: En Android/iOS se usa `AutoScript.signBatchProcess()`
  con servidores trifásicos (preSign/postSign), en lugar del bucle
  recursivo. PyFirma aún no soporta este modo.
- **`multiModeSign`**: Es un wrapper que mapea parámetros de
  `multiModeSign` a `doSignBatch` y llama a esta última.

### 5.7. `selectcert` — Selección de certificado

El sitio web solicita que el usuario seleccione un certificado.
Se registra en el log para posible implementación futura.

### 5.8. `load` — Carga de archivo

El sitio web solicita cargar un archivo del sistema local.
Se registra en el log para posible implementación futura.

---

## 6. Formatos de firma soportados

PyFirma soporta tres formatos de firma electrónica:

### 6.1. PAdES — PDF Advanced Electronic Signature

**Estándar:** ETSI TS 102 778 (firma avanzada para PDF)

| Característica | Valor |
|---|---|
| Biblioteca | `endesive.pdf.cms` |
| Algoritmo hash | SHA-256 |
| Tipo de firma | `sigflags=3` (certificación + aprobación) |
| Incrustación | La firma CMS se añade al final del PDF |

**Metadatos de firma:**
```python
dct = {
    "sigflags": 3,
    "sigpage": 0,
    "contact": "",
    "location": "",
    "signingdate": "D:20260611120000Z",  # Formato PDF
    "reason": "Signed with PyFirma",
}
```

**Firma visible opcional:** Si se activa en la GUI, se añade una
marca de agua con:
- Nombre común (CN) del certificado.
- Fecha y hora local.
- Opción de colocar en margen izquierdo vertical (rotado 90°).
- Opción de aplicar a la primera página o a todas.

### 6.2. XAdES — XML Advanced Electronic Signature

**Estándar:** ETSI TS 101 903 v1.3.2 (XAdES-BES Enveloping)

| Característica | Valor |
|---|---|
| Biblioteca | `lxml` (canonicalización C14N) + `cryptography` (RSA) |
| Algoritmo digest | SHA-256 |
| Algoritmo firma | RSA-SHA256 con PKCS#1 v1.5 |
| Canonicalización | C14N 1.0 (sin comentarios) |

**Estructura del XML generado:**
```xml
<ds:Signature Id="Signature-...">
  <ds:SignedInfo>
    <ds:CanonicalizationMethod Algorithm="...c14n..."/>
    <ds:SignatureMethod Algorithm="...rsa-sha256"/>
    <ds:Reference Type="...SignedProperties" URI="#SignedProperties-...">
      <ds:DigestMethod Algorithm="...sha256"/>
      <ds:DigestValue>...</ds:DigestValue>
    </ds:Reference>
    <ds:Reference URI="#Document-...">
      <ds:DigestMethod Algorithm="...sha256"/>
      <ds:DigestValue>...</ds:DigestValue>
    </ds:Reference>
  </ds:SignedInfo>
  <ds:SignatureValue>...</ds:SignatureValue>
  <ds:KeyInfo>
    <ds:X509Data>
      <ds:X509Certificate>...</ds:X509Certificate>
    </ds:X509Data>
  </ds:KeyInfo>
  <ds:Object>
    <xades:QualifyingProperties Target="#Signature-...">
      <xades:SignedProperties Id="SignedProperties-...">
        <xades:SignedSignatureProperties>
          <xades:SigningTime>2026-06-11T12:00:00Z</xades:SigningTime>
          <xades:SigningCertificate>
            <xades:Cert>
              <xades:CertDigest>...</xades:CertDigest>
              <xades:IssuerSerial>...</xades:IssuerSerial>
            </xades:Cert>
          </xades:SigningCertificate>
        </xades:SignedSignatureProperties>
      </xades:SignedProperties>
    </xades:QualifyingProperties>
  </ds:Object>
  <ds:Object Id="Document-...">
    ... documento XML a firmar ...
  </ds:Object>
</ds:Signature>
```

**Particularidad:** Si los datos no son XML válido, se embeeben
como base64 dentro de un elemento `<DocumentData>`.

### 6.3. CAdES — CMS Advanced Electronic Signature

**Estándar:** ETSI TS 101 733 (CAdES-BES)

| Característica | Valor |
|---|---|
| Biblioteca | `endesive.plain` + `asn1crypto.cms` |
| Algoritmo hash | SHA-256 |
| Modo | Attached/Explícito (datos embeebidos en eContent) |

**Flujo de firma para datos completos:**
1. `endesive.plain.sign()` produce un CMS detached (sin los datos).
2. Se parsea el CMS con `asn1crypto.cms.ContentInfo`.
3. Se embeeben los datos originales en `encap_content_info['content']`.
4. Se vuelca el CMS completo con `ci.dump()`.

**Modo hash precalculado (pdf_size == 32 bytes):**

Para documentos grandes, la webapp no envía el documento completo
(no cabe en la URL). En su lugar envía el hash SHA-256 del documento
(32 bytes) en el parámetro `dat`.

```python
# Detección: si dat son exactamente 32 bytes → es un hash SHA-256
if len(pdf_data) == 32:
    signed_data = signer.sign(
        b'', private_key, certificate,             # contenido vacío
        additional_certificates, hashalgo='sha256',
        attrs=True, signed_value=pdf_data,         # hash precalculado
    )
    # NO se embebe en eContent — firma detached
```

**Importante:** En modo hash, el CMS resultante es una firma **detached**
(sin `eContent`). El hash se pasa como `signed_value` a `endesive` y
se incluye en los atributos firmados (`messageDigest`). NO debe
embeberse en `EncapsulatedContentInfo.content` — si se hace, el
`messageDigest` se recalcula sobre el valor embebido (hash del hash)
y el servidor de validación lo rechaza.

---

## 7. Codificación de datos

### 7.1. Base64 URL-safe

Todo el intercambio de datos binarios usa **base64 URL-safe**:

| Base64 estándar | Base64 URL-safe |
|---|---|
| `+` | `-` |
| `/` | `_` |
| `=` (padding) | Sin padding |

```python
# Codificación (Python → JS):
b64 = base64.b64encode(data).decode('utf-8')
b64_urlsafe = b64.replace('+', '-').replace('/', '_')

# Decodificación (JS → Python):
b64 = raw.strip().replace('-', '+').replace('_', '/')
b64 += '=' * (-len(b64) % 4)  # Restaurar padding
data = base64.b64decode(b64)
```

### 7.2. El problema del binario en WebSocket de texto

Este es uno de los problemas más sutiles descubiertos durante
la implementación. El viaje de ida y vuelta de los datos binarios
a través de una trama de texto WebSocket es:

```
Bytes originales (0-255)
  → JS string (codepoint = byte, mapeo Latin-1)
  → Trama texto WebSocket (JS codifica el string como UTF-8)
  → Python websockets decodifica UTF-8 → str
  → str.encode('latin-1') → bytes originales
```

Para caracteres con codepoints > 255 (artefactos de decodificación
UTF-8 de secuencias inválidas), se usa `surrogateescape` como
fallback, y en último caso se reconstruye byte a byte.

**Solución para `save`:** Cuando el mensaje llega como trama
WebSocket binaria (`isinstance(message, bytes)`), se extrae `dat`
directamente de los bytes crudos buscando `b'&dat='`, evitando
completamente el problema de codificación.

---

## 8. Tabla de respuestas del servidor

| Respuesta | Significado | Cuándo se envía |
|---|---|---|
| `echo` | Conexión WebSocket confirmada | Tras recibir `echo=...@EOF` |
| `<cert_b64>\|<sig_b64>` | Firma + certificado (base64 URL-safe) | Tras `sign`/`cosign`/`countersign` exitoso |
| `OK` | Operación de guardado exitosa | Tras `save` completado |
| `CANCEL` | Usuario canceló la operación | Diálogo cancelado, contraseña faltante |
| `SAF_NO_DATA` | Falta el parámetro `dat` | `save` o `sign` sin datos |
| `SAF_ERROR` | Error genérico | Excepción durante el procesamiento |
| `SAF_ERROR:<msg>` | Error con mensaje | Error específico con detalle |

---

## 9. Callbacks de la GUI (comunicación servidor → interfaz)

El servidor notifica a la GUI mediante un callback que recibe
`(event_type, message)`:

| `event_type` | Propósito | `message` |
|---|---|---|
| `save_operation` | Operación de guardado | `str` o `(str, bytes)` |
| `sign_operation` | Operación de firma | URL `afirma://` completa |
| `batch_operation` | Operación por lotes | URL `afirma://` completa |
| `message` | Operación desconocida o `selectcert`/`load` | URL o mensaje crudo |
| `event` | Notificación informativa | Texto descriptivo |
| `info` | Información de depuración | Texto descriptivo |
| `error` | Error | Texto descriptivo |

---

## 10. TLS / WSS / HTTPS

Si existen los archivos `cert.pem` y `key.pem` en el directorio
de trabajo, ambos servidores se inician con TLS:

| Modo | Sin TLS | Con TLS |
|---|---|---|
| WebSocket | `ws://127.0.0.1:PUERTO` | `wss://127.0.0.1:PUERTO` |
| HTTP | `http://127.0.0.1:PUERTO` | `https://127.0.0.1:PUERTO` |

### 10.1. Cadena de confianza TLS (rootCA + cert.pem)

Para que Firefox acepte la conexión WSS **sin pedir confirmación manual
en cada puerto nuevo**, el certificado del servidor debe estar firmado
por una CA de confianza, no ser autofirmado:

```
rootCA.pem (CA raíz, instalada en Firefox como Autoridad)
    │
    └── firma a cert.pem (certificado del servidor TLS)
```

Si `cert.pem` es autofirmado, Firefox muestra la advertencia de
seguridad cada vez que PyFirma arranca en un puerto nuevo (los
puertos son aleatorios y cambian en cada invocación).

**Generación de la cadena:**
```bash
# 1. CA raíz (solo una vez)
openssl req -x509 -newkey rsa:4096 -keyout rootCA.key -out rootCA.pem \
  -days 3650 -nodes -subj "/CN=PyFirma Root CA/O=PyFirma"
# Instalar rootCA.pem en Firefox como Autoridad de confianza

# 2. Certificado de servidor (firmado por la CA)
openssl req -new -newkey rsa:4096 -keyout server-key.pem -out server.csr \
  -nodes -subj "/CN=localhost/O=PyFirma" \
  -addext "subjectAltName=IP:127.0.0.1"
openssl x509 -req -in server.csr -CA rootCA.pem -CAkey rootCA.key \
  -CAcreateserial -out cert.pem -days 3650 -copy_extensions copy

# 3. Verificar
openssl verify -CAfile rootCA.pem cert.pem
```

El script `regenerar-cert.sh` automatiza estos pasos.

### 10.2. Snap Firefox y `0.0.0.0`

Firefox en Ubuntu 24.04 se instala como paquete **snap** con
aislamiento de red. Los servidores de PyFirma deben escuchar en
`0.0.0.0` (todas las interfaces) en lugar de solo `127.0.0.1` para
que Firefox snap pueda alcanzarlos. Con `127.0.0.1`, Firefox muestra
"no puede establecer una conexión con el servidor en wss://127.0.0.1:...".

---

## 11. Certificados de firma

PyFirma soporta certificados PKCS#12 (`.p12` / `.pfx`) que contienen:
- Clave privada RSA.
- Certificado X.509 del firmante.
- Certificados adicionales de la cadena de confianza (opcional).

La contraseña puede ser vacía (certificados sin protección).

---

## 12. Errores frecuentes y solución

### 12.1. `"¡No se detectaron puertos en la URL!"`

**Causa:** La URL `afirma://` recibida no contiene el parámetro `ports`.

**Diagnóstico:** PyFirma muestra todos los parámetros recibidos
(incluyendo decodificación base64 de cada uno) en el visor de logs.

**Posibles causas:**
- El script `autoscript.js` del sitio web no está enviando los
  puertos correctamente.
- El sistema operativo truncó la URL al pasarla como argumento.

### 12.2. `"Failed to decode dat"` (SAF_ERROR)

**Causa:** El valor de `dat` no es base64 URL-safe válido o está corrupto.

**Diagnóstico:** Revisar `/tmp/pyfirma_http.log` para ver el contenido
recibido.

**Posibles causas:**
- El dato llegó como binario crudo pero se intentó decodificar como
  texto (o viceversa).
- La URL fue truncada en algún punto del camino.
- `autoscript.js` usó `encodeURIComponent` en `dat` cuando no debía.

### 12.3. `"Contraseña incorrecta o certificado inválido"`

**Causa:** La contraseña no coincide con la del archivo `.p12`/`.pfx`,
o el archivo está corrupto.

**Solución:** Verificar la contraseña con:
```bash
openssl pkcs12 -in certificado.p12 -noout -passin pass:CONTRASEÑA
```

### 12.4. Conexión WebSocket cerrada inesperadamente

**Causas comunes:**
- El puerto estaba ocupado y el servidor no pudo iniciar.
- El navegador cerró la pestaña.
- Timeout de inactividad (ping/pong cada 30s).

**Diagnóstico:** El visor de logs muestra `Connection closed: code=X`.

### 12.5. El navegador envía `sign` sin `dat`

**Síntoma:** El visor de logs muestra:
```
Petición de firma sin datos incrustados.
Seleccione un PDF, certifique y pulse Firmar Documento...
```

**Causa:** Algunos sitios web (p.ej. Valide) esperan que la
aplicación nativa cargue el archivo por un mecanismo separado.
No es un error — la GUI se pone en modo pendiente y permite
al usuario seleccionar el PDF manualmente.

### 12.6. `"SAF_ERROR:Unsupported format: XYZ"`

**Causa:** El formato solicitado en el parámetro `format` no está
implementado.

**Formatos soportados:** `PAdES`, `XAdES`, `CADES`, `PDF`.

### 12.7. `"SAF_ERROR:No certificate loaded"`

**Causa:** El navegador solicitó una firma pero no hay ningún
certificado `.p12` cargado en la GUI.

### 12.8. Archivo de bloqueo obsoleto

**Síntoma:** PyFirma no se abre porque cree que ya hay una
instancia corriendo.

**Solución:** Eliminar manualmente el archivo de bloqueo:
```bash
rm /tmp/pyfirma.lock
```

---

## 13. Depuración

### 13.1. Archivos de log

| Archivo | Contenido |
|---|---|
| `/tmp/pyfirma_http.log` | Log detallado del servidor HTTP/JSSocket |
| Visor de logs en GUI | Eventos WebSocket y HTTP en tiempo real |

### 13.2. Habilitar logs detallados

El visor de logs de la GUI (modo interceptor) muestra:
- URLs `afirma://` recibidas con sus parámetros decodificados.
- Cargas útiles JSON formateadas.
- Eventos de conexión/desconexión.
- Errores con traceback completo.

---

## 14. Versiones de `autoscript.js`

El script que corre en el navegador (`autoscript.js`) existe en varias
versiones con diferencias relevantes:

| Versión | `VERSION` | `URL_REQUEST_PREFIX` | PROTOCOL_VERSION |
|---------|-----------|---------------------|-----------------|
| 1.8.2.1 | `"1.8.2.1"` | `wss://` | 4 |
| 1.9.0 | `"1.9.0"` | `ws://` o `wss://` | 4 |

**Diferencias clave:**

- **Prefijo WebSocket:** La v1.9.0 original usa `ws://` (plano),
  pero en páginas HTTPS el navegador bloquea conexiones `ws://` como
  contenido mixto. Algunas webs (p.ej. Junta de Andalucía) sirven una
  v1.9.0 modificada con `wss://`.
- **`PROTOCOL_VERSION` 4:** Ambas versiones usan protocolo v4.
  El servidor debe responder correctamente al handshake echo y a los
  formatos de respuesta `cert|firma`.
- **JSSocket:** Ambas versiones usan `https://127.0.0.1:PUERTO/afirma`
  independientemente del prefijo WebSocket.
- **`batchScript.js`:** Es un script adicional (v1.0.1) que implementa
  la firma por lotes sobre `autoscript.js`. En escritorio usa
  `signMassive()` recursivo; en móvil usa `signBatchProcess()` con
  servidores trifásicos.

---
## 15. Nuevos errores y soluciones

### 15.1. `SEC_ERROR_TOKEN_NOT_LOGGED_IN` al instalar certificado

**Causa:** Firefox tiene activada la **contraseña maestra**. `certutil`
no puede modificar los trust flags del certificado en la base de datos
NSS sin autenticarse.

**Solución:**
1. Desactivar temporalmente la contraseña maestra en Firefox.
2. Ejecutar `certutil -A` o el instalador de PyFirma.
3. Reactivar la contraseña maestra.

### 15.2. Error HTTP 500 del servidor tras firma batch

**Causa:** El servidor remoto (ej. `api-veaja.cloud.juntadeandalucia.es`)
rechaza la operación `doSign`. Posibles causas:
- El certificado del firmante no estaba incluido en la respuesta
  WebSocket (corregido: ahora se envía `cert_b64|firma_b64`).
- El CMS tiene una estructura diferente a la esperada (ej. hash
  embebido en eContent cuando debería ser detached).
- El certificado del firmante no cumple los requisitos del organismo.

**Diagnóstico:** Revisar la consola del navegador. Si aparecen
`"Firma masiva [N] OK"` para todos los documentos pero el POST
a `doSign` devuelve 500, el problema está en el formato de la firma
o en la validación del servidor, no en la conexión.

### 15.3. "no puede establecer una conexión con wss://127.0.0.1:..."

**Causas posibles (orden de probabilidad):**

1. **Firefox snap** no puede alcanzar `127.0.0.1` del host → el
   servidor debe escuchar en `0.0.0.0`.
2. **Certificado TLS no confiable** → instalar `rootCA.pem` en
   Firefox (Ajustes → Privacidad → Certificados → Autoridades).
3. **`cert.pem` autofirmado** en vez de firmado por la CA → usar
   `regenerar-cert.sh`.
4. **Conflicto de puertos** con el servidor HTTP → el reparto
   determinista de puertos (WSS=`ports[0]`, HTTP=`ports[1:]`)
   soluciona esto.

---
## 16. Referencias

| Estándar | Descripción |
|---|---|
| ETSI TS 102 778 | PAdES — Firma electrónica avanzada para PDF |
| ETSI TS 101 903 | XAdES — Firma electrónica avanzada para XML |
| ETSI TS 101 733 | CAdES — Firma electrónica avanzada para CMS |
| W3C XMLDSig | Firma digital XML (canonicalización, digest, firma) |
| PKCS#12 | Formato de archivo para almacenar claves y certificados |
| RFC 4514 | Representación de DN de certificados X.509 |

---


