from PIL import Image, ImageDraw

def create_pdf(filename):
    img = Image.new('RGB', (595, 842), color = 'white') # A4 size approx
    d = ImageDraw.Draw(img)
    d.text((50, 50), "This is a test PDF for signing.", fill=(0,0,0))
    img.save(filename, "PDF")

if __name__ == "__main__":
    create_pdf("dummy.pdf")
