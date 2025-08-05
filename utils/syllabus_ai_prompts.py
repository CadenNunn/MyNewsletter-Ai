from openai import OpenAI
import os
import json
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def extract_syllabus_title_and_topics(raw_text):
    prompt = f"""
    You are a helpful assistant. Here is a syllabus text. Extract the following:

    1. The Course Title (keep it short and clean).
    2. A list of 10-20 distinct course topics the class will cover.
    3. A date-to-topic mapping if specific class dates are mentioned. Use the date as the key (YYYY-MM-DD format), and the topic as the value.
       - Example: "2025-08-24": "World War II"
       - Only include mappings where a date and topic are clearly associated.

    Return ONLY in this JSON format:
    {{
        "course_title": "<course title>",
        "topics": ["Topic 1", "Topic 2", "Topic 3", ...],
        "date_topic_map": {{
            "2025-08-24": "World War II",
            "2025-09-01": "Cold War"
        }}
    }}

    Syllabus Text:
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
        print("AI Extracted Result:", result)
        # Ensure date_topic_map exists in result even if empty
        if 'date_topic_map' not in result:
            result['date_topic_map'] = {}
        return result
    except Exception as e:
        print("Failed to parse AI response:", ai_response)
        return {'course_title': '', 'topics': [], 'date_topic_map': {}}
