from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def write_full_newsletter(
    topic, demographic, tone, title,
    plan_title, section_title, position_in_plan,
    past_content=None
):
    # Add repetition blocker
    past_note = f"""
Below are previous newsletters written for this user.

Avoid repeating any ideas, examples, language, or hooks. Do not reuse frameworks or examples even if phrased differently.

=== BEGIN PAST CONTENT ===
{past_content}
=== END PAST CONTENT ===
""" if past_content else ""

    prompt = f"""
You are a professional newsletter writer for a premium 5-part email series.

This is email {position_in_plan} of a 5-part series titled "{plan_title}" for {demographic} about "{topic}".
The subject/title of this specific email is "{title}".

Write the newsletter in a {tone} tone.

{past_note}

Use this structure:
<h1>{title}</h1>
<p>Open with a strong hook. Be bold, specific, and valuable. No generic intros.</p>

<h2>{section_title}</h2>
<p>Make this email focused, smart, and original. Start with a bold insight, stat, or surprising fact.</p>
<p>Then explain what it means and why it matters for a {demographic}. Include an example, a trend, or a fresh takeaway.</p>
<p>End with a short call-to-action, tip, or something forwardable.</p>

Requirements:
- Only use <h1>, <h2>, and <p> tags.
- No inline styles, fluff, summaries, or vague platitudes.
- Write like a subject matter expert, in a way that’s engaging and skimmable.

Return only valid, clean HTML. Do not include commentary or notes.
"""

    response = client.chat.completions.create(
        model="gpt-4",  # switch to gpt-3.5-turbo if needed
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    return response.choices[0].message.content.strip()
