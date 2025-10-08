# docling_chunker.py
import os
import sys
import shutil
from pathlib import Path
from tqdm import tqdm

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling_core.types.doc import ImageRefMode

from docling.chunking import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from transformers import AutoTokenizer


def extract_with_docling(pdf_path: Path, output_root: Path = Path("output")):
    name = pdf_path.stem
    base = output_root / name
    images_dir = base / "images"
    chunks_dir = base / "chunks" / "text"

    # --- folders ---
    chunks_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    # --- copy original PDF ---
    dst_pdf = base / f"{name}.pdf"
    if not dst_pdf.exists():
        shutil.copy2(pdf_path, dst_pdf)

    # --- Docling options ---
    pipe_opts = PdfPipelineOptions()
    pipe_opts.generate_picture_images = True
    pipe_opts.images_scale = 2.0

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipe_opts)}
    )

    # --- convert ---
    conv = converter.convert(str(pdf_path))
    dl_doc = conv.document

    # --- save structured JSON + referenced images ---
    json_path = base / "structured.json"
    dl_doc.save_as_json(
        filename=json_path,
        artifacts_dir=images_dir,               # ✅ pass Path, not str
        image_mode=ImageRefMode.REFERENCED,
    )


    # --- chunk for RAG ---
    hf_tok = AutoTokenizer.from_pretrained("sentence-transformers/all-MiniLM-L6-v2")
    tokenizer = HuggingFaceTokenizer(tokenizer=hf_tok, max_tokens=512)
    chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

    for i, chunk in enumerate(chunker.chunk(dl_doc), start=1):
        enriched = chunker.contextualize(chunk)
        (chunks_dir / f"chunk_{i:03d}.txt").write_text(enriched, encoding="utf-8")

    print(f"[✓] {name}: JSON->{json_path}  images->{images_dir}  chunks->{chunks_dir}")
def extract_text_for_session(pdf_path: Path) -> str:
    """
    Runs Docling extraction and returns a combined text string of all chunked content.
    Does NOT write anything to S3 or DB—just returns text for in-session use.
    """
    from io import StringIO
    output_dir = Path("/tmp/docling_tmp_output")
    extract_with_docling(pdf_path, output_dir)
    chunks_dir = output_dir / pdf_path.stem / "chunks" / "text"
    buffer = StringIO()
    if chunks_dir.exists():
        for chunk_file in sorted(chunks_dir.glob("*.txt")):
            buffer.write(chunk_file.read_text(encoding="utf-8") + "\n\n")
    return buffer.getvalue()


if __name__ == "__main__":
    input_dir = Path("pdfs")
    output_dir = Path("output")

    if not input_dir.exists():
        sys.exit(f"[!] Input dir '{input_dir}' missing")

    pdfs = [p for p in input_dir.iterdir() if p.suffix.lower() == ".pdf"]
    if not pdfs:
        sys.exit(f"[!] No PDFs found in {input_dir}")

    print(f"[•] Found {len(pdfs)} PDFs")
    for pdf in tqdm(pdfs, desc="Processing PDFs"):
        extract_with_docling(pdf, output_dir)
