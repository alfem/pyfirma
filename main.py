import sys
import os
import argparse
import tempfile
from urllib.parse import urlparse, parse_qs
from gui import App
from cli import run_cli_mode

# Singleton detection: prevent duplicate PyFirma instances
LOCK_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.lock")
REDIRECT_FILE = os.path.join(tempfile.gettempdir(), "pyfirma.redirect")


def is_already_running():
    """Check if another PyFirma instance is already running.
    Returns the PID if yes, None otherwise."""
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # Does NOT kill — just checks if process exists
            return pid
        except (ValueError, OSError, ProcessLookupError):
            # Stale lock file or dead process
            pass
    return None


def forward_to_existing(url):
    """Forward an afirma:// URL to the already-running PyFirma instance."""
    with open(REDIRECT_FILE, 'w') as f:
        f.write(url + '\n')


def cleanup_lock():
    """Remove lock file on exit."""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def main():
    # Detect if we are called as a generic URL handler for afirma://
    if len(sys.argv) == 2 and sys.argv[1].startswith("afirma://"):
        afirma_url = sys.argv[1]

        # Check if another instance is already running
        existing_pid = is_already_running()
        if existing_pid:
            # Forward the URL to the existing instance and exit
            forward_to_existing(afirma_url)
            return

        # First instance — write our PID to the lock file
        try:
            with open(LOCK_FILE, 'w') as f:
                f.write(str(os.getpid()))
        except Exception:
            pass

        parsed = urlparse(afirma_url)
        params = parse_qs(parsed.query)

        afirma_ports = None
        # Support either websocket or service modes (both pass ports)
        if parsed.netloc in ("websocket", "service") or (parsed.path in ("websocket", "service")):
            if 'ports' in params:
                ports_str = params['ports'][0]
                afirma_ports = [int(p.strip()) for p in ports_str.split(',') if p.strip().isdigit()]

        app = App(afirma_url=afirma_url, afirma_ports=afirma_ports)
        app.mainloop()
        cleanup_lock()
        return

    parser = argparse.ArgumentParser(description="PyFirma - Python PDF Signer")
    parser.add_argument("-i", "--input", help="Path to input PDF file")
    parser.add_argument("-c", "--cert", help="Path to .p12/.pfx certificate file")
    parser.add_argument("-p", "--password", help="Certificate password")
    parser.add_argument("-o", "--output", help="Path to output signed PDF file")
    parser.add_argument("--visible", action="store_true", help="Add visible signature stamp")
    parser.add_argument("--vertical-left", action="store_true", help="Place visible signature vertically on the left margin")
    parser.add_argument("--all-pages", action="store_true", help="Apply visible signature to all pages (requires --visible)")

    args = parser.parse_args()

    # If any argument is provided, switch to CLI mode
    if len(sys.argv) > 1:
        # Validate required arguments for CLI
        if not args.input or not args.cert:
            parser.error("CLI mode requires --input and --cert")

        run_cli_mode(args)
    else:
        # GUI Mode
        app = App()
        app.mainloop()

if __name__ == "__main__":
    main()
