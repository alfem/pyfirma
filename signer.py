"""
Módulo de firma de PDF (formato PAdES) para PyFirma.

Proporciona las funciones principales para:
  - Cargar certificados PKCS#12 (.p12/.pfx).
  - Crear marcas de agua visibles en PDF.
  - Firmar digitalmente documentos PDF con certificados X.509.

Utiliza la biblioteca endesive para la firma criptográfica PAdES,
cryptography para la carga de claves, y pypdf + reportlab para
la manipulación de documentos PDF y marcas de agua.
"""

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
    Carga un certificado PKCS#12 desde un archivo.

    Los archivos .p12/.pfx contienen la clave privada, el certificado
    y opcionalmente la cadena de certificación.

    Args:
        path: Ruta al archivo .p12 o .pfx.
        password: Contraseña del certificado (puede ser cadena vacía).

    Returns:
        tuple: (clave_privada, certificado, certificados_adicionales)
               tal como lo devuelve pkcs12.load_key_and_certificates.

    Raises:
        ValueError: Si la contraseña es incorrecta o el archivo está corrupto.
        RuntimeError: Si ocurre cualquier otro error al cargar el certificado.
    """
    # Leer el archivo PKCS#12 completo en memoria
    with open(path, "rb") as fp:
        p12_data = fp.read()

    # Convertir la contraseña a bytes (None si está vacía)
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
    Crea una página PDF con una marca de agua (firma visible).

    Genera un PDF de una sola página usando ReportLab que contiene
    el texto de firma. Este PDF se superpone posteriormente al
    documento original mediante merge_page.

    Args:
        text: Texto a mostrar en la marca de agua (ej. "Firmado por: ...").
        vertical_left: Si es True, coloca el texto rotado 90° en el
                       margen izquierdo. Si es False, lo coloca en la
                       esquina inferior izquierda.

    Returns:
        io.BytesIO: Buffer con los bytes del PDF de la marca de agua.
    """
    packet = io.BytesIO()
    # Crear un nuevo PDF con ReportLab en tamaño A4
    can = canvas.Canvas(packet, pagesize=A4)
    can.setFont("Helvetica", 8)

    if vertical_left:
        # --- Modo vertical en margen izquierdo ---
        # Se rota el texto 90° para que se lea de abajo hacia arriba
        # en el margen izquierdo de la página.

        # Obtener dimensiones de página A4 (aprox. 595.27 x 841.89 puntos)
        page_width, page_height = A4

        # Guardar el estado gráfico actual para restaurarlo después
        can.saveState()

        # Trasladar el origen al punto (20, mitad de la altura de página)
        # y rotar 90° — el texto se dibujará hacia arriba desde ese punto
        can.translate(20, page_height / 2)
        can.rotate(90)

        # Dibujar cada línea de texto centrada respecto al punto de origen
        lines = text.split('\n')
        for line in lines:
            line_width = can.stringWidth(line, "Helvetica", 8)
            # Centrar la línea horizontalmente
            can.drawString(-(line_width / 2), 0, line)
            # Desplazar hacia abajo en el sistema rotado (equivale a
            # moverse hacia la izquierda en coordenadas de página)
            can.translate(0, -10)

        can.restoreState()
    else:
        # --- Modo normal: esquina inferior izquierda ---
        # Posición inicial: (50, 50) puntos desde la esquina inferior izquierda
        x = 50
        y = 50

        lines = text.split('\n')
        for line in lines:
            can.drawString(x, y, line)
            y -= 10  # Espaciado entre líneas: 10 puntos

    can.save()

    # Retroceder el buffer al principio para que pueda ser leído
    packet.seek(0)
    return packet


