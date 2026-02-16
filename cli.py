import getpass
import sys
import os
from signer import sign_pdf

def run_cli_mode(args):
    """
    Handles the CLI execution flow.
    """
    input_pdf = args.input
    cert_path = args.cert
    password = args.password
    output_pdf = args.output

    if not os.path.exists(input_pdf):
        print(f"Error: Input PDF not found: {input_pdf}", file=sys.stderr)
        sys.exit(1)
    
    if not os.path.exists(cert_path):
        print(f"Error: Certificate file not found: {cert_path}", file=sys.stderr)
        sys.exit(1)

    if not password:
        try:
            password = getpass.getpass("Enter certificate password: ")
        except KeyboardInterrupt:
            print("\nOperation cancelled.")
            sys.exit(1)
    
    if not output_pdf:
        base, ext = os.path.splitext(input_pdf)
        output_pdf = f"{base}_signed{ext}"

    print(f"Signing {input_pdf}...")
    
    try:
        visible = getattr(args, 'visible', False)
        vertical_left = getattr(args, 'vertical_left', False)
        all_pages = getattr(args, 'all_pages', False)
        sign_pdf(input_pdf, cert_path, password, output_pdf, visible=visible, vertical_left=vertical_left, all_pages=all_pages)
        print(f"Success! Signed file saved to: {output_pdf}")
    except Exception as e:
        print(f"Error during signing: {str(e)}", file=sys.stderr)
        sys.exit(1)
