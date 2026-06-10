"""XAdES (XML Advanced Electronic Signature) signing for PyFirma.

Implements XAdES-BES Enveloping following ETSI TS 101 903 v1.3.2,
built on XMLDSig (W3C) using lxml for canonicalization and
cryptography for RSA-SHA256 signing.
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

# XML namespaces
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"

# Algorithm URIs
C14N_ALGORITHM = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
DIGEST_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
SIGNATURE_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"

# XAdES SignedProperties type
SIGNED_PROPS_TYPE = "http://uri.etsi.org/01903#SignedProperties"


def _b64(value):
    """Encode bytes as base64 text without whitespace."""
    return base64.b64encode(value).decode("ascii").rstrip("=")


def _make_element(tag, attrib=None, text=None):
    """Create an lxml Element."""
    el = etree.Element(tag, attrib=attrib)
    if text is not None:
        el.text = text
    return el


def _canonicalize(element):
    """Canonicalize (C14N 1.0) an XML element tree."""
    return etree.tostring(
        element, method="c14n", exclusive=False, with_comments=False
    )


def sign_xades(data_bytes, private_key, certificate):
    """Sign arbitrary XML data with XAdES-BES Enveloping.

    Args:
        data_bytes: The XML data bytes to sign.
        private_key: RSA private key.
        certificate: X.509 certificate.

    Returns:
        bytes: The complete signed XAdES XML document.
    """
    # Generate unique IDs
    signature_id = f"Signature-{uuid.uuid4().hex[:12]}"
    signed_props_id = f"SignedProperties-{uuid.uuid4().hex[:12]}"
    doc_ref_id = f"Document-{uuid.uuid4().hex[:12]}"

    # --- Build QualifyingProperties (must be built first for digest) ---
    signing_time_str = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Certificate digest (DER-encoded)
    cert_der = certificate.public_bytes(Encoding.DER)
    cert_digest = hashlib.sha256(cert_der).digest()

    # Issuer DN
    issuer_dn = certificate.issuer.rfc4514_string()

    # Build SignedSignatureProperties
    signed_sig_props = _make_element(f"{{{NS_XADES}}}SignedSignatureProperties")

    # SigningTime
    signed_sig_props.append(
        _make_element(f"{{{NS_XADES}}}SigningTime", text=signing_time_str)
    )

    # SigningCertificate
    signing_cert = _make_element(f"{{{NS_XADES}}}SigningCertificate")
    cert_el = _make_element(f"{{{NS_XADES}}}Cert")
    cert_digest_el = _make_element(f"{{{NS_XADES}}}CertDigest")
    cert_digest_el.append(
        _make_element(f"{{{NS_DS}}}DigestMethod", attrib={"Algorithm": DIGEST_SHA256})
    )
    cert_digest_el.append(
        _make_element(f"{{{NS_DS}}}DigestValue", text=_b64(cert_digest))
    )
    cert_el.append(cert_digest_el)

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

    # SignedProperties
    signed_props = _make_element(
        f"{{{NS_XADES}}}SignedProperties",
        attrib={"Id": signed_props_id},
    )
    signed_props.append(signed_sig_props)

    # QualifyingProperties
    qual_props = _make_element(
        f"{{{NS_XADES}}}QualifyingProperties",
        attrib={"Target": f"#{signature_id}"},
    )
    qual_props.append(signed_props)

    # --- Wrap document data in ds:Object ---
    doc_obj = _make_element(
        f"{{{NS_DS}}}Object",
        attrib={"Id": doc_ref_id},
    )
    try:
        input_doc = etree.fromstring(data_bytes)
        doc_obj.append(input_doc)
    except etree.XMLSyntaxError:
        # Not valid XML, embed as base64 in a wrapper element
        wrapper = _make_element("DocumentData")
        wrapper.text = _b64(data_bytes)
        doc_obj.append(wrapper)

    # --- Build KeyInfo ---
    key_info = _make_element(f"{{{NS_DS}}}KeyInfo")
    x509_data = _make_element(f"{{{NS_DS}}}X509Data")
    cert_b64 = _b64(certificate.public_bytes(Encoding.DER))
    x509_data.append(
        _make_element(f"{{{NS_DS}}}X509Certificate", text=cert_b64)
    )
    key_info.append(x509_data)

    # --- Build SignedInfo ---
    signed_info = _make_element(f"{{{NS_DS}}}SignedInfo")

    # Canonicalization Method
    signed_info.append(
        _make_element(
            f"{{{NS_DS}}}CanonicalizationMethod",
            attrib={"Algorithm": C14N_ALGORITHM},
        )
    )

    # Signature Method
    signed_info.append(
        _make_element(
            f"{{{NS_DS}}}SignatureMethod",
            attrib={"Algorithm": SIGNATURE_RSA_SHA256},
        )
    )

    # Reference to SignedProperties
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

    # Reference to document data
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

    # Compute digests BEFORE canonicalizing SignedInfo
    sp_digest = hashlib.sha256(_canonicalize(signed_props)).digest()
    ds_sp.text = _b64(sp_digest)

    doc_c14n = _canonicalize(doc_obj)
    doc_digest = hashlib.sha256(doc_c14n).digest()
    ds_doc.text = _b64(doc_digest)

    # --- Compute the signature ---
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

    # --- Assemble Signature element ---
    signature_el = _make_element(
        f"{{{NS_DS}}}Signature",
        attrib={"Id": signature_id},
    )
    signature_el.append(signed_info)
    signature_el.append(sig_val_el)
    signature_el.append(key_info)

    # ds:Object wrapping QualifyingProperties
    qual_obj = _make_element(f"{{{NS_DS}}}Object")
    qual_obj.append(qual_props)
    signature_el.append(qual_obj)

    # ds:Object wrapping document data
    signature_el.append(doc_obj)

    # --- Serialize ---
    result = etree.tostring(
        signature_el,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )

    return result
