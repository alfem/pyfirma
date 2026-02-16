import customtkinter
import os
from tkinter import filedialog, messagebox
import threading
from signer import sign_pdf

customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

class App(customtkinter.CTk):
    def __init__(self):
        super().__init__()

        self.title("PyFirma")
        self.geometry("600x450")
        
        self.input_file = None
        self.cert_file = None

        self.setup_ui()

    def setup_ui(self):
        # Header
        self.header_label = customtkinter.CTkLabel(self, text="PyFirma - PDF Signer", font=customtkinter.CTkFont(size=20, weight="bold"))
        self.header_label.pack(pady=20)

        # Main Container
        self.frame = customtkinter.CTkFrame(self)
        self.frame.pack(pady=20, padx=20, fill="both", expand=True)

        # File Section
        self.file_label = customtkinter.CTkLabel(self.frame, text="Document:")
        self.file_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        
        self.file_path_label = customtkinter.CTkLabel(self.frame, text="No file selected", text_color="gray", wraplength=300)
        self.file_path_label.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        
        self.file_button = customtkinter.CTkButton(self.frame, text="Select PDF", command=self.select_file)
        self.file_button.grid(row=0, column=2, padx=10, pady=10)

        # Certificate Section
        self.cert_label = customtkinter.CTkLabel(self.frame, text="Certificate (.p12):")
        self.cert_label.grid(row=1, column=0, padx=10, pady=10, sticky="w")
        
        self.cert_path_label = customtkinter.CTkLabel(self.frame, text="No certificate selected", text_color="gray", wraplength=300)
        self.cert_path_label.grid(row=1, column=1, padx=10, pady=10, sticky="w")
        
        self.cert_button = customtkinter.CTkButton(self.frame, text="Select Cert", command=self.select_cert)
        self.cert_button.grid(row=1, column=2, padx=10, pady=10)

        # Password Section
        self.pass_label = customtkinter.CTkLabel(self.frame, text="Password:")
        self.pass_label.grid(row=2, column=0, padx=10, pady=10, sticky="w")
        
        self.pass_entry = customtkinter.CTkEntry(self.frame, show="*", width=200)
        self.pass_entry.grid(row=2, column=1, padx=10, pady=10, sticky="w")

        # Visible Signature Checkbox
        self.visible_var = customtkinter.BooleanVar(value=False)
        self.visible_checkbox = customtkinter.CTkCheckBox(self.frame, text="Add Visible Signature", variable=self.visible_var, command=self.toggle_visible_options)
        self.visible_checkbox.grid(row=3, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        
        # Vertical Left Checkbox (Initially Disabled/Hidden)
        self.vertical_left_var = customtkinter.BooleanVar(value=False)
        self.vertical_left_checkbox = customtkinter.CTkCheckBox(self.frame, text="Vertical Left Margin", variable=self.vertical_left_var)
        self.vertical_left_checkbox.grid(row=3, column=2, padx=10, pady=10, sticky="w")
        self.vertical_left_checkbox.configure(state="disabled")
        
        # All Pages Checkbox (Initially Disabled)
        self.all_pages_var = customtkinter.BooleanVar(value=False)
        self.all_pages_checkbox = customtkinter.CTkCheckBox(self.frame, text="Sign All Pages", variable=self.all_pages_var)
        self.all_pages_checkbox.grid(row=4, column=0, columnspan=2, padx=10, pady=10, sticky="w")
        self.all_pages_checkbox.configure(state="disabled")

        # Action Button
        self.sign_button = customtkinter.CTkButton(self, text="Sign Document", command=self.start_signing, height=40, font=customtkinter.CTkFont(size=16, weight="bold"), state="disabled")
        self.sign_button.pack(pady=20)
        
        # Status
        self.status_label = customtkinter.CTkLabel(self, text="Ready", text_color="gray")
        self.status_label.pack(side="bottom", pady=10)

    def toggle_visible_options(self):
        if self.visible_var.get():
            self.vertical_left_checkbox.configure(state="normal")
            self.all_pages_checkbox.configure(state="normal")
        else:
            self.vertical_left_checkbox.configure(state="disabled")
            self.vertical_left_var.set(False)
            self.all_pages_checkbox.configure(state="disabled")
            self.all_pages_var.set(False)

    def select_file(self):
        filename = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if filename:
            self.input_file = filename
            self.file_path_label.configure(text=os.path.basename(filename), text_color="white")
            self.check_ready()

    def select_cert(self):
        filename = filedialog.askopenfilename(filetypes=[("Certificate Files", "*.p12 *.pfx")])
        if filename:
            self.cert_file = filename
            self.cert_path_label.configure(text=os.path.basename(filename), text_color="white")
            self.check_ready()

    def check_ready(self):
        if self.input_file and self.cert_file:
            self.sign_button.configure(state="normal")

    def start_signing(self):
        password = self.pass_entry.get()
        if not password:
            messagebox.showerror("Error", "Please enter the certificate password.")
            return

        self.sign_button.configure(state="disabled")
        self.status_label.configure(text="Signing...", text_color="orange")
        self.update()

        visible = self.visible_var.get()
        vertical_left = self.vertical_left_var.get()
        all_pages = self.all_pages_var.get()

        # Run in thread to not freeze UI
        threading.Thread(target=self.perform_signing, args=(password, visible, vertical_left, all_pages), daemon=True).start()

    def perform_signing(self, password, visible, vertical_left, all_pages):
        try:
            base, ext = os.path.splitext(self.input_file)
            output_file = f"{base}_signed{ext}"
            
            sign_pdf(self.input_file, self.cert_file, password, output_file, visible=visible, vertical_left=vertical_left, all_pages=all_pages)
            
            self.after(0, lambda: self.signing_success(output_file))
        except Exception as e:
            error_msg = str(e)
            self.after(0, lambda: self.signing_error(error_msg))

    def signing_success(self, output_file):
        messagebox.showinfo("Success", f"File signed successfully!\nSaved as: {os.path.basename(output_file)}")
        self.status_label.configure(text="Finished successfully", text_color="green")
        self.sign_button.configure(state="normal")
        # Clear password for security? detailed implementation choice. 
        # self.pass_entry.delete(0, 'end') 

    def signing_error(self, error_msg):
        messagebox.showerror("Signing Error", error_msg)
        self.status_label.configure(text="Error occurred", text_color="red")
        self.sign_button.configure(state="normal")

if __name__ == "__main__":
    app = App()
    app.mainloop()
