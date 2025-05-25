import sqlite3
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from newsletter_writer import write_full_newsletter
import json

def get_recent_newsletters(user_id, limit=5):
    conn = sqlite3.connect('newsletter.db')
    c = conn.cursor()
    c.execute("""
        SELECT content FROM past_newsletters
        WHERE plan_id IN (
            SELECT id FROM newsletters WHERE user_id = ?
        )
        ORDER BY created_at DESC
        LIMIT ?
    """, (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return "\n\n---\n\n".join([r[0] for r in rows])

def check_and_send():
    now = datetime.now()
    conn = sqlite3.connect('newsletter.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("""
        SELECT * FROM emails
        WHERE sent = 0 AND send_date <= ?
    """, (now.isoformat(),))
    due_emails = c.fetchall()

    print(f"⏱ Checked at {now.isoformat()} — {len(due_emails)} emails due")

    for email_row in due_emails:
        plan_id = email_row['plan_id']
        position = email_row['position_in_plan']
        title = email_row['title']

        # Load the matching plan from newsletters
        c.execute("SELECT * FROM newsletters WHERE id = ?", (plan_id,))
        plan = c.fetchone()
        if not plan:
            print(f"❌ Plan with plan_id {plan_id} not found in newsletters.")
            continue

        # Extract fields from the plan
        user_id = plan['user_id']
        topic = plan['topic']
        demographic = plan['demographic']
        tone = plan['tone']
        plan_title = plan['plan_title']
        section_titles = json.loads(plan['section_titles'])
        summary = plan['summary'] if 'summary' in plan.keys() else ''

        # Get past newsletters for context
        past_content = get_recent_newsletters(user_id)

        try:
            html = write_full_newsletter(
                topic=topic,
                demographic=demographic,
                tone=tone,
                title=title,
                plan_title=plan_title,
                section_title=section_titles[position - 1],
                position_in_plan=position,
                past_content=past_content
            )
        except Exception as e:
            print(f"❌ GPT generation failed for email {email_row['email_id']}: {e}")
            continue

        # Update emails table
        c.execute("""
            UPDATE emails
            SET html_content = ?, sent = 1
            WHERE email_id = ?
        """, (html, email_row['email_id']))

        # Log into past_newsletters (NO user_id column in that table)
        c.execute("""
            INSERT INTO past_newsletters (plan_id, content, created_at)
            VALUES (?, ?, ?)
        """, (plan_id, html, now.isoformat()))

        print(f"✅ Sent: {title}")

    conn.commit()
    conn.close()

# Scheduler startup
scheduler = BlockingScheduler()
scheduler.add_job(check_and_send, 'interval', minutes=1)
print("🕒 Scheduler started. Checking every 1 minute.")
scheduler.start()
