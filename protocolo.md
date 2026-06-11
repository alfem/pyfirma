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
en hilos independientes:

### 3.1. Modo WebSocket (`ws_server.py`)

El navegador abre una conexión WebSocket a `ws://127.0.0.1:PUERTO`
(o `wss://` si hay TLS) y envía las operaciones como mensajes de texto.

| Característica | Valor |
|---|---|
| Biblioteca | `websockets` (Python) |
| Puerto | Primer disponible de la lista |
| TLS | Activado si existen `cert.pem` y `key.pem` |
| Ping/Pong | Cada 30 s (keepalive) |
| Tamaño máximo mensaje | 50 MB (`max_size`) |
| Timeout cierre | 5 s (`close_timeout`) |

**Handshake inicial (echo):**
```
Cliente → Servidor:  echo=-idsession=ABC123@EOF
Servidor → Cliente:  echo
```

### 3.2. Modo HTTP / JSSocket (`http_server.py`)

Algunos sitios web (p.ej. Valide) usan el modo JSSocket en lugar de
WebSocket. En este modo, el navegador envía peticiones HTTP POST al
endpoint `https://127.0.0.1:PUERTO/afirma`.

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
|<base64_urlsafe_del_documento_firmado>
```

El prefijo `|` imita el formato de Java AutoFirma:
`signature|certificate`. `autoscript.js` divide por `|`:
- `parte[0]` → `signature` (1er parámetro del callback JS)
- `parte[1]` → `certificate` (2º parámetro del callback JS)

Con `"|" + b64`, los datos firmados llegan como texto base64 al
1er parámetro del callback, evitando la decodificación a binario
que causaría corrupción UTF-8 en la URL de guardado posterior.

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

Operación de firma múltiple. Actualmente registrada en el log pero
no implementada completamente.

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
| Modo | Attached/Explícito (datos embeebidos) |

**Flujo de firma:**
1. `endesive.plain.sign()` produce un CMS detached (sin los datos).
2. Se parsea el CMS con `asn1crypto.cms.ContentInfo`.
3. Se embeeben los datos originales en `encap_content_info['content']`.
4. Se vuelca el CMS completo con `ci.dump()`.

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
| `\|<b64>` | Documento firmado (base64 URL-safe) | Tras `sign`/`cosign`/`countersign` exitoso |
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

Para generar certificados de desarrollo:
```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=localhost"
```

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

## 14. Referencias

| Estándar | Descripción |
|---|---|
| ETSI TS 102 778 | PAdES — Firma electrónica avanzada para PDF |
| ETSI TS 101 903 | XAdES — Firma electrónica avanzada para XML |
| ETSI TS 101 733 | CAdES — Firma electrónica avanzada para CMS |
| W3C XMLDSig | Firma digital XML (canonicalización, digest, firma) |
| PKCS#12 | Formato de archivo para almacenar claves y certificados |
| RFC 4514 | Representación de DN de certificados X.509 |

---


