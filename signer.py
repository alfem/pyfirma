import datetime
import io
import endesive.pdf.cms
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.x509.oid import NameOID
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader, PdfWriter

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

def create_watermark(text):
    """
    Creates a watermark PDF page with the given text using ReportLab.
    Returns the PDF bytes.
    """
    packet = io.BytesIO()
    # Create a new PDF with Reportlab
    can = canvas.Canvas(packet, pagesize=A4)
    can.setFont("Helvetica", 8)
    
    # Position: Bottom Left, slightly offset
    x = 50
    y = 50
    
    # Split text into lines
    lines = text.split('\n')
    for line in lines:
        can.drawString(x, y, line)
        y -= 10 # Line spacing
        
    can.save()

    # Move to the beginning of the StringIO buffer
    packet.seek(0)
    return packet

def sign_pdf(input_pdf_path, cert_path, password, output_pdf_path, visible=False):
    """
    Signs a PDF file with the given certificate and password.
    Optionally adds a visible signature stamp.
    """
    # Load certificate and private key
    private_key, certificate, additional_certificates = load_certificate(cert_path, password)
    
    # Prepare date for signing
    date = datetime.datetime.now(datetime.timezone.utc)
    date_str = date.strftime('D:%Y%m%d%H%M%SZ')

    # Read original PDF data
    with open(input_pdf_path, 'rb') as fp:
        data = fp.read()

    # If visible signature is requested, modify the PDF data before signing
    if visible:
        # Extract Common Name (CN) from certificate
        subject_name = "Unknown"
        for attribute in certificate.subject:
            if attribute.oid == NameOID.COMMON_NAME:
                subject_name = attribute.value
                break
        
        # Prepare text
        local_date = datetime.datetime.now()
        formatted_date = local_date.strftime("%d/%m/%Y %H:%M:%S")
        watermark_text = f"Firmado por: {subject_name}\nFecha: {formatted_date}"
        
        # Create watermark
        watermark_pdf_bytes = create_watermark(watermark_text)
        
        # Merge watermark
        watermark_pdf = PdfReader(watermark_pdf_bytes)
        original_pdf = PdfReader(io.BytesIO(data))
        pdf_writer = PdfWriter()
        
        # Merge into the first page (index 0)
        first_page = original_pdf.pages[0]
        first_page.merge_page(watermark_pdf.pages[0])
        pdf_writer.add_page(first_page)
        
        # Add remaining pages
        for page_num in range(1, len(original_pdf.pages)):
            pdf_writer.add_page(original_pdf.pages[page_num])
            
        # Write modified PDF to bytes for signing
        output_buffer = io.BytesIO()
        pdf_writer.write(output_buffer)
        data = output_buffer.getvalue()

    # Data to sign (Cryptographic Metadata)
    dct = {
        "sigflags": 3,
        "sigpage": 0,
        "contact": "",
        "location": "",
        "signingdate": date_str,
        "reason": "Signed with PyFirma",
    }
    
    # Sign (using the potentially modified data)
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
