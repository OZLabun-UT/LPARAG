import fitz  # PyMuPDF
import os
import sys

def extract_from_pdf(pdf_path, output_folder="output"):
    # Open PDF
    doc = fitz.open(pdf_path)

    # Create folders
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    base_output = os.path.join(output_folder, pdf_name)
    text_folder = os.path.join(base_output, "text")
    image_folder = os.path.join(base_output, "images")

    os.makedirs(text_folder, exist_ok=True)
    os.makedirs(image_folder, exist_ok=True)

    # Loop through pages
    for page_index in range(len(doc)):
        page = doc.load_page(page_index)

        # ---- Extract text ----
        text = page.get_text("text")
        text_file = os.path.join(text_folder, f"page_{page_index+1}.txt")
        with open(text_file, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[+] Saved text: {text_file}")

        # ---- Extract images ----
        image_list = page.get_images(full=True)
        if image_list:
            print(f"[+] Found {len(image_list)} images on page {page_index+1}")
        else:
            print(f"[!] No images found on page {page_index+1}")

        for image_index, img in enumerate(image_list, start=1):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]

            image_name = f"page{page_index+1}_img{image_index}.{image_ext}"
            image_path = os.path.join(image_folder, image_name)

            with open(image_path, "wb") as img_file:
                img_file.write(image_bytes)
            print(f"    Saved image: {image_path}")

    print(f"\n[✓] Finished extracting {pdf_path}")
    print(f"    Output in: {base_output}")


if __name__ == "__main__":
    # Default input dir = ./pdfs
    input_dir = sys.argv[1] if len(sys.argv) > 1 else "pdfs"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "output"

    if not os.path.exists(input_dir):
        print(f"[!] Input directory '{input_dir}' does not exist.")
        sys.exit(1)

    for filename in os.listdir(input_dir):
        if filename.lower().endswith(".pdf"):
            pdf_path = os.path.join(input_dir, filename)
            extract_from_pdf(pdf_path, output_dir)
