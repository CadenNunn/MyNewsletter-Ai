from openai import OpenAI
import os
from dotenv import load_dotenv
import json

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
        "quiz": "- Create 5 challenging multiple-choice questions that test understanding of the topic. Include answers below the questions in a clear answer key format.",
        "flashcards": "- Generate a list of flashcards (term/definition or Q&A format) focusing on core facts, keywords, or ideas that are essential for recall and review.",
        "suggested reading": "- Recommend 2–3 high-quality online resources (articles or videos) that go deeper into the topic. Include 1-line summaries for each."
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
<p>Term: ...<br>Definition: ...</p>

<h2>Suggested Reading</h2>
<p><a href='URL'>Title</a> — 1 sentence summary</p>

<h2>Conclusion</h2>
<p>Brief motivational outro and recap.</p>

{past_note}

Requirements:
- Include only the content types the user selected.
- Write with a confident, encouraging, and thoughtful tone.
- Prioritize relevance, depth, and usefulness. Don’t oversimplify.
- Return clean, valid HTML using only <h1>, <h2>, and <p> tags.
- No inline styles, tables, markdown, or extra formatting.
- Return only the HTML. No notes or explanations.
"""

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    return response.choices[0].message.content.strip()