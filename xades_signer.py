"""
Firmador XAdES (XML Advanced Electronic Signature) para PyFirma.

Implementa el formato XAdES-BES Enveloping según el estándar
ETSI TS 101 903 v1.3.2, construido sobre XMLDSig (W3C).

Utiliza lxml para la canonicalización C14N 1.0 y cryptography
para la firma RSA-SHA256.

Estructura del XML generado:

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
            <xades:SigningTime>...</xades:SigningTime>
            <xades:SigningCertificate>...</xades:SigningCertificate>
          </xades:SignedSignatureProperties>
        </xades:SignedProperties>
      </xades:QualifyingProperties>
    </ds:Object>
    <ds:Object Id="Document-...">
      ... documento XML a firmar ...
    </ds:Object>
  </ds:Signature>
"""

import datetime
import hashlib
import uuid
import base64

from lxml import etree
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID


# ---------------------------------------------------------------------------
# Espacios de nombres XML utilizados en XAdES / XMLDSig
# ---------------------------------------------------------------------------

# XMLDSig: firmas digitales XML (W3C)
NS_DS = "http://www.w3.org/2000/09/xmldsig#"

# XAdES: firmas electrónicas avanzadas XML (ETSI)
NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"

# ---------------------------------------------------------------------------
# URIs de algoritmos
# ---------------------------------------------------------------------------

# Canonicalization C14N 1.0 (sin comentarios)
C14N_ALGORITHM = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"

# Digest SHA-256
DIGEST_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"

# Firma RSA con SHA-256
SIGNATURE_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"

# Tipo de SignedProperties de XAdES
SIGNED_PROPS_TYPE = "http://uri.etsi.org/01903#SignedProperties"


# ---------------------------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------------------------

def _b64(value):
    """
    Codifica bytes en texto base64 sin espacios ni relleno '=' al final.

    Se usa para los valores de los elementos DigestValue, SignatureValue
    y X509Certificate en el XML firmado.

    Args:
        value: Datos binarios a codificar.

    Returns:
        str: Representación base64 sin caracteres '=' finales.
    """
    return base64.b64encode(value).decode("ascii").rstrip("=")


def _make_element(tag, attrib=None, text=None):
    """
    Crea un elemento XML de lxml con el tag, atributos y texto dados.

    Args:
        tag: Nombre del elemento (puede incluir namespace con {uri}).
        attrib: Diccionario opcional de atributos del elemento.
        text: Contenido de texto opcional del elemento.

    Returns:
        lxml.etree.Element: El elemento XML creado.
    """
    el = etree.Element(tag, attrib=attrib)
    if text is not None:
        el.text = text
    return el


def _canonicalize(element):
    """
    Canonicaliza un elemento XML usando C14N 1.0.

    La canonicalización es necesaria para calcular los digests
    de forma determinista: dos representaciones XML equivalentes
    deben producir el mismo hash.

    Args:
        element: Elemento lxml a canonicalizar.

    Returns:
        bytes: Representación canónica del elemento en C14N 1.0.
    """
    return etree.tostring(
        element, method="c14n", exclusive=False, with_comments=False
    )


# ---------------------------------------------------------------------------
# Función principal de firma XAdES
# ---------------------------------------------------------------------------

