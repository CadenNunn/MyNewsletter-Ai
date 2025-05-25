import os
from openai import OpenAI
from dotenv import load_dotenv
import json

# Load the .env file and API key
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def create_email_plan(topic, demographic, tone):
    # Step 1: Generate the 5-email plan
    prompt = f"""
You are a professional newsletter strategist.

Create a 5-part email newsletter plan for the topic: "{topic}".
Audience: {demographic}.
Tone: {tone}.

Return a JSON object with this structure:
{{
  "plan_title": "Main series title",
  "section_titles": [
    "Email 1 title",
    "Email 2 title",
    "Email 3 title",
    "Email 4 title",
    "Email 5 title"
  ]
}}
Only return the JSON. No commentary or extra explanation.
"""

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    content = response.choices[0].message.content

    # Try parsing JSON response
    try:
        parsed = json.loads(content)
        plan_title = parsed.get("plan_title", "Untitled Series")
        section_titles = parsed.get("section_titles", [])
        if not isinstance(section_titles, list) or len(section_titles) != 5:
            raise ValueError("Invalid section_titles format.")
    except Exception as e:
        print("❌ Error parsing GPT response:", e)
        print("Raw content was:\n", content)
        return None, None, None

    # Step 2: Generate a one-line teaser summary of the email plan
    summary_prompt = f"""You created this 5-part email newsletter series with the following input:

    Topic: {topic}
    Demographic: {demographic}
    Series title: {plan_title}
    Email titles:
    - {section_titles[0]}
    - {section_titles[1]}
    - {section_titles[2]}
    - {section_titles[3]}
    - {section_titles[4]}

    Write a one-sentence teaser that builds curiosity and excitement about the newsletter.
    This teaser will be shown to the user to get them excited about their first email.

    It should:
    - Be short and intriguing (1 sentence max)
    - Hint at the value of the series for a {demographic}
    - Relate directly to the topic: "{topic}"
    - Avoid summarizing content — instead, spark interest

    Return only the one-sentence teaser.
    """

    summary_response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": summary_prompt}],
        temperature=0.9
    )

    summary = summary_response.choices[0].message.content.strip()

    return plan_title, section_titles, summary
