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

def _freq_to_timedelta(freq: str) -> timedelta:
    freq = (freq or "").lower()
    if freq == 'daily':
        return timedelta(days=1)
    if freq == 'bidaily':
        return timedelta(days=2)
    if freq == 'weekly':
        return timedelta(weeks=1)
    return timedelta(days=7)

def _schedule_next_school_email(db, plan: SchoolNewsletter, just_sent_position: int, now_utc: datetime):
    """Create the next Email row. If new topics are exhausted but we haven't hit the selected total,
    keep scheduling by cycling prior topics (simple review placeholder)."""
    try:
        topics_list = json.loads(plan.topics) if plan.topics else []
    except Exception:
        topics_list = []

    total_topics = len(topics_list)
    total_allowed = plan.max_emails if getattr(plan, 'max_emails', None) else total_topics  # Pro: no explicit cap beyond topics

    next_pos = just_sent_position + 1
    if next_pos > total_allowed:
        return None  # reached the user-selected total emails

    if next_pos <= total_topics:
        # still sending new topics
        next_topic = topics_list[next_pos - 1]
    else:
        # no new topics left: cycle through earlier topics as review
        if total_topics == 0:
            return None
        review_index = (next_pos - total_topics - 1) % total_topics
        next_topic = topics_list[review_index]

    interval = _freq_to_timedelta(plan.frequency)
    send_at = now_utc + interval

    next_email = Email(
        user_id=plan.user_id,
        plan_id=plan.id,
        position_in_plan=next_pos,
        topic=next_topic,
        title=None,
        send_date=send_at,
        sent=False
    )
    db.add(next_email)

    plan.next_send_time = send_at
    db.commit()
    db.refresh(next_email)
    return next_email



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
        sender={"name": "Memoraid", "email": "no-reply@mynewsletterai.com"},
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
                            from newsletter_writer_school import write_study_email, generate_subject_line

                            try:
                                topics_list = json.loads(plan.topics) if plan.topics else []
                            except Exception:
                                topics_list = []

                            current_topic = email.topic or (
                                topics_list[email.position_in_plan - 1] if 0 <= (email.position_in_plan - 1) < len(topics_list) else "Untitled Topic"
                            )

                            # Generate content exactly like before
                            subject_line = generate_subject_line(current_topic, plan.course_name)

                            html = write_study_email(
                                course_name=plan.course_name,
                                topics=plan.topics,
                                section_title=current_topic,
                                content_types=json.loads(plan.content_types),
                                plan_title=plan.course_name,
                                position_in_plan=email.position_in_plan,
                                past_content=None
                            )

                            # ✅ No email send — publish to dashboard only
                            # Keep the subject for reference
                            # ✅ Send lightweight notification via Brevo
                            try:
                                subject_line = f"[{plan.course_name}] {current_topic} — Lesson #{email.position_in_plan}"
                                send_email(user.email, subject_line, "")
                                print(f"📧 Notification sent to {user.email} for lesson {email.position_in_plan}")
                            except Exception as e:
                                print(f"⚠️ Failed to send Brevo notification: {e}")

                            # Store subject for dashboard/history
                            email.title = subject_line


                    except Exception as e:
                        print(f"❌ GPT generation failed for email {getattr(email, 'email_id', None)}: {e}")
                        continue


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

                    # Rolling scheduling: for SCHOOL plans, schedule the next email after a successful send
                    if plan_type == "school":
                        try:
                            topics_list = json.loads(plan.topics) if plan.topics else []
                        except Exception:
                            topics_list = []

                        total_topics = len(topics_list)
                        total_allowed = plan.max_emails if getattr(plan, 'max_emails', None) else total_topics

                        sent_count = db.query(Email).filter_by(plan_id=plan.id, user_id=plan.user_id, sent=True).count()

                        print(f"🔍 Debug — Plan {plan.id} (school): total_allowed={total_allowed}, total_topics={total_topics}, sent_count={sent_count}")

                        if sent_count >= total_allowed:
                            print("✅ School plan complete — reached selected total emails")

                            # Verify with retries (paranoia)
                            for i in range(5):
                                verified_sent = db.query(Email).filter_by(plan_id=plan.id, sent=True).count()
                                if verified_sent >= total_allowed:
                                    break
                                time.sleep(0.5)

                            # Mark complete; do not delete
                            plan.first_pass_complete = True
                            plan.completed_at = now
                            plan.next_send_time = None
                            db.commit()

                            remaining_new = max(total_topics - min(total_topics, total_allowed), 0)
                            if remaining_new > 0:
                                print(f"ℹ️ {remaining_new} new topics remain locked — show upgrade CTA.")

                        else:
                            # Schedule next one (new if available, otherwise review-cycle), until total_allowed is reached
                            next_email = _schedule_next_school_email(
                                db=db,
                                plan=plan,
                                just_sent_position=email.position_in_plan,
                                now_utc=now
                            )
                            if next_email:
                                print(f"📅 Next school email scheduled: id={next_email.email_id}, position={next_email.position_in_plan}, send_date={next_email.send_date.isoformat()}")



                    else:
                        # GENERAL plans keep their current behavior (you can convert later if desired)
                        total = db.query(Email).filter_by(plan_id=plan.id, user_id=plan.user_id).count()
                        sent = db.query(Email).filter_by(plan_id=plan.id, user_id=plan.user_id, sent=True).count()

                        print(f"🔍 Debug — Plan {plan.id} (general): total={total}, sent={sent}")
                        if total == sent:
                            print(f"✅ Plan complete? True")
                            for i in range(5):
                                verified_sent = db.query(Email).filter_by(plan_id=plan.id, sent=True).count()
                                if verified_sent == total:
                                    break
                                time.sleep(0.5)
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
