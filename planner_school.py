import os
import json
from openai import OpenAI
from dotenv import load_dotenv

# Load API key from .env
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MAX_TOPICS = 200  # hard safety cap

def _coerce_topics_list(maybe_list):
    """Validate, strip, dedupe (case-insensitive), keep natural order, cap by MAX_TOPICS."""
    if not isinstance(maybe_list, list):
        return []
    seen = set()
    out = []
    for x in maybe_list:
        s = str(x).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= MAX_TOPICS:
            break
    # basic sanity: drop extremely short junk
    out = [t for t in out if len(t) >= 3]
    return out

def create_study_plan(course_name, topics_or_text, content_types, num_emails=None):
    """
    UPDATED: Extracts as many academically specific topics as are present in the input.
    - topics_or_text: list[str] (candidate topics) OR str (paragraph/notes)
    - content_types: kept for future prompt conditioning (not strictly used here)
    - num_emails: ignored for extraction count; you can still use it elsewhere for scheduling
    Returns: (plan_title: str, summary: str, topics_list: list[str]) or (None, None, None)
    """

    # Prepare model input block
    if isinstance(topics_or_text, list):
        input_kind = "LIST"
        provided_block = "\n".join(f"- {t}" for t in topics_or_text)
    else:
        input_kind = "PARAGRAPH_OR_MIXED"
        provided_block = str(topics_or_text or "").strip()

    # Prompt: detect type, extract ALL specific subtopics found (1..N), no fixed length
    prompt = f"""
You are an expert academic strategist.

Course title: "{course_name}"

Input kind: {input_kind}
Input provided (may be a paragraph/summary or a messy/clean list of topics):
\"\"\" 
{provided_block[:8000]}
\"\"\"

Your tasks:
1) Create a concise series title.
2) Write a 2–3 sentence motivational summary that frames the plan as an active journey (e.g., "You'll build mastery step by step" / "Each review will sharpen recall").
3) Extract ALL academically specific subtopics that are clearly present or implied by the input.
   - Return as many as are actually present (1..N), not a fixed count.
   - Be precise (e.g., "SN2 Mechanism and Stereochemistry" not "Chemistry").
   - Normalize/merge/clarify duplicates; preserve logical order if a list is given.
   - Avoid vague/broad items; avoid duplicates.

Return ONLY valid JSON in this exact format (no prose before/after):
{{
  "plan_title": "Your series title here",
  "summary": "A short motivational summary here.",
  "topics": ["topic 1", "topic 2", "..."]
}}
"""

    def _ask(p):
        return client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": p}],
            temperature=0.2  # low for consistency
        )

    content = None
    try:
        # Attempt 1
        resp = _ask(prompt)
        content = resp.choices[0].message.content.strip()
        parsed = json.loads(content)

        plan_title = str(parsed.get("plan_title", "Study Plan")).strip()
        summary    = str(parsed.get("summary", "")).strip()
        topics_list = _coerce_topics_list(parsed.get("topics", []))

        if not topics_list:
            raise ValueError("No topics extracted.")

        return plan_title, summary, topics_list

    except Exception as e1:
        # Strict retry if JSON/structure fails
        try:
            retry_prompt = (
                prompt
                + "\n\nREMINDER: Return ONLY valid JSON with keys plan_title, summary, topics (array). No commentary."
            )
            resp2 = _ask(retry_prompt)
            content = resp2.choices[0].message.content.strip()
            parsed = json.loads(content)

            plan_title = str(parsed.get("plan_title", "Study Plan")).strip()
            summary    = str(parsed.get("summary", "")).strip()
            topics_list = _coerce_topics_list(parsed.get("topics", []))

            if not topics_list:
                raise ValueError("No topics extracted (retry).")

            return plan_title, summary, topics_list

        except Exception as e2:
            print("❌ Error in create_study_plan (both attempts failed):", e1, "|", e2)
            print("❌ Last raw model output:\n", content or "<no content>")
            return None, None, None
