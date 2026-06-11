"""
Generador de PDF de prueba para PyFirma.

Crea un documento PDF simple (tamaño A4) con un texto de prueba.
Útil para verificar el funcionamiento del firmador sin necesidad
de un documento externo.
"""

from PIL import Image, ImageDraw


def create_pdf(filename):
    """
    Crea un archivo PDF con un texto de prueba.

    Genera una imagen en blanco de tamaño A4 aproximado (595x842 px)
    con el texto "This is a test PDF for signing." y la guarda como PDF.

    Args:
        filename: Ruta donde se guardará el archivo PDF generado.
    """
    # Crear imagen en blanco de tamaño A4 aproximado (595x842 puntos)
    img = Image.new('RGB', (595, 842), color='white')
    d = ImageDraw.Draw(img)
    # Dibujar texto de prueba en la posición (50, 50)
    d.text((50, 50), "This is a test PDF for signing.", fill=(0, 0, 0))
    # Guardar como PDF
    img.save(filename, "PDF")


# --- Punto de entrada: genera dummy.pdf si se ejecuta directamente ---
if __name__ == "__main__":
    create_pdf("dummy.pdf")
