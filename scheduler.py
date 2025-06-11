import sqlite3
import json
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from newsletter_writer import write_full_newsletter

import os
from dotenv import load_dotenv
import requests
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
import time  # ✅ Added for delay before deletion request

# Load environment variables
load_dotenv()

# Brevo config
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_TEMPLATE_ID = os.getenv("BREVO_TEMPLATE_ID")

if not BREVO_API_KEY:
    print("❌ ERROR: BREVO_API_KEY is missing in .env")
if not BREVO_TEMPLATE_ID:
    print("❌ ERROR: BREVO_TEMPLATE_ID is missing in .env")

def send_email(to_email, subject, html_content):
    print(f"📩 Preparing to send email to {to_email}")
    print(f"📄 Subject: {subject}")
    print(f"🧩 Template ID: {BREVO_TEMPLATE_ID}")
    print(f"🧾 Content preview:\n{html_content[:300]}...\n")

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = BREVO_API_KEY

    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        template_id=int(BREVO_TEMPLATE_ID),
        params={
            "title": subject,
            "content": html_content
        }
    )

    try:
        response = api_instance.send_transac_email(email)
        print(f"✅ Brevo API Response: {response}")
    except ApiException as e:
        print(f"❌ Brevo API Exception while sending to {to_email}: {e}")

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
    now = datetime.now(timezone.utc)
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

        c.execute("SELECT * FROM newsletters WHERE id = ?", (plan_id,))
        plan = c.fetchone()
        if not plan:
            print(f"❌ Plan with plan_id {plan_id} not found.")
            continue

        user_id = plan['user_id']
        topic = plan['topic']
        demographic = plan['demographic']
        tone = plan['tone']
        plan_title = plan['plan_title']
        section_titles = json.loads(plan['section_titles'])
        summary = plan['summary'] if 'summary' in plan.keys() else ''

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

        c.execute("SELECT email FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        if user:
            send_email(user['email'], title, html)
        else:
            print(f"⚠️ No user found with ID {user_id}")

        c.execute("""
            UPDATE emails
            SET html_content = ?, sent = 1
            WHERE email_id = ?
        """, (html, email_row['email_id']))

        c.execute("""
            INSERT INTO past_newsletters (plan_id, content, created_at)
            VALUES (?, ?, ?)
        """, (plan_id, html, now.isoformat()))

        print(f"✅ Logged + Sent: {title}")

        conn.commit()

        # ✅ Check if this plan is complete
        c.execute("""
            SELECT COUNT(*) FROM emails 
            WHERE plan_id = ? AND user_id = ?
        """, (plan_id, user_id))
        total = c.fetchone()[0]

        c.execute("""
            SELECT COUNT(*) FROM emails 
            WHERE plan_id = ? AND user_id = ? AND sent = 1
        """, (plan_id, user_id))
        sent = c.fetchone()[0]

        print(f"🔍 Debug — Plan {plan_id}: total={total}, sent={sent}")
        plan_complete = (sent == total)
        print(f"✅ Plan complete? {plan_complete}")

        conn.close()

        if plan_complete:
            try:
                for i in range(5):
                    verify_conn = sqlite3.connect('newsletter.db')
                    verify_cursor = verify_conn.cursor()
                    verify_cursor.execute("SELECT COUNT(*) FROM emails WHERE plan_id = ? AND sent = 1", (plan_id,))
                    verified_sent = verify_cursor.fetchone()[0]
                    verify_conn.close()

                    print(f"🔄 Retry check ({i+1}/5): sent={verified_sent} (expected {total})")
                    if verified_sent == total:
                        break
                    time.sleep(0.5)

                print(f"🔁 Sending POST to /check-and-delete-plan for plan_id={plan_id}")
                response = requests.post(
                    "http://localhost:5000/check-and-delete-plan",
                    data={"plan_id": plan_id}
                )
                print(f"🧹 Cleanup status: {response.status_code}")
                print(f"🧹 Cleanup response: {response.text}")
            except Exception as e:
                print(f"❌ Failed to delete plan {plan_id}: {e}")

scheduler = BlockingScheduler()
scheduler.add_job(check_and_send, 'interval', minutes=1)
print("🕒 Scheduler started. Checking every 1 minute.")
scheduler.start()