def sign_xades(data_bytes, private_key, certificate):
    """
    Firma datos XML con el formato XAdES-BES Enveloping.

    Produce un documento XML que contiene:
      - El documento original embeebido dentro de un ds:Object.
      - La firma XMLDSig (SignedInfo + SignatureValue + KeyInfo).
      - Las propiedades cualificadas XAdES (SigningTime, SigningCertificate).

    Args:
        data_bytes: Datos XML a firmar (bytes).
        private_key: Clave privada RSA del firmante.
        certificate: Certificado X.509 del firmante.

    Returns:
        bytes: Documento XML completo con la firma XAdES-BES Enveloping.
    """
    # --- Generar IDs únicos para los elementos principales ---
    signature_id = f"Signature-{uuid.uuid4().hex[:12]}"
    signed_props_id = f"SignedProperties-{uuid.uuid4().hex[:12]}"
    doc_ref_id = f"Document-{uuid.uuid4().hex[:12]}"

    # =====================================================================
    # 1. CONSTRUIR QualifyingProperties (XAdES)
    # =====================================================================
    # Debe construirse primero porque su digest se incluye en SignedInfo.

    # Fecha de firma en formato ISO 8601
    signing_time_str = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Digest del certificado en formato DER
    cert_der = certificate.public_bytes(Encoding.DER)
    cert_digest = hashlib.sha256(cert_der).digest()

    # DN del emisor en formato RFC 4514 (ej: "CN=AC RAIZ, O=..., C=ES")
    issuer_dn = certificate.issuer.rfc4514_string()

    # --- 1a. SignedSignatureProperties ---
    # Contiene: SigningTime, SigningCertificate
    signed_sig_props = _make_element(f"{{{NS_XADES}}}SignedSignatureProperties")

    # SigningTime: momento en que se realiza la firma
    signed_sig_props.append(
        _make_element(f"{{{NS_XADES}}}SigningTime", text=signing_time_str)
    )

    # SigningCertificate: digest e identificación del certificado firmante
    signing_cert = _make_element(f"{{{NS_XADES}}}SigningCertificate")
    cert_el = _make_element(f"{{{NS_XADES}}}Cert")

    # CertDigest: hash SHA-256 del certificado DER
    cert_digest_el = _make_element(f"{{{NS_XADES}}}CertDigest")
    cert_digest_el.append(
        _make_element(f"{{{NS_DS}}}DigestMethod", attrib={"Algorithm": DIGEST_SHA256})
    )
    cert_digest_el.append(
        _make_element(f"{{{NS_DS}}}DigestValue", text=_b64(cert_digest))
    )
    cert_el.append(cert_digest_el)

    # IssuerSerial: nombre del emisor + número de serie del certificado
    issuer_serial = _make_element(f"{{{NS_XADES}}}IssuerSerial")
    issuer_serial.append(
        _make_element(f"{{{NS_DS}}}X509IssuerName", text=issuer_dn)
    )
    issuer_serial.append(
        _make_element(
            f"{{{NS_DS}}}X509SerialNumber",
            text=str(certificate.serial_number),
        )
    )
    cert_el.append(issuer_serial)
    signing_cert.append(cert_el)
    signed_sig_props.append(signing_cert)

    # --- 1b. SignedProperties ---
    # Envuelve SignedSignatureProperties con un Id referenciable
    signed_props = _make_element(
        f"{{{NS_XADES}}}SignedProperties",
        attrib={"Id": signed_props_id},
    )
    signed_props.append(signed_sig_props)

    # --- 1c. QualifyingProperties ---
    # Apunta a la firma que contiene estas propiedades
    qual_props = _make_element(
        f"{{{NS_XADES}}}QualifyingProperties",
        attrib={"Target": f"#{signature_id}"},
    )
    qual_props.append(signed_props)

    # =====================================================================
    # 2. CONSTRUIR ds:Object con los datos a firmar
    # =====================================================================
    doc_obj = _make_element(
        f"{{{NS_DS}}}Object",
        attrib={"Id": doc_ref_id},
    )
    try:
        # Intentar parsear los datos como XML válido
        input_doc = etree.fromstring(data_bytes)
        doc_obj.append(input_doc)
    except etree.XMLSyntaxError:
        # Si no es XML válido, embeberlo como base64 en un elemento envoltorio
        wrapper = _make_element("DocumentData")
        wrapper.text = _b64(data_bytes)
        doc_obj.append(wrapper)

    # =====================================================================
    # 3. CONSTRUIR KeyInfo (información de la clave)
    # =====================================================================
    key_info = _make_element(f"{{{NS_DS}}}KeyInfo")
    x509_data = _make_element(f"{{{NS_DS}}}X509Data")
    # Incluir el certificado X.509 completo en base64
    cert_b64 = _b64(certificate.public_bytes(Encoding.DER))
    x509_data.append(
        _make_element(f"{{{NS_DS}}}X509Certificate", text=cert_b64)
    )
    key_info.append(x509_data)

    # =====================================================================
    # 4. CONSTRUIR SignedInfo
    # =====================================================================
    # Es el bloque principal que se firma criptográficamente.
    # Contiene referencias a los datos firmados con sus respectivos digests.

    signed_info = _make_element(f"{{{NS_DS}}}SignedInfo")

    # Método de canonicalización: C14N 1.0
    signed_info.append(
        _make_element(
            f"{{{NS_DS}}}CanonicalizationMethod",
            attrib={"Algorithm": C14N_ALGORITHM},
        )
    )

    # Método de firma: RSA-SHA256
    signed_info.append(
        _make_element(
            f"{{{NS_DS}}}SignatureMethod",
            attrib={"Algorithm": SIGNATURE_RSA_SHA256},
        )
    )

    # --- Referencia #1: SignedProperties (obligatorio en XAdES-BES) ---
    ds_sp = _make_element(f"{{{NS_DS}}}DigestValue")
    ref_sp = _make_element(
        f"{{{NS_DS}}}Reference",
        attrib={
            "Type": SIGNED_PROPS_TYPE,
            "URI": f"#{signed_props_id}",
        },
    )
    ref_sp.append(
        _make_element(
            f"{{{NS_DS}}}DigestMethod", attrib={"Algorithm": DIGEST_SHA256}
        )
    )
    ref_sp.append(ds_sp)
    signed_info.append(ref_sp)

    # --- Referencia #2: Documento a firmar ---
    ds_doc = _make_element(f"{{{NS_DS}}}DigestValue")
    ref_doc = _make_element(
        f"{{{NS_DS}}}Reference",
        attrib={"URI": f"#{doc_ref_id}"},
    )
    ref_doc.append(
        _make_element(
            f"{{{NS_DS}}}DigestMethod", attrib={"Algorithm": DIGEST_SHA256}
        )
    )
    ref_doc.append(ds_doc)
    signed_info.append(ref_doc)

    # =====================================================================
    # 5. CALCULAR LOS DIGESTS
    # =====================================================================
    # Se calculan DESPUÉS de construir SignedInfo pero ANTES de firmar.
    # Los digests se calculan sobre la forma canónica C14N de cada elemento.

    # Digest de SignedProperties
    sp_digest = hashlib.sha256(_canonicalize(signed_props)).digest()
    ds_sp.text = _b64(sp_digest)

    # Digest del documento
    doc_c14n = _canonicalize(doc_obj)
    doc_digest = hashlib.sha256(doc_c14n).digest()
    ds_doc.text = _b64(doc_digest)

    # =====================================================================
    # 6. CALCULAR LA FIRMA (SignatureValue)
    # =====================================================================
    # Se firma la forma canónica de SignedInfo con la clave privada
    signed_info_c14n = _canonicalize(signed_info)
    signature_value = private_key.sign(
        signed_info_c14n,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    sig_val_el = _make_element(
        f"{{{NS_DS}}}SignatureValue",
        text=_b64(signature_value),
    )

    # =====================================================================
    # 7. ENSAMBLAR EL ELEMENTO Signature COMPLETO
    # =====================================================================
    signature_el = _make_element(
        f"{{{NS_DS}}}Signature",
        attrib={"Id": signature_id},
    )
    signature_el.append(signed_info)
    signature_el.append(sig_val_el)
    signature_el.append(key_info)

    # ds:Object envolviendo QualifyingProperties (XAdES)
    qual_obj = _make_element(f"{{{NS_DS}}}Object")
    qual_obj.append(qual_props)
    signature_el.append(qual_obj)

    # ds:Object envolviendo el documento firmado
    signature_el.append(doc_obj)

    # =====================================================================
    # 8. SERIALIZAR A BYTES
    # =====================================================================
    result = etree.tostring(
        signature_el,
        xml_declaration=True,   # Incluir <?xml version="1.0" encoding="UTF-8"?>
        encoding="UTF-8",
        pretty_print=True,       # Formatear con sangría para legibilidad
    )

    return result
