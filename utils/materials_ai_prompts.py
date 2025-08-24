from openai import OpenAI
import os
import json
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def extract_material_title_and_topics(raw_text: str):
    prompt = f"""
You are a helpful assistant. Here is a class material (quiz, test, notes, homework, slides, etc.). Extract the following:

1. The Course Title (short, clean; guess from context if needed).
2. A list of distinct, academically rigorous course topics covered or implied by the material.
   - Avoid vague items like "Introduction" or "Overview."
   - Merge duplicates (case-insensitive).
   - Keep topics concise but specific enough to stand alone as a review subject.
   - Prefer 8–15 high-quality topics, but do not pad with weak items.

Return ONLY in this JSON format:
{{
  "course_title": "<course title>",
  "topics": ["Topic 1", "Topic 2", "Topic 3", ...]
}}

Material Text:
{raw_text[:4000]}
"""


    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    ai_response = response.choices[0].message.content
    ai_response = ai_response.strip('```json').strip('```').strip()

    try:
        result = json.loads(ai_response)
        # normalize/guard
        if 'course_title' not in result or not isinstance(result['course_title'], str):
            result['course_title'] = ''
        if 'topics' not in result or not isinstance(result['topics'], list):
            result['topics'] = []
        return result
    except Exception:
        print("Failed to parse AI response (materials):", ai_response)
        return {'course_title': '', 'topics': []}
