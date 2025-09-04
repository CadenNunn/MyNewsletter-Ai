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

# --- NEW: tiny subject inferrer (zero deps) ---
def _infer_subject(course_name: str, section_title: str, topics) -> str:
    text = f"{course_name} {section_title} {' '.join(topics) if isinstance(topics, (list, tuple)) else topics}".lower()
    keys = {
        "math": ["calculus","algebra","geometry","trig","statistics","probability","matrix","derivative","integral","limit","equation","function"],
        "language": ["spanish","french","german","italian","conjugation","vocabulary","vocab","grammar","listening","speaking","reading","pronunciation","preterite","subjunctive"],
        "bio": ["biology","biochem","enzyme","cell","genetics","physiology","anatomy","pathway","protein"],
        "chem": ["chemistry","stoichiometry","equilibrium","acid","base","redox","organic","enthalpy","bonding","orbitals"],
        "physics": ["kinematics","forces","electric","magnetic","optics","thermo","quantum"],
        "cs": ["algorithm","data structure","python","java","recursion","complexity"],
        "history": ["revolution","empire","treaty","primary source","chronology","industrial","medieval","renaissance"],
    }
    best, score = "generic", 0
    for k, kw in keys.items():
        s = sum(kwd in text for kwd in kw)
        if s > score:
            best, score = k, s
    return best



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

    # --- Subject-aware section descriptions (minimal logic, big payoff) ---
    subject = _infer_subject(course_name, section_title, topics)

    if subject == "math":
        section_descriptions = {
            "summary": (
                "- Explain the key ideas for ONE specific concept in plain text (no LaTeX). "
                "List core formulas (as plaintext), constraints (domain/assumptions), and the most common mistake on exams."
            ),
            "example": (
                "- Provide ONE fully worked problem (step-by-step). "
                "Each step on a new line with a short justification. Keep numbers realistic."
            ),
            "quiz": (
                "- Write 5 exam-style problems mixing conceptual and quick-calculation items. "
                "Each stem must be a concrete problem; distractors should be plausible. "
                "Mark the correct letter via the data-attribute (see format). No answer paragraph."
            ),
            "flashcards": (
                "- Write 8 formula/definition flashcards as Term::Definition lines. "
                "Prefer canonical forms and short definitions."
            ),
            "suggested reading": (
                "- Suggest 2–3 practice sources (chapters/sections or problem sets) that target the exact weaknesses implied by the quiz."
            ),
        }
    elif subject == "language":
        section_descriptions = {
            "summary": (
                "- Explain ONE grammar/usage point in 5 bullets: when to use it, key pattern, 2 high-frequency examples, and a common mistake."
            ),
            "example": (
                "- Create ONE 4–6 line mini-dialogue showcasing the pattern. "
                "Each line immediately followed by a translation."
            ),
            "quiz": (
                "- Write 5 MCQs biased toward cloze (fill-the-blank) with conjugation/word-choice/meaning. "
                "Keep options short, high-frequency, and only one clearly correct by context. "
                "Mark the correct letter via data-attribute. No answer paragraph."
            ),
            "flashcards": (
                "- Write 10 flashcards as Word/Phrase::Meaning | Example (very short, high-frequency)."
            ),
            "suggested reading": (
                "- Suggest 2–3 resources (chapter/lesson/video) that emphasize listening + production for this pattern."
            ),
        }
    else:
        # generic / science / everything else — your prior behavior, but cleaner
        section_descriptions = {
            "summary": (
                "- Explain the subtopic’s mechanism and logic (steps, drivers, exceptions). "
                "Go beyond definition with at least one non-obvious insight."
            ),
            "example": (
                "- Provide ONE realistic applied scenario and explain the reasoning chain to the outcome. "
                "Must feel different from the summary."
            ),
            "quiz": (
                "- Write 5 application-heavy MCQs. "
                "Each question must declare the correct letter via data-attribute. No separate answers block."
            ),
            "flashcards": (
                "- Write 6–8 flashcards covering distinct angles (definition, regulator, detection, pitfall)."
            ),
            "suggested reading": (
                "- Suggest 2–3 targeted resources that complement the above content."
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

    # Subject-specific rule text for higher exam relevance
    if subject == "math":
        subject_rules = (
            "Rule 3 — Exam-match contract: Prefer concrete, solvable problems with realistic numbers. "
            "Show reasoning clearly. Prioritize functions, graphs, limits/derivatives/integrals, algebraic manipulation, and common traps."
        )
        quiz_rules = (
            "For math, stems must be actual problems (compute/decide). "
            "At least 3 items require calculation or symbolic manipulation. Distractors must be plausible (common algebra/calculus errors)."
        )
    elif subject == "language":
        subject_rules = (
            "Rule 3 — Exam-match contract: Prefer production/recognition tasks. "
            "Focus on cloze with correct forms, meaning-by-context, and short translations. Use high-frequency vocabulary."
        )
        quiz_rules = (
            "For language, prefer cloze with one clearly correct form/word by context; keep options short and natural. "
            "Include at least 1 conjugation item and 1 meaning/usage judgment."
        )
    else:
        subject_rules = (
            "Rule 3 — Exam-match contract: Emphasize application, edge cases, and interpretation. "
            "Anchor in mechanisms, cases, trade-offs, and pitfalls relevant to typical exams in this subject."
        )
        quiz_rules = (
            "Ensure stems test application/interpretation rather than recall. Distractors should reflect common misconceptions."
        )

    prompt = f"""
You are an expert study strategist creating a study email for a course titled "{plan_title}".
This is email {position_in_plan}. Today's declared focus is: "{section_title}"
The broader course/topics context is: "{topics}"
Detected subject type: {subject}

Task: produce exam-grade reinforcement aligned to what students actually see on tests.

Rule 1 — Narrow focus: Choose ONE specific high-yield subtopic appropriate to {subject} and use it consistently.
Rule 2 — Non-redundancy: Each section must cover a **different dimension** (concept/mechanism vs application vs pitfalls vs practice).
{subject_rules}

{past_note}

Include ONLY the following sections based on the student's selected preferences:
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
- {quiz_rules}

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
- Write at an advanced college level appropriate to {subject}.
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
