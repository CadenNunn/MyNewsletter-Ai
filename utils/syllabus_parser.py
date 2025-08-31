import os
import re
import shutil
from io import BytesIO

import fitz  # PyMuPDF
from pdf2image import convert_from_bytes
import pytesseract
import cv2
import numpy as np
from PIL import Image

from utils.syllabus_ai_prompts import extract_syllabus_title_and_topics


def _resolve_tesseract_cmd() -> str:
    """
    Resolve the Tesseract binary path deterministically.
      1) TESSERACT_CMD env var (explicit override)
      2) shutil.which('tesseract') (PATH)
      3) Common Homebrew/Intel fallbacks
    """
    env_path = os.environ.get("TESSERACT_CMD")
    if env_path and os.path.exists(env_path):
        return env_path

    which_path = shutil.which("tesseract")
    if which_path:
        return which_path

    candidates = [
        "/opt/homebrew/bin/tesseract",  # Apple Silicon Homebrew (most likely for you)
        "/usr/local/bin/tesseract",     # Intel Homebrew
    ]
    for p in candidates:
        if os.path.exists(p):
            return p

    raise RuntimeError(
        "Tesseract binary not found. Install via Homebrew (`brew install tesseract`) "
        "or set TESSERACT_CMD to the tesseract binary path."
    )


# Set the command once at import time so pytesseract uses the right binary
pytesseract.pytesseract.tesseract_cmd = _resolve_tesseract_cmd()


def preprocess_image_for_ocr(pil_image: Image.Image) -> Image.Image:
    """
    Grayscale → OTSU binarize → light deskew.
    Returns a PIL Image ready for OCR.
    """
    img = np.array(pil_image.convert('L'))
    # OTSU: let it pick the threshold automatically
    _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Deskew (guard against empty coords)
    coords = np.column_stack(np.where(img > 0))
    if coords.size:
        angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + angle) if angle < -45 else -angle
        (h, w) = img.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        img = cv2.warpAffine(
            img, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE
        )

    return Image.fromarray(img)


def extract_topics_from_syllabus(file_content: bytes, filename: str):
    """
    Extract raw text from PDF (prefer text layer, fallback to OCR per-page),
    or from DOCX; then delegate to AI prompt to parse title/topics.
    """
    text = ""
    fname = (filename or "").lower()

    if fname.endswith('.pdf'):
        doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
        try:
            for page_number, page in enumerate(doc, start=1):
                page_text = (page.get_text() or "").strip()

                if page_text:
                    # Text layer present — better fidelity and faster than OCR
                    text += page_text + "\n"
                else:
                    # No text layer — rasterize page and OCR
                    images = convert_from_bytes(
                        file_content,
                        first_page=page_number,
                        last_page=page_number
                    )
                    for img in images:
                        processed_img = preprocess_image_for_ocr(img)
                        ocr_text = pytesseract.image_to_string(processed_img)
                        text += (ocr_text or "") + "\n"
        finally:
            doc.close()

    elif fname.endswith('.docx'):
        from docx import Document
        doc = Document(BytesIO(file_content))
        text = "\n".join(p.text for p in doc.paragraphs)

    else:
        # Unsupported types return a clear message (keeps downstream stable)
        return {"course_title": "", "topics": ["Unsupported file type"]}

    # Optional debug
    # print("Extracted Raw Text (First 1000 chars):", text[:1000])

    # Call AI to extract course title and topics
    return extract_syllabus_title_and_topics(text)
