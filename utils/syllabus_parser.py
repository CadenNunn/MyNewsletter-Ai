import re
from io import BytesIO
import fitz  # PyMuPDF
from pdf2image import convert_from_bytes
import pytesseract
import cv2
import numpy as np
from PIL import Image
from utils.syllabus_ai_prompts import extract_syllabus_title_and_topics

import shutil
import pytesseract

_tess = shutil.which("tesseract")
if _tess:
    pytesseract.pytesseract.tesseract_cmd = _tess
else:
    # Common path on Debian/Ubuntu base images used by Render
    pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"



def preprocess_image_for_ocr(pil_image):
    # Convert to grayscale
    img = np.array(pil_image.convert('L'))

    # Thresholding to enhance contrast
    _, img = cv2.threshold(img, 150, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Deskewing
    coords = np.column_stack(np.where(img > 0))
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    # Return preprocessed image as PIL Image
    return Image.fromarray(img)


def extract_topics_from_syllabus(file_content, filename):
    text = ""

    if filename.endswith('.pdf'):
        doc = fitz.open(stream=BytesIO(file_content), filetype="pdf")
        for page_number, page in enumerate(doc, start=1):
            page_text = page.get_text().strip()

            if page_text:
                print(f"Page {page_number}: Extracted from text layer.")
                text += page_text
            else:
                print(f"Page {page_number}: No text layer found. Running OCR...")
                images = convert_from_bytes(file_content, first_page=page_number, last_page=page_number)
                for img in images:
                    processed_img = preprocess_image_for_ocr(img)
                    ocr_text = pytesseract.image_to_string(processed_img)
                    text += ocr_text

    elif filename.endswith('.docx'):
        from docx import Document
        doc = Document(BytesIO(file_content))
        text = "\n".join([para.text for para in doc.paragraphs])

    else:
        print("Unsupported file type:", filename)
        return {"course_title": "", "topics": ["Unsupported file type"]}

    print("Extracted Raw Text (First 1000 chars):", text[:1000])

    # Call AI to extract course title and topics
    extraction_result = extract_syllabus_title_and_topics(text)
    return extraction_result
