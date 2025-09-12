import os
import json
import pdfplumber
import camelot
from pdf2image import convert_from_path
from pathlib import Path

def extract_pdf(pdf_path, out_dir):
    pdf_name = Path(pdf_path).stem
    pdf_out = Path(out_dir) / pdf_name
    pdf_out.mkdir(parents=True, exist_ok=True)

    # ---- Extract text ----
    text_content = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text:
                text_content.append({"page": i, "text": text})

    with open(pdf_out / "text.json", "w", encoding="utf-8") as f:
        json.dump(text_content, f, indent=2, ensure_ascii=False)

    # ---- Extract tables ----
    try:
        tables = camelot.read_pdf(pdf_path, pages="all")
        table_content = []
        for t in tables:
            table_content.append({"page": t.page, "table": t.df.to_dict()})

        with open(pdf_out / "tables.json", "w", encoding="utf-8") as f:
            json.dump(table_content, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] Table extraction failed for {pdf_name}: {e}")

    # ---- Extract images ----
    img_dir = pdf_out / "images"
    img_dir.mkdir(exist_ok=True)

    try:
        images = convert_from_path(pdf_path)
        for i, img in enumerate(images, start=1):
            img_path = img_dir / f"page_{i}.png"
            img.save(img_path, "PNG")
    except Exception as e:
        print(f"[WARN] Image extraction failed for {pdf_name}: {e}")

    print(f"[INFO] Finished extracting {pdf_name} → {pdf_out}")


def process_pdfs(input_dir, output_dir="pdf_output"):
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    pdfs = list(input_dir.glob("*.pdf"))
    if not pdfs:
        print(f"[ERROR] No PDFs found in {input_dir}")
        return

    for pdf in pdfs:
        extract_pdf(pdf, output_dir)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extract text, tables, and images from PDFs.")
    parser.add_argument("input_dir", help="Folder containing PDFs")
    parser.add_argument("--output_dir", default="pdf_output", help="Folder to save extracted content")
    args = parser.parse_args()

    process_pdfs(args.input_dir, args.output_dir)
