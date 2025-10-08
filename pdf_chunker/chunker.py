import fitz  # PyMuPDF
import os
import sys
from bs4 import BeautifulSoup
import cv2
import numpy as np
from pathlib import Path


def extract_from_pdf(pdf_path, output_folder="output"):
    # Open PDF
    doc = fitz.open(pdf_path)

    # Create folders
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    base_output = os.path.join(output_folder, pdf_name)
    text_folder = os.path.join(base_output, "text")
    image_folder = os.path.join(base_output, "images")
    attachments_folder = os.path.join(base_output, "attachments")
    vector_folder = os.path.join(base_output, "vectors")
    figure_folder = os.path.join(base_output, "figures")

    os.makedirs(text_folder, exist_ok=True)
    os.makedirs(image_folder, exist_ok=True)
    os.makedirs(attachments_folder, exist_ok=True)
    os.makedirs(vector_folder, exist_ok=True)
    os.makedirs(figure_folder, exist_ok=True)

    # ---- Loop through pages for text + images + vector graphics ----
    for page_index in range(len(doc)):
        page = doc.load_page(page_index)

        # ---- Extract text ----
        text = page.get_text("text")
        text_file = os.path.join(text_folder, f"page_{page_index+1}.txt")
        with open(text_file, "w", encoding="utf-8") as f:
            f.write(text)

        # ---- Extract images ----
        for image_index, img in enumerate(page.get_images(full=True), start=1):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]

            image_name = f"page{page_index+1}_img{image_index}.{image_ext}"
            image_path = os.path.join(image_folder, image_name)
            with open(image_path, "wb") as img_file:
                img_file.write(image_bytes)

        # ---- Export vector graphics (plots, diagrams) as SVG (without text) ----
        svg_raw = page.get_svg_image()
        soup = BeautifulSoup(svg_raw, "xml")

        # Remove all text elements
        for t in soup.find_all("text"):
            t.decompose()

        svg_path = os.path.join(vector_folder, f"page_{page_index+1}.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(str(soup))
        print(f"[+] Saved vector graphics (plots only) as {svg_path}")

        # ---- Detect figure regions via bounding boxes ----
        # ---- Detect figure regions via bounding boxes ----
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3))  # render at ~216 DPI
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)

        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        # Threshold: invert so content is white on black
        _, thresh = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)

        # Morphological dilation to merge nearby elements (keeps axes with plots)
        kernel = np.ones((5, 5), np.uint8)   # size can be tuned (3x3 or 7x7)
        dilated = cv2.dilate(thresh, kernel, iterations=2)

        # Find contours of merged content regions
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        fig_index = 1
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)

            # Skip very small noise boxes
            if w < 80 or h < 80:
                continue

            crop = img[y:y+h, x:x+w]
            fig_path = os.path.join(figure_folder, f"page{page_index+1}_fig{fig_index}.png")
            cv2.imwrite(fig_path, crop)
            print(f"[+] Saved figure crop: {fig_path}")
            fig_index += 1


    # ---- Extract embedded files (PDFs, Excel, etc.) ----
    for name in doc.embfile_names():
        info = doc.embfile_info(name)
        content = doc.embfile_get(name)

        safe_name = os.path.basename(info.get("filename", name))
        attachment_path = os.path.join(attachments_folder, safe_name)

        with open(attachment_path, "wb") as f:
            f.write(content)
        print(f"[+] Extracted embedded file: {attachment_path}")

    print(f"[✓] Finished extracting {pdf_path}")
    print(f"    Output in: {base_output}")


def extract_embedded_files(doc, attachments_folder):
    """Extract embedded files (PDFs, Excel, etc.) from a PyMuPDF Document."""
    for name in doc.embfile_names():
        info = doc.embfile_info(name)
        content = doc.embfile_get(name)

        safe_name = os.path.basename(name)  # ensure no directory injection
        attachment_path = os.path.join(attachments_folder, safe_name)

        with open(attachment_path, "wb") as f:
            f.write(content)
        print(f"[+] Extracted embedded file: {attachment_path}")


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
