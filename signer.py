import datetime
import endesive.pdf.cms
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization, hashes

def load_certificate(path, password):
    """
    Loads a PKCS#12 certificate from a file.
    """
    with open(path, "rb") as fp:
        p12_data = fp.read()
    
    password_bytes = password.encode('utf-8') if password else None
    
    try:
        p12 = pkcs12.load_key_and_certificates(p12_data, password_bytes)
        return p12
    except ValueError:
        raise ValueError("Invalid password or corrupted certificate file.")
    except Exception as e:
        raise RuntimeError(f"Failed to load certificate: {str(e)}")

def sign_pdf(input_pdf_path, cert_path, password, output_pdf_path):
    """
    Signs a PDF file with the given certificate and password.
    """
    # Load certificate and private key
    private_key, certificate, additional_certificates = load_certificate(cert_path, password)
    
    # Prepare date for signing
    date = datetime.datetime.now(datetime.timezone.utc)
    date_str = date.strftime('D:%Y%m%d%H%M%SZ')

    # Data to sign
    dct = {
        "sigflags": 3,
        "sigpage": 0,
        "contact": "",
        "location": "",
        "signingdate": date_str,
        "reason": "Signed with PyFirma",
    }
    
    with open(input_pdf_path, 'rb') as fp:
        data = fp.read()
    
    # Sign
    datas = endesive.pdf.cms.sign(
        data,
        dct,
        private_key,
        certificate,
        additional_certificates,
        'sha256'
    )
    
    # Save output
    with open(output_pdf_path, 'wb') as fp:
        fp.write(data)
        fp.write(datas)
