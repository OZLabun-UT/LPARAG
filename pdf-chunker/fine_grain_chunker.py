import os
import json
import shutil
from pathlib import Path
from tqdm import tqdm
from unstructured.partition.pdf import partition_pdf


def extract_from_pdf(pdf_path):
    """
    Extracts structured text, images, captions, and tables from a PDF
    using Unstructured.io's high-resolution layout model.
    Saves output in:
        pdf-chunker/output/{pdf_name}/
            ├── {pdf_name}.pdf
            ├── structured.json
            └── chunks/
                ├── text/
                └── images/
    """
    # ----- Hardcoded paths -----
    output_root = Path("pdf-chunker/output")

    # Folder setup
    pdf_name = Path(pdf_path).stem
    base_output = output_root / pdf_name
    chunks_dir = base_output / "chunks"
    text_dir = chunks_dir / "text"
    image_dir = chunks_dir / "images"

    # Create folder hierarchy
    text_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[•] Extracting from {pdf_name} ...")

    # Copy original PDF into its folder
    dest_pdf_path = base_output / f"{pdf_name}.pdf"
    if not dest_pdf_path.exists():
        shutil.copy2(pdf_path, dest_pdf_path)
        print(f"[+] Copied original PDF to {dest_pdf_path}")

    # --- Extract with Unstructured.io ---
    elements = partition_pdf(
        filename=pdf_path,
        strategy="hi_res",  # layout-aware for scientific PDFs
        extract_images_in_pdf=True,
        extract_image_block_output_dir=str(image_dir),
        include_metadata=True,
    )

    # --- Save text chunks individually ---
    records = []
    text_counter = 1
    for el in elements:
        record = el.to_dict()

        # Flatten image path if present
        if "metadata" in record and "image_path" in record["metadata"]:
            record["image_path"] = record["metadata"]["image_path"]

        # Save text chunks
        if record.get("text"):
            text_filename = f"chunk_{text_counter:03d}.txt"
            text_path = text_dir / text_filename
            with open(text_path, "w", encoding="utf-8") as tf:
                tf.write(record["text"])
            record["text_path"] = str(text_path)
            text_counter += 1

        records.append(record)

    # --- Save structured JSON ---
    out_json = base_output / "structured.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"[✓] Saved structured data: {out_json}")
    print(f"[✓] {text_counter - 1} text chunks, images in {image_dir}")


if __name__ == "__main__":
    # ----- Hardcoded input folder -----
    input_dir = Path("pdf-chunker/pdfs")
    output_dir = Path("pdf-chunker/output")

    if not input_dir.exists():
        print(f"[!] Input directory '{input_dir}' does not exist.")
        raise SystemExit(1)

    pdf_files = [f for f in input_dir.iterdir() if f.suffix.lower() == ".pdf"]
    if not pdf_files:
        print(f"[!] No PDFs found in {input_dir}")
        raise SystemExit(1)

    print(f"[•] Found {len(pdf_files)} PDFs in {input_dir}")
    for pdf_path in tqdm(pdf_files, desc="Processing PDFs"):
        extract_from_pdf(pdf_path)
