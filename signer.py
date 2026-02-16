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

def create_watermark(text, vertical_left=False):
    """
    Creates a watermark PDF page with the given text using ReportLab.
    Returns the PDF bytes.
    """
    packet = io.BytesIO()
    # Create a new PDF with Reportlab
    can = canvas.Canvas(packet, pagesize=A4)
    can.setFont("Helvetica", 8)
    
    if vertical_left:
        # Rotate 90 degrees
        can.rotate(90)
        
        # Position: Left Margin, Middle
        # When rotated 90 degrees:
        # X becomes vertical axis (bottom to top)
        # Y becomes horizontal axis (left to right, but inverted relative to page)
        
        # We need to coordinate transform or just simpler logic:
        # Translate to desired origin, then rotate.
        
        # Reset and use translate/rotate context
        can = canvas.Canvas(packet, pagesize=A4)
        can.setFont("Helvetica", 8) 
        
        # Target position: Left margin (e.g., 20) and Centered vertically (e.g. A4 height / 2)
        # A4 size is roughly 595.27 x 841.89 points
        page_width, page_height = A4
        
        # We want the text to run up the left side.
        # Origin for string will be (margin, center - half_text_length)
        # But we want rotation.
        
        can.saveState()
        can.translate(20, page_height / 2)
        can.rotate(90)
        
        # Now we are drawing along the Y axis of the page, centered
        # Text alignment
        lines = text.split('\n')
        total_lines = len(lines)
        
        # Draw centered around the origin
        # Calculate width of text to center it? 
        # Actually, if we rotate 90, the text baseline runs up. 
        # We can just drawString(0, 0, text) and it will start at (20, half_height) and go UP.
        
        # Let's adjust slightly to center the block of text if multi-line
        # Multi-line in rotated context:
        # Each line is stacked 'below' the previous in Y, which means to the LEFT in unrotated space.
        # So we need to draw lines at standard offsets.
        
        # Center the block horizontally (in unrotated space, so Y in rotated space)
        # We start at (0,0) which is (20, page_height/2)
        
        # Let's approximate starting X (in rotated sys) to center the text block length?
        # A simpler approach for "Firmado por..." date is usually just starting from a point.
        # Let's start the text block centered on the page height.
        text_width = can.stringWidth(lines[0], "Helvetica", 8) # approximate with first line
        start_x = - (text_width / 2)
        
        for line in lines:
            line_width = can.stringWidth(line, "Helvetica", 8)
            # Center each line relative to the center point
            can.drawString(-(line_width / 2), 0, line)
            # Move "down" (which is left in page coordinates) for next line
            # But wait, standard text usually reads left-to-right.
            # "Rotated 90 degrees left" -> Text runs bottom-to-top.
            
            # If we want the lines stacked, we move 'down' in Y.
            # Y axis in rotated space points to the 'left' of the page.
            # So decreasing Y moves towards the left edge.
            can.translate(0, -10) 
            
        can.restoreState()
        
    else:
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

def sign_pdf(input_pdf_path, cert_path, password, output_pdf_path, visible=False, vertical_left=False, all_pages=False):
    """
    Signs a PDF file with the given certificate and password.
    Optionally adds a visible signature stamp.
    If all_pages is True, the visible signature will be added to all pages.
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
        watermark_pdf_bytes = create_watermark(watermark_text, vertical_left=vertical_left)
        
        # Merge watermark
        watermark_pdf = PdfReader(watermark_pdf_bytes)
        original_pdf = PdfReader(io.BytesIO(data))
        pdf_writer = PdfWriter()
        
        if all_pages:
            # Apply signature to all pages
            for page_num in range(len(original_pdf.pages)):
                page = original_pdf.pages[page_num]
                page.merge_page(watermark_pdf.pages[0])
                pdf_writer.add_page(page)
        else:
            # Merge into the first page only (index 0)
            first_page = original_pdf.pages[0]
            first_page.merge_page(watermark_pdf.pages[0])
            pdf_writer.add_page(first_page)
            
            # Add remaining pages without signature
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