def sign_pdf(
    input_pdf_path, cert_path, password, output_pdf_path,
    visible=False, vertical_left=False, all_pages=False,
):
    """
    Firma digitalmente un archivo PDF con un certificado.

    Realiza una firma PAdES (PDF Advanced Electronic Signature) sobre
    el documento. Opcionalmente, añade una marca de agua visible con
    los datos del firmante.

    Flujo:
    1. Cargar certificado y clave privada.
    2. Leer el PDF original.
    3. Si se solicita firma visible, generar la marca de agua y
       fusionarla con el PDF (primera página o todas).
    4. Calcular y embeber la firma criptográfica PAdES.
    5. Guardar el PDF firmado.

    Args:
        input_pdf_path: Ruta al PDF de entrada.
        cert_path: Ruta al archivo de certificado .p12/.pfx.
        password: Contraseña del certificado.
        output_pdf_path: Ruta donde guardar el PDF firmado.
        visible: Si es True, añade una marca de agua visible.
        vertical_left: Si es True, coloca la marca en el margen izquierdo.
        all_pages: Si es True, aplica la marca a todas las páginas
                   (requiere visible=True).

    Raises:
        ValueError: Si la contraseña del certificado es incorrecta.
        RuntimeError: Si ocurre un error durante la carga o firma.
    """
    # --- 1. Cargar certificado y clave privada ---
    private_key, certificate, additional_certificates = \
        load_certificate(cert_path, password)

    # --- 2. Preparar la fecha de firma en formato PDF ---
    # Formato requerido por el estándar PDF: D:YYYYMMDDHHmmSSZ
    date = datetime.datetime.now(datetime.timezone.utc)
    date_str = date.strftime('D:%Y%m%d%H%M%SZ')

    # --- 3. Leer el PDF original completo en memoria ---
    with open(input_pdf_path, 'rb') as fp:
        data = fp.read()

    # --- 4. Añadir marca de agua visible si se solicita ---
    if visible:
        # Extraer el nombre común (CN) del certificado para la marca
        subject_name = "Unknown"
        for attribute in certificate.subject:
            if attribute.oid == NameOID.COMMON_NAME:
                subject_name = attribute.value
                break

        # Preparar el texto de la marca de agua
        local_date = datetime.datetime.now()
        formatted_date = local_date.strftime("%d/%m/%Y %H:%M:%S")
        watermark_text = f"Firmado por: {subject_name}\nFecha: {formatted_date}"

        # Generar el PDF de la marca de agua
        watermark_pdf_bytes = create_watermark(
            watermark_text, vertical_left=vertical_left
        )

        # Fusionar la marca de agua con el PDF original
        watermark_pdf = PdfReader(watermark_pdf_bytes)
        original_pdf = PdfReader(io.BytesIO(data))
        pdf_writer = PdfWriter()

        if all_pages:
            # Aplicar la marca a todas las páginas del documento
            for page_num in range(len(original_pdf.pages)):
                page = original_pdf.pages[page_num]
                page.merge_page(watermark_pdf.pages[0])
                pdf_writer.add_page(page)
        else:
            # Aplicar la marca solo a la primera página (índice 0)
            first_page = original_pdf.pages[0]
            first_page.merge_page(watermark_pdf.pages[0])
            pdf_writer.add_page(first_page)

            # Añadir el resto de páginas sin marca
            for page_num in range(1, len(original_pdf.pages)):
                pdf_writer.add_page(original_pdf.pages[page_num])

        # Serializar el PDF modificado a bytes para la firma
        output_buffer = io.BytesIO()
        pdf_writer.write(output_buffer)
        data = output_buffer.getvalue()

    # --- 5. Preparar metadatos de la firma criptográfica ---
    dct = {
        "sigflags": 3,        # 3 = firma PAdES (certificación + aprobación)
        "sigpage": 0,         # Página donde se coloca la firma invisible (0-based)
        "contact": "",        # Información de contacto del firmante
        "location": "",       # Ubicación geográfica de la firma
        "signingdate": date_str,
        "reason": "Signed with PyFirma",
    }

    # --- 6. Calcular la firma criptográfica ---
    # endesive.pdf.cms.sign calcula el hash del documento y lo firma
    # con la clave privada, produciendo un objeto CMS (PKCS#7) embebido
    datas = endesive.pdf.cms.sign(
        data,
        dct,
        private_key,
        certificate,
        additional_certificates,
        'sha256',  # Algoritmo de hash
    )

    # --- 7. Guardar el PDF firmado ---
    # El PDF firmado es el documento original + la firma CMS al final
    with open(output_pdf_path, 'wb') as fp:
        fp.write(data)   # Documento PDF (con o sin marca de agua)
        fp.write(datas)  # Firma CMS emebebida
