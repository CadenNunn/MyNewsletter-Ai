import os
import json
from openai import OpenAI
from dotenv import load_dotenv

# Load API key from .env
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def create_study_plan(course_name, topics, content_types, num_emails=5):
    # Generate JSON structure preview
    email_slots = ',\n    '.join([f'"Email {i+1} title"' for i in range(num_emails)])

    prompt = f"""
You are an expert study strategist creating a focused {num_emails}-part email study sequence for a high school or college student.

Course title: "{course_name}"
Topics provided: "{topics}"

Your task:
1. Create a compelling short series title (like a mini-course name)
2. Write a 2–3 sentence motivational summary of the study series
3. Choose {num_emails} specific academic subtopics (1 per email). These should be real, specific academic content like "Causes of the American Revolution" or "How Enzymes Affect Metabolism".

Return ONLY valid JSON in this format:
{{
  "plan_title": "Your mini-course title here",
  "summary": "A short motivational summary here.",
  "section_titles": [
    {email_slots}
  ]
}}

DO NOT include any extra explanation or commentary. Return only valid JSON.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )

        content = response.choices[0].message.content.strip()
        print("🔍 GPT RAW OUTPUT:\n", content)

        parsed = json.loads(content)

        plan_title = parsed.get("plan_title", "Study Plan")
        summary = parsed.get("summary", "")
        section_titles = parsed.get("section_titles", [])

        if not isinstance(section_titles, list):
            raise ValueError("section_titles is not a list")

        return plan_title, summary, section_titles

    except Exception as e:
        print("❌ Error in create_study_plan:", e)
        print("❌ Raw GPT response content:\n", locals().get("content", "No content"))
        return None, None, None
