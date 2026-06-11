"""
Modo línea de comandos (CLI) de PyFirma.

Permite firmar documentos PDF desde la terminal sin necesidad de
abrir la interfaz gráfica. Útil para automatización y scripts.
"""

import getpass
import sys
import os
from signer import sign_pdf


def run_cli_mode(args):
    """
    Ejecuta el flujo de firma en modo CLI.

    Recibe los argumentos parseados de la línea de comandos y:
    1. Valida que existan el PDF de entrada y el certificado.
    2. Solicita la contraseña si no se proporcionó como argumento.
    3. Genera un nombre de salida automático si no se especificó.
    4. Invoca la función de firma y muestra el resultado.

    Args:
        args: Namespace de argparse con los atributos:
              input, cert, password, output, visible,
              vertical_left, all_pages.
    """
    # --- Extraer parámetros de los argumentos ---
    input_pdf = args.input
    cert_path = args.cert
    password = args.password
    output_pdf = args.output

    # --- Validar que el PDF de entrada existe ---
    if not os.path.exists(input_pdf):
        print(f"Error: Input PDF not found: {input_pdf}", file=sys.stderr)
        sys.exit(1)

    # --- Validar que el archivo de certificado existe ---
    if not os.path.exists(cert_path):
        print(f"Error: Certificate file not found: {cert_path}", file=sys.stderr)
        sys.exit(1)

    # --- Solicitar contraseña si no se pasó por argumento ---
    if not password:
        try:
            password = getpass.getpass("Enter certificate password: ")
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            sys.exit(1)

    # --- Generar nombre del archivo de salida si no se especificó ---
    # Por defecto: <nombre_original>_signed.<extensión>
    if not output_pdf:
        base, ext = os.path.splitext(input_pdf)
        output_pdf = f"{base}_signed{ext}"

    print(f"Signing {input_pdf}...")

    # --- Ejecutar la firma ---
    try:
        # Leer opciones de firma visible desde los argumentos
        # (por defecto False si no se pasaron)
        visible = getattr(args, 'visible', False)
        vertical_left = getattr(args, 'vertical_left', False)
        all_pages = getattr(args, 'all_pages', False)

        sign_pdf(
            input_pdf, cert_path, password, output_pdf,
            visible=visible,
            vertical_left=vertical_left,
            all_pages=all_pages,
        )
        print(f"Success! Signed file saved to: {output_pdf}")
    except Exception as e:
        print(f"Error during signing: {str(e)}", file=sys.stderr)
        sys.exit(1)
