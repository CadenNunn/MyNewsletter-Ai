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
        model="gpt-4",
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
    """
    Refined deep-dive writer:
      - Prevents repetition across sections: each section must cover a unique facet of the subtopic.
      - Renames 'Quick Win' → context-driven title ('Practice Set' if quiz, 'Flash Drill' if flashcards, else 'Case Check').
      - Enforces 'upper-division contract': no elementary filler; focus on mechanisms, regulators, exceptions, and exam traps.
    """

    # --- Past-content guardrail ---
    past_note = f"""
Avoid repeating any ideas, questions, or examples already written for this course.

=== BEGIN PAST CONTENT ===
{past_content}
=== END PAST CONTENT ===
""" if past_content else ""

    # --- Section descriptions tuned for variety ---
    section_descriptions = {
        "summary": (
            "- Write a deep explanation of the subtopic’s **mechanism and logic** (e.g., steps, regulators, driving forces). "
            "Do not just define; unpack *how and why* it works. Include at least one non-obvious insight or exception."
        ),
        "example": (
            "- Give a **realistic applied scenario** where the subtopic plays out (e.g., mutation, experimental condition, clinical case). "
            "Explain the reasoning chain to the outcome. Must feel different from summary — no rephrasing."
        ),
        "quiz": (
            "- Write 5 challenging multiple-choice questions that test **application and reasoning** (not recall). "
            "Each question must declare the correct option in a data attribute as defined below. Do NOT add a separate 'Answers' paragraph."
        ),
        "flashcards": (
            "- Write 6–8 flashcards targeting **different dimensions** of the subtopic: one on mechanism, one on regulators, "
            "one on experimental detection, one on pathological consequence, etc. Avoid duplicating quiz or summary."
        ),
        "suggested reading": (
            "- Suggest 2–3 advanced, realistic study resources (textbook sections, lecture names, or well-known video series). "
            "Each resource must complement gaps left by the sections above."
        ),
    }

    requested_blocks = "\n".join([
        section_descriptions[ct.lower()]
        for ct in content_types if ct.lower() in section_descriptions
    ])



    # --- Teaser for next narrow slice ---
    next_teaser = (
        "Next email zooms in on another advanced facet — regulators, edge-cases, or exam traps you must master."
    )

    # --- STRUCTURE HTML skeleton based on selected sections ---
    selected = [ct.lower() for ct in content_types]

    sections_html = []

    if "summary" in selected:
        sections_html.append(
            "<h2>Summary</h2>\n"
            "<ul>\n"
            "  <li><strong>Mechanism:</strong> ...</li>\n"
            "  <li><strong>Regulation:</strong> ...</li>\n"
            "  <li><strong>Exception/Pitfall:</strong> ...</li>\n"
            "</ul>"
        )

    if "example" in selected:
        sections_html.append(
            "<h2>Example</h2>\n"
            "<p><strong>Scenario:</strong> ...</p>\n"
            "<p><strong>Task:</strong> ...</p>\n"
            "<p><strong>Reasoning:</strong> ...</p>\n"
            "<p><strong>Conclusion:</strong> ...</p>"
        )

    if "quiz" in selected:
        sections_html.append(
            "<h2>Quiz</h2>\n"
            "<ol class='quiz'>\n"
            "  <li class='question' data-answer='A'>\n"
            "    <div class='prompt'>Q1 …</div>\n"
            "    <ul class='choices'>\n"
            "      <li>A) …</li>\n"
            "      <li>B) …</li>\n"
            "      <li>C) …</li>\n"
            "      <li>D) …</li>\n"
            "    </ul>\n"
            "  </li>\n"
            "  <li class='question' data-answer='B'>\n"
            "    <div class='prompt'>Q2 …</div>\n"
            "    <ul class='choices'>\n"
            "      <li>A) …</li>\n"
            "      <li>B) …</li>\n"
            "      <li>C) …</li>\n"
            "      <li>D) …</li>\n"
            "    </ul>\n"
            "  </li>\n"
            "  <li class='question' data-answer='C'>\n"
            "    <div class='prompt'>Q3 …</div>\n"
            "    <ul class='choices'>\n"
            "      <li>A) …</li>\n"
            "      <li>B) …</li>\n"
            "      <li>C) …</li>\n"
            "      <li>D) …</li>\n"
            "    </ul>\n"
            "  </li>\n"
            "  <li class='question' data-answer='D'>\n"
            "    <div class='prompt'>Q4 …</div>\n"
            "    <ul class='choices'>\n"
            "      <li>A) …</li>\n"
            "      <li>B) …</li>\n"
            "      <li>C) …</li>\n"
            "      <li>D) …</li>\n"
            "    </ul>\n"
            "  </li>\n"
            "  <li class='question' data-answer='A'>\n"
            "    <div class='prompt'>Q5 …</div>\n"
            "    <ul class='choices'>\n"
            "      <li>A) …</li>\n"
            "      <li>B) …</li>\n"
            "      <li>C) …</li>\n"
            "      <li>D) …</li>\n"
            "    </ul>\n"
            "  </li>\n"
            "</ol>"
        )

    if "flashcards" in selected:
        sections_html.append(
            "<h2>Flashcards</h2>\n"
            "<ul>\n"
            "  <li>Term 1 — Definition 1</li>\n"
            "  <li>Term 2 — Definition 2</li>\n"
            "  <li>Term 3 — Definition 3</li>\n"
            "  <li>Term 4 — Definition 4</li>\n"
            "  <li>Term 5 — Definition 5</li>\n"
            "  <li>Term 6 — Definition 6</li>\n"
            "</ul>\n"
            "<!-- STRICT FORMAT: exactly one em dash (—) per <li>, no nested tags -->"
        )

    if "suggested reading" in selected:
        sections_html.append(
            "<h2>Suggested Reading</h2>\n"
            "<ul>\n"
            "  <li>Title/Section — why it helps</li>\n"
            "  <li>Title/Section — why it helps</li>\n"
            "  <li>Title/Section — why it helps</li>\n"
            "</ul>"
        )

    if sections_html:
        structure_html = "\n\n".join(sections_html)
    else:
        # Fallback if nothing selected
        structure_html = (
            "<h2>Summary</h2>\n"
            "<ul>\n"
            "  <li><strong>Mechanism:</strong> ...</li>\n"
            "  <li><strong>Regulation:</strong> ...</li>\n"
            "  <li><strong>Exception/Pitfall:</strong> ...</li>\n"
            "</ul>\n\n"
            "<h2>Flashcards</h2>\n"
            "<ul>\n"
            "  <li>Front — Back</li>\n"
            "  <li>Front — Back</li>\n"
            "  <li>Front — Back</li>\n"
            "  <li>Front — Back</li>\n"
            "  <li>Front — Back</li>\n"
            "</ul>"
        )

    prompt = f"""
You are an expert study strategist creating a study email for a course titled "{plan_title}".
This is email {position_in_plan}. Today's declared focus is: "{section_title}"
The broader course/topics context is: "{topics}"

Task: produce exam-grade reinforcement at **upper-division level**.

Rule 1 — Narrow focus: Choose ONE specific high-yield subtopic (e.g., folding pathway step, chaperone role, disease misfolding mechanism). Use it consistently throughout.
Rule 2 — Non-redundancy: Each section must cover a **different dimension** of the subtopic (mechanism vs application vs pitfalls vs practice).
Rule 3 — Upper-division contract: Avoid elementary filler. Anchor content in enzymes, pathways, regulators, kinetics, energetics, experimental methods, or pathological relevance.




Then, based on the student's selected content preferences, include ONLY the following sections:
{requested_blocks if requested_blocks.strip() else "- If no sections selected, include Summary + Flashcards."}

QUIZ FORMAT (MANDATORY — DO NOT DEVIATE):
Under <h2>Quiz</h2> output EXACTLY this structure for 5 questions:
<ol class="quiz">
  <li class="question" data-answer="A|B|C|D">
    <div class="prompt">Question text…</div>
    <ul class="choices">
      <li>A) Choice A</li>
      <li>B) Choice B</li>
      <li>C) Choice C</li>
      <li>D) Choice D</li>
    </ul>
  </li>
  <!-- repeat to total 5 li.question items -->
</ol>
Rules:
- NO <br> between choices.
- Exactly one correct letter in data-answer per question.
- Do NOT add an "Answers:" paragraph anywhere.

Conclude with 2–3 sentences that recap the unique subtopic insights and tease what's next:
- Next Up: {next_teaser}

STRUCTURE (use HTML tags exactly):
<h1>{section_title}</h1>

<p>Short motivational intro (2–3 sentences) that names the chosen subtopic.</p>

{structure_html}

<h2>Conclusion</h2>
<p>Recap + Next Up teaser.</p>

Global requirements:
- Each section must present **new information** (no overlaps, no paraphrasing).
- Write at advanced college level (biochemistry/upper-division).
- Return valid HTML using <h1>, <h2>, <p>, <ul>, <ol>, <li>, and inline <strong>. No tables or inline styles.
"""

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )

    # --- Light QA ---
    raw_html = response.choices[0].message.content.strip()
    if raw_html.startswith("```"):
        raw_html = raw_html.strip("`").strip()
    if "```" in raw_html:
        raw_html = raw_html.split("```")[0]
    if len(raw_html) > 15000:
        raw_html = raw_html[:15000] + "\n<p>[Content trimmed]</p>"

    return raw_html
