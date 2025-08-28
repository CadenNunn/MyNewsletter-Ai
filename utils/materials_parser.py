# utils/materials_parser.py
from io import BytesIO
import fitz  # PyMuPDF
from pdf2image import convert_from_bytes
import pytesseract, cv2, numpy as np
from PIL import Image
from utils.materials_ai_prompts import extract_material_title_and_topics

import shutil
import pytesseract

pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

def preprocess_image_for_ocr(pil_image):
    img = np.array(pil_image.convert('L'))
    _, img = cv2.threshold(img, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(img > 0))
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    (h, w) = img.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(img)

def extract_topics_from_material(file_content, filename):
    text = ""
    fname = (filename or "").lower()

    if fname.endswith('.pdf'):
        doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
        try:
            for page_number, page in enumerate(doc, start=1):
                page_text = page.get_text().strip()
                if page_text:
                    print(f"Page {page_number}: Extracted from text layer.")
                    text += page_text + "\n"
                else:
                    print(f"Page {page_number}: No text layer found. Running OCR...")
                    images = convert_from_bytes(file_content, first_page=page_number, last_page=page_number)
                    for img in images:
                        processed_img = preprocess_image_for_ocr(img)
                        text += pytesseract.image_to_string(processed_img) + "\n"
        finally:
            doc.close()

    elif fname.endswith('.docx'):
        from docx import Document
        doc = Document(BytesIO(file_content))
        text = "\n".join(p.text for p in doc.paragraphs)

    else:
        print("Unsupported file type:", filename)
        # No doc_type here — we only return course_title + topics
        return {"course_title": "", "topics": ["Unsupported file type"]}

    print("Extracted Raw Text (First 1000 chars):", text[:1000])
    return extract_material_title_and_topics(text)
