from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine, select, func, and_
from models import Email, Newsletter, PastNewsletter, User
from db import SessionLocal  # SQLAlchemy session
import json
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from newsletter_writer import write_full_newsletter
import os
from dotenv import load_dotenv
import requests
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
import time  # ✅ Added for delay before deletion request
from dateutil.relativedelta import relativedelta  # ✅ Needed for monthly scheduling
from models import SchoolNewsletter
import threading
send_lock = threading.Lock()
from filelock import FileLock



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
    print(f"🗞 Content preview:\n{html_content[:300]}...\n")

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = BREVO_API_KEY

    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        template_id=int(BREVO_TEMPLATE_ID),
        sender={"name": "MyNewsletter AI", "email": "no-reply@mynewsletterai.com"},
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
    db = SessionLocal()
    try:
        # Get plan IDs for the user
        plan_ids = db.query(Newsletter.id).filter(Newsletter.user_id == user_id).subquery()

        # Fetch recent past newsletters
        rows = (
            db.query(PastNewsletter.content)
            .filter(PastNewsletter.plan_id.in_(plan_ids))
            .order_by(PastNewsletter.created_at.desc())
            .limit(limit)
            .all()
        )

        return "\n\n---\n\n".join([r[0] for r in rows])
    finally:
        db.close()


from filelock import FileLock, Timeout

def check_and_send():
    lock_path = "send_scheduler.lock"

    try:
        with FileLock(lock_path, timeout=1):
            print("✅ File lock acquired. Starting send process.")

            now = datetime.now(timezone.utc)
            db = SessionLocal()

            try:
                # Fetch due emails
                due_emails = (
                    db.query(Email)
                    .filter(Email.sent == False, Email.send_date <= now)
                    .all()
                )

                print(f"⏱ Checked at {now.isoformat()} — {len(due_emails)} emails due")

                for email in due_emails:
                    # Try regular Newsletter plan
                    plan = db.query(Newsletter).filter(Newsletter.id == email.plan_id).first()
                    plan_type = "general"

                    if not plan:
                        # Try SchoolNewsletter plan
                        plan = db.query(SchoolNewsletter).filter(SchoolNewsletter.id == email.plan_id).first()
                        plan_type = "school"

                    if not plan:
                        print(f"❌ Plan with plan_id {email.plan_id} not found in either table.")
                        continue

                    user = db.query(User).filter(User.id == plan.user_id).first()
                    if not user:
                        print(f"⚠️ No user found with ID {plan.user_id}")
                        continue

                    try:
                        if plan_type == "general":
                            past_content = get_recent_newsletters(plan.user_id)

                            html = write_full_newsletter(
                                topic=plan.topic,
                                demographic=plan.demographic,
                                tone=plan.tone,
                                title=email.title,
                                plan_title=plan.plan_title,
                                section_title=json.loads(plan.section_titles)[email.position_in_plan - 1],
                                position_in_plan=email.position_in_plan,
                                past_content=past_content
                            )

                        elif plan_type == "school":
                            from newsletter_writer_school import write_study_email  # New GPT writer for school

                            html = write_study_email(
                                course_name=plan.course_name,
                                topics=plan.topics,
                                section_title=email.title,
                                content_types=json.loads(plan.content_types),
                                plan_title=plan.course_name,
                                position_in_plan=email.position_in_plan,
                                past_content=None  # Optional for now
                            )

                    except Exception as e:
                        print(f"❌ GPT generation failed for email {email.id}: {e}")
                        continue

                    # Send the email
                    send_email(user.email, email.title, html)

                    # Update email row
                    email.html_content = html
                    email.sent = True

                    # Log past newsletter
                    past = PastNewsletter(
                        plan_id=email.plan_id,
                        content=html,
                        created_at=now
                    )
                    db.add(past)

                    # Update next send time
                    freq = plan.frequency.lower()
                    if freq == 'daily':
                        plan.next_send_time = now + timedelta(days=1)
                    elif freq == 'bidaily':
                        plan.next_send_time = now + timedelta(days=2)
                    elif freq == 'weekly':
                        plan.next_send_time = now + timedelta(weeks=1)
                    else:
                        plan.next_send_time = now + timedelta(days=7)

                    db.commit()
                    print(f"✅ Logged + Sent: {email.title}")

                    # Check if plan is complete
                    total = db.query(Email).filter_by(plan_id=plan.id, user_id=plan.user_id).count()
                    sent = db.query(Email).filter_by(plan_id=plan.id, user_id=plan.user_id, sent=True).count()

                    print(f"🔍 Debug — Plan {plan.id}: total={total}, sent={sent}")
                    if total == sent:
                        print(f"✅ Plan complete? True")

                        # Verify with retries
                        for i in range(5):
                            verified_sent = db.query(Email).filter_by(plan_id=plan.id, sent=True).count()
                            print(f"🔄 Retry check ({i+1}/5): sent={verified_sent} (expected {total})")
                            if verified_sent == total:
                                break
                            time.sleep(0.5)

                        # Trigger deletion
                        try:
                            print(f"🔁 Sending POST to /check-and-delete-plan for plan_id={plan.id}")
                            response = requests.post(
                                "http://localhost:5000/check-and-delete-plan",
                                data={"plan_id": plan.id}
                            )
                            print(f"🧹 Cleanup status: {response.status_code}")
                            print(f"🧹 Cleanup response: {response.text}")
                        except Exception as e:
                            print(f"❌ Failed to delete plan {plan.id}: {e}")

            finally:
                db.close()

    except Timeout:
        print("⚠️ Skipping send: another process is already running.")


scheduler = BackgroundScheduler()
scheduler.add_job(check_and_send, 'interval', minutes=1)
print("🕒 Scheduler started. Checking every 1 minute.")
