# utils/materials_parser.py
from io import BytesIO
import os
import shutil

import fitz  # PyMuPDF
from pdf2image import convert_from_bytes
import pytesseract
import cv2
import numpy as np
from PIL import Image

from utils.materials_ai_prompts import extract_material_title_and_topics


# Lazy + safe OCR init: never crash import if Tesseract is missing
_OCR_AVAILABLE = False

def _try_init_tesseract() -> bool:
    """
    Try to locate Tesseract and configure pytesseract.
    Returns True if available, False otherwise.
    """
    global _OCR_AVAILABLE
    if _OCR_AVAILABLE:
        return True

    env_path = os.environ.get("TESSERACT_CMD")
    if env_path and os.path.exists(env_path):
        pytesseract.pytesseract.tesseract_cmd = env_path
        _OCR_AVAILABLE = True
        return True

    which_path = shutil.which("tesseract")
    if which_path:
        pytesseract.pytesseract.tesseract_cmd = which_path
        _OCR_AVAILABLE = True
        return True

    candidates = [
        "/usr/bin/tesseract",           # Debian/Ubuntu default (Render)
        "/opt/homebrew/bin/tesseract",  # Apple Silicon Homebrew
        "/usr/local/bin/tesseract",     # Intel Homebrew
    ]
    for p in candidates:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            _OCR_AVAILABLE = True
            return True

    return False

def ocr_available() -> bool:
    """Public check used by call sites to gate OCR work."""
    return _try_init_tesseract()



def preprocess_image_for_ocr(pil_image: Image.Image) -> Image.Image:
    """Lightweight preprocessing: grayscale, OTSU binarize, small deskew."""
    img = np.array(pil_image.convert('L'))
    _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    coords = np.column_stack(np.where(img > 0))
    if coords.size > 0:
        angle = cv2.minAreaRect(coords)[-1]
        angle = -(90 + angle) if angle < -45 else -angle
        (h, w) = img.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    return Image.fromarray(img)


def extract_topics_from_material(file_content: bytes, filename: str):
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
                    # Text layer present — better fidelity and faster
                    text += page_text + "\n"
                    continue

                # No text layer: try OCR only if available
                if ocr_available():
                    images = convert_from_bytes(
                        file_content,
                        first_page=page_number,
                        last_page=page_number
                    )
                    for img in images:
                        processed_img = preprocess_image_for_ocr(img)
                        text += pytesseract.image_to_string(processed_img) + "\n"
                else:
                    # OCR not available on this box; leave empty for this page
                    text += ""

        finally:
            doc.close()

    elif fname.endswith('.docx'):
        from docx import Document
        doc = Document(BytesIO(file_content))
        text = "\n".join(p.text for p in doc.paragraphs)

    else:
        # Unsupported types return a clear message (keeps downstream stable)
        return {"course_title": "", "topics": ["Unsupported file type"]}

    # Optional debug print (truncate to keep logs readable)
    # print("Extracted Raw Text (First 1000 chars):", text[:1000])

    return extract_material_title_and_topics(text)
