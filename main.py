import sys
import argparse
from gui import App
from cli import run_cli_mode

def main():
    parser = argparse.ArgumentParser(description="PyFirma - Python PDF Signer")
    parser.add_argument("-i", "--input", help="Path to input PDF file")
    parser.add_argument("-c", "--cert", help="Path to .p12/.pfx certificate file")
    parser.add_argument("-p", "--password", help="Certificate password")
    parser.add_argument("-o", "--output", help="Path to output signed PDF file")
    parser.add_argument("--visible", action="store_true", help="Add visible signature stamp")
    parser.add_argument("--vertical-left", action="store_true", help="Place visible signature vertically on the left margin")

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
