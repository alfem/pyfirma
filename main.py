"""
Punto de entrada principal de PyFirma.

Soporta dos modos de ejecución:
  1. Modo GUI (interfaz gráfica): se ejecuta sin argumentos.
  2. Modo CLI (línea de comandos): se ejecuta con --input y --cert.

También actúa como manejador del protocolo personalizado afirma://,
registrado en el sistema para que el navegador invoque a PyFirma
al encontrar enlaces de este tipo.

Gestiona la detección de instancia única mediante un archivo de
bloqueo (lock file), redirigiendo nuevas invocaciones a la
instancia ya en ejecución.
"""

import sys
import os
import argparse
import tempfile
from urllib.parse import urlparse, parse_qs
from gui import App
from cli import run_cli_mode


# --- Archivos para el control de instancia única ---
# LOCK_FILE: contiene el PID de la instancia en ejecución.
# REDIRECT_FILE: usado para pasar URLs afirma:// de una instancia nueva a la existente.
LOCK_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.lock")
REDIRECT_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.redirect")


def is_already_running():
    """
    Comprueba si ya hay otra instancia de PyFirma en ejecución.

    Lee el PID del archivo de bloqueo y verifica si el proceso
    correspondiente sigue vivo mediante os.kill(pid, 0).

    Returns:
        int or None: El PID de la instancia existente, o None si no hay ninguna.
    """
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip())
            # os.kill con señal 0 NO mata el proceso — solo comprueba si existe
            os.kill(pid, 0)
            return pid
        except (ValueError, OSError, ProcessLookupError):
            # Archivo de bloqueo obsoleto o proceso muerto — se ignora
            pass
    return None


def forward_to_existing(url):
    """
    Reenvía una URL afirma:// a la instancia de PyFirma ya en ejecución.

    Escribe la URL en REDIRECT_FILE; la instancia existente la recoge
    mediante su polling periódico y arranca nuevos servidores si es necesario.

    Args:
        url: La URL afirma:// completa a reenviar.
    """
    with open(REDIRECT_FILE, 'w') as f:
        f.write(url + '\n')


def cleanup_lock():
    """
    Elimina el archivo de bloqueo al cerrar la aplicación.

    Se llama al finalizar la instancia principal para liberar el lock.
    """
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def main():
    """
    Función principal de PyFirma.

    Flujo de decisión:
    1. Si se invoca con una URL afirma:// como único argumento:
       - Si ya hay una instancia corriendo, reenvía la URL y sale.
       - Si no, crea el lock, parsea los puertos y lanza la GUI interceptora.
    2. Si se invoca con argumentos de CLI (--input, --cert, etc.):
       - Ejecuta el modo CLI directamente.
    3. Sin argumentos:
       - Lanza la interfaz gráfica normal.
    """
    # --- Caso 1: Invocación como manejador de protocolo afirma:// ---
    # El sistema llama a: pyfirma "afirma://websocket?ports=XXXXX&..."
    if len(sys.argv) == 2 and sys.argv[1].startswith("afirma://"):
        afirma_url = sys.argv[1]

        # Comprobar si ya hay otra instancia corriendo
        existing_pid = is_already_running()
        if existing_pid:
            # Reenviar la URL a la instancia existente y salir
            forward_to_existing(afirma_url)
            return

        # Primera instancia — escribir nuestro PID en el archivo de bloqueo
        try:
            with open(LOCK_FILE, 'w') as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        # Parsear la URL para extraer los puertos
        parsed = urlparse(afirma_url)
        params = parse_qs(parsed.query)

        afirma_ports = None
        # Soportar tanto modo websocket como modo service (ambos pasan puertos)
        if parsed.netloc in ("websocket", "service") or \
           (parsed.path in ("websocket", "service")):
            if 'ports' in params:
                ports_str = params['ports'][0]
                afirma_ports = [
                    int(p.strip())
                    for p in ports_str.split(',')
                    if p.strip().isdigit()
                ]

        # Lanzar la GUI en modo interceptor
        app = App(afirma_url=afirma_url, afirma_ports=afirma_ports)
        app.mainloop()
        cleanup_lock()
        return

    # --- Caso 2 y 3: Modo CLI o GUI normal ---
    parser = argparse.ArgumentParser(description="PyFirma - Python PDF Signer")
    parser.add_argument("-i", "--input", help="Path to input PDF file")
    parser.add_argument("-c", "--cert", help="Path to .p12/.pfx certificate file")
    parser.add_argument("-p", "--password", help="Certificate password")
    parser.add_argument("-o", "--output", help="Path to output signed PDF file")
    parser.add_argument(
        "--visible", action="store_true",
        help="Add visible signature stamp",
    )
    parser.add_argument(
        "--vertical-left", action="store_true",
        help="Place visible signature vertically on the left margin",
    )
    parser.add_argument(
        "--all-pages", action="store_true",
        help="Apply visible signature to all pages (requires --visible)",
    )

    args = parser.parse_args()

    # Si se pasó algún argumento, usar modo CLI
    if len(sys.argv) > 1:
        # Validar argumentos obligatorios para CLI
        if not args.input or not args.cert:
            parser.error("CLI mode requires --input and --cert")

        run_cli_mode(args)
    else:
        # Sin argumentos: modo GUI normal
        app = App()
        app.mainloop()


if __name__ == "__main__":
    main()
