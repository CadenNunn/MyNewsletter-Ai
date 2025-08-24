from openai import OpenAI
import os
from dotenv import load_dotenv
import json

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def generate_subject_line(topic, course_name):
    """Generate a short, engaging subject line for the given topic."""
    prompt = f"""
Generate a short, engaging email subject line for a study email in the course "{course_name}".
The email focuses on the topic: "{topic}".
It should be concise, clear, and intriguing for a college-level student.
Use curiosity, challenge, or action phrasing (e.g., "Mastering...", "Can you solve...", "Crack the code on...").
Return only the subject line text with no quotes, punctuation at the end, or extra words.
"""
    response = client.chat.completions.create(
        model="gpt-4",  # OK; you can switch to "gpt-4o-mini" for cheaper subject lines
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6
    )
    return response.choices[0].message.content.strip()


def write_study_email(
    course_name,
    topics,
    section_title,
    content_types,
    plan_title,
    position_in_plan,
    past_content=None
):
    past_note = f"""
Avoid repeating any ideas, questions, or examples already written for this course.

=== BEGIN PAST CONTENT ===
{past_content}
=== END PAST CONTENT ===
""" if past_content else ""

    section_descriptions = {
        "summary": "- Write a focused summary of the key idea, assuming the student has already encountered the material. Use clear, intelligent phrasing that reinforces understanding rather than teaching from scratch.",
        "example": "- Provide a detailed, creative real-life analogy or scenario that helps clarify the concept. Avoid academic jargon—this should feel vivid, relatable, and memorable.",
        "quiz": "- Create 5 challenging multiple-choice questions. At least 2 should involve application or reasoning (not pure recall). Include an Answer Key with brief rationales.",
        "flashcards": "- Generate 5 flashcards in 'Front — Back' format focusing on core facts, keywords, or ideas essential for recall. Do not repeat quiz items; make them complementary.",
        "suggested reading": "- Recommend 2–3 realistic resources (e.g., textbook chapter, 'Khan Academy article on ...', 'CrashCourse video on ...'). Do not fabricate URLs; provide titles/descriptions only."
    }

    requested_blocks = "\n".join([
        section_descriptions[ct.lower()]
        for ct in content_types if ct.lower() in section_descriptions
    ])

    prompt = f"""
You are an expert study strategist creating a study support email for a student enrolled in a course titled "{plan_title}".

This is email {position_in_plan} of their personalized learning series.
Today's focus is: "{section_title}"
The course covers: "{topics}"

Your job is to reinforce—not introduce—the material. Assume the student has already seen the content in class and is now reviewing or deepening their understanding.

Start with a brief (2–3 sentence) motivational introduction that previews the day's topic in an engaging way.

Then, based on the student's selected content preferences, include ONLY the following sections:
{requested_blocks}

Finish with a short (2–3 sentence) conclusion that reinforces the topic and motivates the student to continue studying.

Use this structure:
<h1>{section_title}</h1>
<p>Short motivational intro.</p>

<h2>Summary</h2>
<p>...</p>

<h2>Example</h2>
<p>...</p>

<h2>Quiz</h2>
<p>Q1...</p>
...
<p><strong>Answers:</strong></p>
<p>A1...</p>

<h2>Flashcards</h2>
<p>Front — Back</p>

<h2>Suggested Reading</h2>
<p>Resource Title — 1 sentence summary</p>

<h2>Conclusion</h2>
<p>Brief motivational outro and recap.</p>

{past_note}

Requirements:
- Include only the content types the user selected; DO NOT include sections the user did not select.
- Write with a confident, encouraging, and thoughtful tone.
- Prioritize relevance, depth, and usefulness. Don’t oversimplify.
- Return clean, valid HTML using only <h1>, <h2>, and <p> tags.
- No inline styles, tables, markdown, or extra formatting.
- Return only the HTML. No notes or explanations.
"""

    response = client.chat.completions.create(
        model="gpt-4",  # For higher quality you can use "gpt-4o"; keep as-is if you prefer
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    # --- Light QA: strip rogue code fences/overruns and cap length ---
    raw_html = response.choices[0].message.content.strip()
    if raw_html.startswith("```"):
        raw_html = raw_html.strip("`").strip()
    if "```" in raw_html:
        raw_html = raw_html.split("```")[0]
    if len(raw_html) > 15000:
        raw_html = raw_html[:15000] + "\n<p>[Content trimmed]</p>"

    return raw_html
