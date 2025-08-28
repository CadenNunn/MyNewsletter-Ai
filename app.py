import os
import sqlite3
import time
from flask import Flask, session, redirect, request, flash, url_for, render_template
from dotenv import load_dotenv
import stripe
import openai
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from gpt_planner import create_email_plan
import json
from db import SessionLocal
from models import User, Review, Newsletter, Email, PastNewsletter
from db import SessionLocal
from models import Newsletter, Email
from sqlalchemy import func
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy import asc, desc
from math import ceil
from models import SchoolNewsletter
from planner_school import create_study_plan
from utils.syllabus_parser import extract_topics_from_syllabus
from flask_session import Session
import redis
import psycopg2
from psycopg2.extras import RealDictCursor







# ✅ Add these for PostgreSQL
import psycopg2
import psycopg2.extras




# Load environment variables
load_dotenv()
print("✅ STRIPE_WEBHOOK_SECRET:", os.getenv('STRIPE_WEBHOOK_SECRET'))

# Set API keys
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
openai.api_key = os.getenv("OPENAI_API_KEY")






# Flask app setup
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")


# Redis Session Configuration
app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_REDIS'] = redis.from_url(os.getenv('REDIS_URL'))

# Redis Session Configuration For local testing
#app.config['SESSION_TYPE'] = 'redis'
#app.config['SESSION_REDIS'] = redis.Redis(host='localhost', port=6379)

# Initialize Flask-Session
Session(app)

# --- DB helpers must be defined BEFORE scheduler tries to use them ---
from flask import request, render_template, redirect
import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv('DATABASE_URL')

def _is_local_db(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or '').lower()
        return host in ('localhost', '127.0.0.1', '::1')
    except Exception:
        return False


def pg_connect(dict_cursor: bool = True):
    """Centralized Postgres connection with TLS + TCP keepalives."""
    kwargs = {
        "dsn": DATABASE_URL,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
    # Only require SSL when NOT connecting to localhost
    if not _is_local_db(DATABASE_URL):
        kwargs["sslmode"] = "require"

    if dict_cursor:
        kwargs["cursor_factory"] = psycopg2.extras.RealDictCursor
    return psycopg2.connect(**kwargs)


def ensure_indexes_once():
    """Speed up due-email scans and dashboard/reschedule queries."""
    try:
        conn = pg_connect(dict_cursor=False)
        cur = conn.cursor()
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_emails_due
            ON emails (send_date)
            WHERE sent = false
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_emails_plan_sent_pos
            ON emails (plan_id, sent, position_in_plan)
        """)
        conn.commit()
    except Exception as e:
        print("⚠️ ensure_indexes_once failed:", e)
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass

ensure_indexes_once()

import threading
import atexit

RUN_SCHEDULER = os.getenv("RUN_SCHEDULER", "0") == "1"

def _acquire_scheduler_lock():
    """
    Use a Postgres advisory lock so only ONE process/thread becomes the scheduler,
    even if something accidentally spawns more than one worker someday.
    """
    try:
        conn = pg_connect(dict_cursor=False)  # uses DATABASE_URL + keepalives
        cur = conn.cursor()
        # Pick a unique 64-bit key for your app (hash of 'memoraid-scheduler' for example)
        cur.execute("SELECT pg_try_advisory_lock(730563707911)")

        got = cur.fetchone()[0]
        if got:
            print("🔒 Acquired scheduler advisory lock.")
            return conn, cur  # keep connection open to hold the lock
        else:
            cur.close()
            conn.close()
            print("⏸️ Another instance holds the scheduler lock. Not starting scheduler here.")
            return None, None
    except Exception as e:
        print("⚠️ Failed to acquire scheduler lock:", e)
        return None, None

def _start_scheduler_if_leader():
    from scheduler import scheduler
    lock_conn, lock_cur = _acquire_scheduler_lock()
    if not lock_conn:
        return  # not the leader

    # Start scheduler in this process (background thread)
    scheduler.start()
    print("🟢 Scheduler started in web service.")

    @atexit.register
    def _release_lock():
        try:
            # advisory lock auto-releases on connection close, but be explicit
            if lock_cur:
                lock_cur.close()
            if lock_conn:
                lock_conn.close()
            print("🔓 Scheduler advisory lock released.")
        except Exception:
            pass

if RUN_SCHEDULER:
    # Run in a daemon thread to avoid blocking Flask/Gunicorn boot
    threading.Thread(target=_start_scheduler_if_leader, daemon=True).start()
    print("🟡 Scheduler init thread launched (will start only if lock acquired).")
else:
    print("🟡 Scheduler disabled (RUN_SCHEDULER=0).")




# Plan-based limits (subs-first)
# Plan-based limits (subs-first)
PLAN_FEATURES = {
    'free': {'max_total': 1, 'school': {'slider_max': 5}},   # total emails allowed
    'plus': {'max_total': 3, 'school': {'slider_max': 30}},
    'pro':  {'max_total': None, 'school': {'slider_max': None}},  # None = unlimited in UI
}


# Pricing Keys

STRIPE_PRICE_PLUS = os.getenv("STRIPE_PRICE_PLUS")
STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO")

STRIPE_PRICES = {
    "plus": STRIPE_PRICE_PLUS,
    "pro": STRIPE_PRICE_PRO,
}

from db import SessionLocal
from models import User

def _get_user_plan_features(db, user_id: int):
    user = db.query(User).filter(User.id == user_id).first()
    plan_name = (user.plan or 'free').lower() if user else 'free'
    return PLAN_FEATURES.get(plan_name, PLAN_FEATURES['free'])

def _slider_max_for_user(features: dict, topics_len: int) -> int:
    """Return the max slider value the user can pick (total emails, new + review)."""
    school_cfg = features.get('school', {})
    cap = school_cfg.get('slider_max')  # None => unlimited
    if cap is None:
        # Pro: give plenty of headroom; keep finite for slider UX
        return max(topics_len, 60)  # tweak later if you want
    return max(1, cap)

def _allowed_email_options(features: dict, topics_len: int):
    sch = features.get('school', {})
    cap = sch.get('max_emails_cap')  # None => unlimited
    counts = [n for n in sch.get('allowed_counts', [])
              if isinstance(n, int) and n <= topics_len and (cap is None or n <= cap)]
    # Fallback: always show at least one option
    if not counts:
        if cap is None:
            counts = [min(5, topics_len)] if topics_len > 0 else [5]
        else:
            counts = [min(cap, topics_len)] if topics_len > 0 else [cap or 5]
    include_all = bool(sch.get('allow_all')) and (cap is None) and topics_len > 0
    return counts, include_all, cap

def get_user_email(user_id):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        return user.email if user else None
    finally:
        db.close()




from flask import send_from_directory

@app.route('/adminer')
def serve_adminer():
    return send_from_directory('.', 'adminer.html')

import shutil, subprocess
from flask import jsonify

@app.route("/debug/binaries")
def debug_binaries():
    def try_run(cmd):
        try:
            return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).splitlines()[0]
        except Exception as e:
            return f"ERR: {e}"

    return jsonify({
        "which.tesseract": shutil.which("tesseract"),
        "which.pdftotext": shutil.which("pdftotext"),
        "tesseract_version": try_run(["tesseract", "--version"]),
        "pdftotext_version": try_run(["pdftotext", "-v"]),
    })



#---------- Scheduler auto delete ------------
import time
from db import SessionLocal
from models import Newsletter, Email, PastNewsletter

def delete_newsletter_plan(plan_id, user_id, retries=5, delay=0.5):
    for attempt in range(retries):
        try:
            db = SessionLocal()
            try:
                db.query(Newsletter).filter(Newsletter.id == plan_id).delete()
                db.query(Email).filter(Email.plan_id == plan_id).delete()
                db.query(PastNewsletter).filter(PastNewsletter.plan_id == plan_id).delete()
                db.commit()
                print(f"✅ Plan {plan_id} successfully deleted.")
                return True
            except Exception as e:
                db.rollback()
                if "locked" in str(e).lower() and attempt < retries - 1:
                    print(f"⚠️ DB locked, retrying... ({attempt + 1})")
                    time.sleep(delay)
                else:
                    print(f"❌ Final DB error: {e}")
                    return False
            finally:
                db.close()
        except Exception as outer_e:
            print(f"❌ Outer DB error on attempt {attempt + 1}: {outer_e}")
            time.sleep(delay)
    return False  # fallback if all retries fail

# ----------- Scheduler auto delete check ------

@app.route('/check-and-delete-plan', methods=['POST'])
def check_and_delete_plan():
    plan_id = request.form.get("plan_id")
    print(f"📥 Incoming /check-and-delete-plan request with plan_id={plan_id}")

    if not plan_id:
        print("❌ plan_id missing in request")
        return "Missing plan_id", 400

    try:
        plan_id = int(plan_id)
    except ValueError:
        print(f"❌ plan_id '{plan_id}' is not a valid integer")
        return "Invalid plan_id", 400

    time.sleep(1)

    db = SessionLocal()
    try:
        newsletter = db.query(Newsletter).filter(Newsletter.id == plan_id).first()
        if not newsletter:
            print(f"❌ [Flask] No newsletter plan found for ID {plan_id}")
            return "Plan not found", 404
        user_id = newsletter.user_id
        print(f"👤 Found user_id={user_id} for plan_id={plan_id}")

        total = db.query(Email).filter(Email.plan_id == plan_id).count()
        sent = db.query(Email).filter(Email.plan_id == plan_id, Email.sent == True).count()

        print(f"📊 Total emails in plan {plan_id}: {total}")
        print(f"📬 Sent emails for plan {plan_id}: {sent}")
    finally:
        db.close()

    if sent == total:
        print(f"🗑️ [Flask] All emails sent. Auto-deleting plan {plan_id}")
        deleted = delete_newsletter_plan(plan_id, user_id=user_id)
        print(f"✅ Deletion result: {'Success' if deleted else 'Failure'}")
        return "Deleted" if deleted else "Delete failed", 200
    else:
        print(f"ℹ️ [Flask] Plan {plan_id} not yet complete: {sent}/{total}")
        return "Not ready", 200

# ---------- Context Processor -------------
@app.context_processor
def inject_subscription_context():
    if 'user_id' not in session:
        return {}

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == session['user_id']).first()
        downgrade_to = user.downgrade_to if user else None
        subscription_end_date = user.subscription_end_date if user else None

        # 🛡️ Normalize to ISO string if it's a datetime; guard None
        try:
            from datetime import datetime, timezone
            if subscription_end_date is not None and not isinstance(subscription_end_date, str):
                # Expecting a datetime-like object; coerce to UTC ISO string
                if isinstance(subscription_end_date, datetime):
                    if subscription_end_date.tzinfo is None:
                        subscription_end_date = subscription_end_date.replace(tzinfo=timezone.utc)
                    subscription_end_date = subscription_end_date.isoformat()
                else:
                    # Unknown type; drop it to avoid .fromisoformat errors
                    print(f"⚠️ subscription_end_date unexpected type: {type(subscription_end_date)}; clearing.")
                    subscription_end_date = None
        except Exception as e:
            print("⚠️ Failed to coerce subscription_end_date to string:", e)
            subscription_end_date = None

        days_left = None
        subscription_end_display = None

        if isinstance(subscription_end_date, str) and subscription_end_date.strip():
            from datetime import datetime, timezone, date
            try:
                # tolerate trailing 'Z' and treat as UTC
                end_dt = datetime.fromisoformat(subscription_end_date.replace('Z', ''))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                subscription_end_display = end_dt.isoformat()
                today_utc = datetime.now(timezone.utc).date()
                days_left = (end_dt.date() - today_utc).days
                if days_left < 0:
                    days_left = None
            except Exception as e:
                print(f"⚠️ Invalid subscription_end_date format: {subscription_end_date} — {e}")
                subscription_end_display = subscription_end_date  # raw fallback

        return {
            'subscription_days_left': days_left,
            'downgrade_to': downgrade_to,
            'pending_downgrade': bool(downgrade_to),
            'subscription_end_display': subscription_end_display,
        }

    except Exception as e:
        print("❌ Error injecting subscription context:", e)
        return {}
    finally:
        db.close()


# ---------------- Home ----------------
@app.route("/")
def home():
    # If logged in, send straight to dashboard
    if "user_id" in session:
        return redirect(url_for("dashboard"))  # endpoint for /dashboard

    db = SessionLocal()
    try:
        reviews_query = (
            db.query(Review.name, Review.stars, Review.comment)
            .order_by(Review.id.desc())
            .limit(10)
            .all()
        )
        reviews = [
            {"name": r.name, "stars": r.stars, "comment": r.comment}
            for r in reviews_query
        ]
        return render_template("index.html", reviews=reviews, hide_header=True, hide_footer=True)
    finally:
        db.close()





# ---------- Date Formating ------------

from datetime import datetime, timedelta, timezone

@app.template_filter('format_datetime')
def format_datetime(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime('%B %d, %Y at %I:%M %p')  # e.g. July 7, 2025 at 02:00 PM
    except Exception as e:
        print(f"⚠️ Error formatting datetime: {value} → {e}")
        return value  # fallback

#---------- Choose newsletter -------
@app.route("/choose-newsletter-type")
def choose_newsletter_type():
    return render_template("choose_newsletter_type.html")


# ------------- Builder ----------------
@app.route('/build-newsletter', methods=['GET'])
def build_newsletter():
    if 'user_id' not in session:
        flash("Please log in to create a newsletter.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()

        if not user:
            flash("Could not verify your plan. Please try again.")
            return redirect(url_for('dashboard'))

        plan = user.plan
        limits = PLAN_FEATURES.get(plan, {'max_total': 1})

        # Count TOTAL newsletters across both tables
        general_count = db.query(Newsletter).filter(Newsletter.user_id == user_id).count()
        school_count = db.query(SchoolNewsletter).filter(SchoolNewsletter.user_id == user_id).count()
        total_count = general_count + school_count

        if limits['max_total'] is not None and total_count >= limits['max_total']:
            flash("You’ve reached your plan’s newsletter limit. Upgrade to create more.")
            return redirect(url_for('dashboard'))

        return render_template('build.html')

    finally:
        db.close()


@app.route('/generate-newsletter')
def generate_newsletter():
    data = session.get('newsletter_input')
    if not data:
        flash("Something went wrong. Please try again.")
        return redirect(url_for('build_newsletter'))

    # Call GPT
    plan_title, section_titles, summary = create_email_plan(
        data['topic'], data['demographic'], data['tone']
)


    if not plan_title or not section_titles or not summary:
        return "Failed to generate plan", 500

    # Store completed plan
    session['newsletter'] = {
        **data,
        'plan_title': plan_title,
        'section_titles': section_titles,
        'summary': summary
    }

    # Calculate max datetime (7-day window for scheduling)
    max_datetime = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

    return render_template('preview.html', plan=session['newsletter'], max_datetime=max_datetime)

#--------------- Create newsletter -------------------------

@app.route('/create-newsletter', methods=['POST'])
def create_newsletter():
    topic = request.form['topic']
    demographic = request.form['demographic']
    frequency = request.form['frequency']
    tone = request.form['tone']
    email = request.form.get('email')
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to create a newsletter.")
        return redirect(url_for('login'))

    session['newsletter_input'] = {
        'user_id': user_id,
        'email': email,
        'topic': topic,
        'demographic': demographic,
        'frequency': frequency,
        'tone': tone
    }

    return render_template('loading.html', topic=topic, demographic=demographic)


# ---------------- Confirmation & DB Storage ----------------
@app.route('/confirm-newsletter', methods=['POST'])
def confirm_newsletter():
    from datetime import datetime, timedelta, timezone
    import pytz

    data = session.get('newsletter', {})
    if not data:
        return redirect(url_for('build_newsletter'))

    frequency = request.form.get('frequency')
    send_time = request.form.get('send_time')
    plan_title = request.form.get('plan_title')
    data['frequency'] = frequency

    user_id = data.get('user_id')
    email = data.get('email')
    topic = data.get('topic')
    demographic = data.get('demographic')
    tone = data.get('tone')
    section_titles = data.get('section_titles')
    summary = data.get('summary')

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)

    if send_time == 'now':
        first_send = now
    elif send_time == 'tomorrow':
        first_send = now + timedelta(days=1)
    elif send_time == 'in_2_days':
        first_send = now + timedelta(days=2)
    elif send_time == 'in_3_days':
        first_send = now + timedelta(days=3)
    elif send_time == 'next_week':
        first_send = now + timedelta(days=7)
    else:
        try:
            naive = datetime.fromisoformat(send_time)
            local = pytz.timezone("America/Chicago").localize(naive)
            first_send = local.astimezone(timezone.utc)
            print("🕒 Custom time converted to UTC:", first_send.isoformat())
        except Exception as e:
            print("❌ Error parsing custom time, defaulting to now:", e)
            first_send = now

    freq_map = {'daily': 1, 'bidaily': 2, 'weekly': 7}
    interval_days = freq_map.get(frequency.lower(), 7)

    db = SessionLocal()

    # ✅ Plan limit enforcement (using max_total only)
    user = db.query(User).filter(User.id == user_id).first()
    plan = user.plan.lower() if user and user.plan else 'free'
    limits = PLAN_FEATURES.get(plan, {'max_total': 1})

    general_count = db.query(Newsletter).filter(Newsletter.user_id == user_id).count()
    school_count = db.query(SchoolNewsletter).filter(SchoolNewsletter.user_id == user_id).count()
    total_count = general_count + school_count

    if limits['max_total'] is not None and total_count >= limits['max_total']:
        db.close()
        flash("You’ve reached your plan’s newsletter limit. Upgrade to create more.")
        return redirect(url_for('dashboard'))

    # ✅ Insert newsletter (always active now)
    new_newsletter = Newsletter(
        user_id=user_id,
        email=email,
        topic=topic,
        demographic=demographic,
        plan_title=plan_title,
        section_titles=json.dumps(section_titles),
        summary=summary,
        frequency=frequency,
        tone=tone,
        next_send_time=first_send,
        is_active=1  # Always active
    )
    db.add(new_newsletter)
    db.commit()

    new_newsletter.plan_id = new_newsletter.id
    db.commit()

    print("🧾 Inserted Newsletter ID:", new_newsletter.id)

    # ✅ Insert scheduled emails
    for i, title in enumerate(section_titles):
        send_date = first_send + timedelta(days=interval_days * i)
        email_obj = Email(
            user_id=user_id,
            plan_id=new_newsletter.id,
            position_in_plan=i + 1,
            title=title,
            send_date=send_date,
            sent=False
        )
        db.add(email_obj)

    db.commit()
    db.close()

    return render_template("success.html", next_send=first_send.isoformat())

#---------------- SCHOOL newsletter builder --------
@app.route('/build-school-newsletter', methods=['GET'])
def build_school_newsletter():
    print("Session Data on Load:", dict(session))

    if 'user_id' not in session:
        flash("Please log in to create a school newsletter.")
        return redirect(url_for('login'))

    # --- Clear Syllabus Session if Reset Param is Present ---
    if request.args.get('reset') == 'true':
        session.pop('syllabus_course_name', None)
        session.pop('syllabus_extracted_topics', None)
        print("Session reset triggered. Cleared syllabus data.")

    user_id = session['user_id']
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        plan = user.plan
        limits = PLAN_FEATURES.get(plan, {'max_total': 1})

        # Count TOTAL newsletters across both tables
        general_count = db.query(Newsletter).filter(Newsletter.user_id == user_id).count()
        school_count = db.query(SchoolNewsletter).filter(SchoolNewsletter.user_id == user_id).count()
        total_count = general_count + school_count

        if limits['max_total'] is not None and total_count >= limits['max_total']:
            flash("You’ve reached your plan’s newsletter limit. Upgrade to create more.")
            return redirect(url_for('dashboard'))

        # Pass current session data (could be empty if reset)
        return render_template('build_school.html',
                               syllabus_course_name=session.get('syllabus_course_name', ''),
                               extracted_topics=session.get('syllabus_extracted_topics', []))

    finally:
        db.close()



from planner_school import create_study_plan  # Ensure this is at the top

@app.route('/generate-school-newsletter')
def generate_school_newsletter():
    data = session.get('school_newsletter_input')
    if not data:
        flash("Something went wrong. Please try again.")
        return redirect(url_for('build_school_newsletter'))

    course_name    = data['course_name']
    topics_or_text = data['topics']            # 👈 raw string OR list
    content_types  = data['content_types']     # list of strings

    # Call the updated planner (extracts ALL real topics; variable length)
    plan_title, summary, extracted_topics = create_study_plan(course_name, topics_or_text,     content_types)

    if not plan_title or not extracted_topics:
        flash("AI failed to generate a valid study plan. Please try again.")
        return redirect(url_for('build_school_newsletter'))

    # Persist the AI-extracted list (this becomes the source of truth going forward)
    session['school_newsletter'] = {
        **data,
        'course_name': course_name,
        'topics': extracted_topics,      # 👈 store the list now
        'content_types': content_types,
        'plan_title': plan_title,
        'section_titles': extracted_topics,  # if your template expects this key
        'summary': summary
    }

    # slider bounds: derive from the extracted list length
    db = SessionLocal()
    try:
        features   = _get_user_plan_features(db, data['user_id'])
        topics_len = len(extracted_topics)     # 👈 from AI output
        slider_max = _slider_max_for_user(features, topics_len)
    finally:
        db.close()


    max_datetime = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

    # ---- DEBUG: verify data headed to template ----
    try:
        _plan_dbg = session['school_newsletter']
        print("🧪 PREVIEW plan keys:", list(_plan_dbg.keys()))
        print("🧪 PREVIEW course_name:", repr(_plan_dbg.get('course_name')))
        print("🧪 PREVIEW frequency:", repr(_plan_dbg.get('frequency')))
        _topics = _plan_dbg.get('topics')
        print("🧪 PREVIEW topics type:", type(_topics).__name__, "len:", (len(_topics) if isinstance(_topics, list) else 'n/a'))
        print("🧪 PREVIEW first topic:", (_topics[0] if isinstance(_topics, list) and _topics else 'n/a'))
    except Exception as e:
        print("❌ PREVIEW debug print failed:", e)

    return render_template(
        'school_preview.html',
        plan=session['school_newsletter'],
        max_datetime=max_datetime,
        topics_len=topics_len,
        slider_max=slider_max
    )



#---------------- SCHOOL Create newsletter----------
@app.route('/create-school-newsletter', methods=['POST'])
def create_school_newsletter():
    course_name = request.form['course_name']
    topics = request.form['topics']
    frequency = request.form['frequency']
    email = request.form.get('email')
    content_types = request.form.getlist('content_types')  # comes in as list of strings
    user_id = session.get('user_id')

    if not user_id:
        flash("You must be logged in to create a school newsletter.")
        return redirect(url_for('login'))

    # Pass raw textarea (could be a paragraph or a list) to the planner
    raw_topics_or_text = (topics or "").strip()

    session['school_newsletter_input'] = {
        'user_id': user_id,
        'email': email,
        'course_name': course_name,
        'topics': raw_topics_or_text,    # 👈 raw string; planner will extract ALL topics
        'frequency': frequency,
        'content_types': content_types
    }




    return render_template('loading_school.html', topic=course_name, demographic="Students")


#----------------- SCHOOl confirm newsletter and database storage -------
@app.route('/confirm-school-newsletter', methods=['POST'])
def confirm_school_newsletter():
    from datetime import datetime, timedelta, timezone
    import pytz

    data = session.get('school_newsletter', {})
    if not data:
        return redirect(url_for('build_school_newsletter'))

    frequency = request.form.get('frequency')
    send_time = request.form.get('send_time')
    course_name = request.form.get('course_name') or data.get('course_name')
    topics_list = data.get('topics')  # full list
    content_types = data.get('content_types')
    summary = data.get('summary')
    user_id = data.get('user_id')
    email = data.get('email')

    # Slider value: total emails to send (new + review)
    raw_max = request.form.get('max_emails', '')
    try:
        requested_total = int(raw_max) if raw_max.strip() else None
    except ValueError:
        requested_total = None

    if not topics_list or not isinstance(topics_list, list):
        flash("No topics found for this study plan.")
        return redirect(url_for('build_school_newsletter'))

    # First send time
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if send_time == 'now':
        first_send = now
    elif send_time == 'tomorrow':
        first_send = now + timedelta(days=1)
    elif send_time == 'in_2_days':
        first_send = now + timedelta(days=2)
    elif send_time == 'in_3_days':
        first_send = now + timedelta(days=3)
    elif send_time == 'next_week':
        first_send = now + timedelta(days=7)
    else:
        try:
            naive = datetime.fromisoformat(send_time)
            local = pytz.timezone("America/Chicago").localize(naive)
            first_send = local.astimezone(timezone.utc)
        except Exception as e:
            print("❌ Error parsing custom time, defaulting to now:", e)
            first_send = now

    db = SessionLocal()

    # Enforce user plan total plans
    features = _get_user_plan_features(db, user_id)
    max_total = features.get('max_total', 1)
    general_count = db.query(Newsletter).filter(Newsletter.user_id == user_id).count()
    school_count  = db.query(SchoolNewsletter).filter(SchoolNewsletter.user_id == user_id).count()
    if max_total is not None and (general_count + school_count) >= max_total:
        db.close()
        flash("You’ve reached your plan’s newsletter limit. Upgrade to create more.")
        return redirect(url_for('dashboard'))

    # Clamp slider to plan’s slider_max
    topics_len = len(topics_list)
    slider_max = _slider_max_for_user(features, topics_len)
    if requested_total is None:
        # Pro selecting "All" or blank -> default to all new topics (topics_len)
        max_emails_total = topics_len
    else:
        max_emails_total = max(1, min(requested_total, slider_max))

    # Create plan (store full topics + total emails to send in first pass)
    new_school_plan = SchoolNewsletter(
        user_id=user_id,
        email=email,
        course_name=course_name,
        topics=json.dumps(topics_list),
        content_types=json.dumps(content_types),
        frequency=frequency,
        next_send_time=first_send,
        is_active=1,
        summary=summary,
        max_emails=max_emails_total      # NOW: total emails (new + review)
    )
    db.add(new_school_plan)
    db.commit()

    new_school_plan_id = new_school_plan.id
    print("🧾 Inserted SchoolNewsletter ID:", new_school_plan_id)

    # Schedule ONLY the first email
    first_topic = topics_list[0]
    first_email = Email(
        user_id=user_id,
        plan_id=new_school_plan_id,
        position_in_plan=1,
        topic=first_topic,
        title=None,
        send_date=first_send,
        sent=False
    )
    db.add(first_email)
    db.commit()
    db.close()

    # Cleanup
    for key in ['syllabus_course_name', 'syllabus_extracted_topics', 'syllabus_date_topic_map', 'date_sync_enabled']:
        session.pop(key, None)

    return render_template("success.html", next_send=first_send.isoformat())


#--------------- Syllabus Upload --------------------
@app.route('/upload-syllabus', methods=['POST'])
def upload_syllabus():
    if 'user_id' not in session:
        flash("Please log in to upload a syllabus.")
        return redirect(url_for('login'))

    syllabus_file = request.files.get('syllabus')
    if not syllabus_file:
        flash("No file uploaded.")
        return redirect(url_for('build_school_newsletter'))

    file_content = syllabus_file.read()

    # Get both course title and topics from AI
    extraction_result = extract_topics_from_syllabus(file_content, syllabus_file.filename)

    course_title = extraction_result.get('course_title', '')
    topics = extraction_result.get('topics', [])
    date_topic_map = extraction_result.get('date_topic_map', {})

    # Store in session
    session['syllabus_course_name'] = course_title
    session['syllabus_extracted_topics'] = topics
    session['syllabus_date_topic_map'] = date_topic_map

    # Capture Date Sync checkbox from form and persist in session
    date_sync_enabled = 'date_sync' in request.form
    session['date_sync_enabled'] = date_sync_enabled
    print(f"✅ Date Sync Enabled captured in upload: {date_sync_enabled}")

    # Debug Outputs
    print("Extracted Course Title:", course_title)
    print("Extracted Topics:", topics)
    print("Extracted Date Topic Map:", date_topic_map)

    return redirect(url_for('build_school_newsletter'))

# ---------------- Upload Documents ----------------
from utils.materials_parser import extract_topics_from_material

@app.route('/upload-material', methods=['POST'])
def upload_material():
    if 'user_id' not in session:
        flash("Please log in to upload materials.")
        return redirect(url_for('login'))

    f = request.files.get('material')
    if not f:
        flash("No file uploaded.")
        return redirect(url_for('build_school_newsletter'))

    data = extract_topics_from_material(f.read(), f.filename)
    course_title = data.get('course_title', '')
    topics = data.get('topics', [])
    # doc_type = data.get('doc_type', 'unknown')  # optional log

    # Use the SAME session keys your builder expects
    session['syllabus_course_name'] = course_title or ''
    session['syllabus_extracted_topics'] = topics
    session['syllabus_date_topic_map'] = {}    # none for materials
    session['date_sync_enabled'] = False

    print("✅ Material uploaded:", f.filename)
    print("Course Title (guess):", course_title)
    print("Extracted Topics:", topics[:10])

    return redirect(url_for('build_school_newsletter'))


# ---------------- User Registration ----------------
from sqlalchemy.exc import IntegrityError
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            flash("Passwords do not match.", "auth")
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        db = SessionLocal()
        try:
            # Insert user
            new_user = User(email=email, password_hash=hashed_password)
            db.add(new_user)
            db.commit()
            db.refresh(new_user)  # Get auto-generated ID

            # Auto-login
            session['user_id'] = new_user.id
            session['email'] = new_user.email
            flash("Account created and logged in successfully!", "auth")
            return redirect(url_for('dashboard'))

        except IntegrityError:
            db.rollback()
            flash("Email already registered. Please log in instead.", "auth")
            return redirect(url_for('register'))

        finally:
            db.close()

    return render_template('register.html')

# ---------------- User Login ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).first()

            if user:
                if check_password_hash(user.password_hash, password):
                    session['user_id'] = user.id
                    session['email'] = user.email
                    flash("Logged in successfully!", "auth")
                    return redirect(url_for('dashboard'))
                else:
                    flash("Incorrect password. Please try again.", "auth")
                    return redirect(url_for('login'))
            else:
                flash("No account found with that email.", "auth")
                return redirect(url_for('login'))

        finally:
            db.close()

    return render_template('login.html')

# ---------------- Logout -----------------

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for('home'))


# ---------------- Dashboard --------------
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash("Please log in to view your dashboard.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    db = SessionLocal()
    try:
        # 🧠 Fetch user and their plan
        user = db.query(User).filter(User.id == user_id).first()
        user_plan = user.plan.lower() if user and user.plan else 'free'

        # 🔵 Fetch general newsletters
        general_newsletters = (
            db.query(Newsletter)
            .filter(Newsletter.user_id == user_id)
            .order_by(Newsletter.next_send_time.desc())
            .all()
        )

        # 🟣 Fetch school newsletters
        school_newsletters = (
            db.query(SchoolNewsletter)
            .filter(SchoolNewsletter.user_id == user_id)
            .order_by(SchoolNewsletter.next_send_time.desc())
            .all()
        )

        # 🧠 Progress is now: SENT vs MAX (not total rows in emails table)
        # 1) Sent count per plan_id
        sent_rows = (
            db.query(
                Email.plan_id,
                func.sum(func.cast(Email.sent, Integer)).label("sent")
            )
            .filter(Email.user_id == user_id)
            .group_by(Email.plan_id)
            .all()
        )
        sent_by_plan = {row.plan_id: (row.sent or 0) for row in sent_rows}

        # 2) Cap comes from the specific plan object (NOT user plan).
        #    - SchoolNewsletter: use n.max_emails (can be None → Unlimited)
        #    - Newsletter (general): currently has no max_emails field → Unlimited by default

        def to_int_or_none(x):
            try:
                return int(x) if x is not None else None
            except (TypeError, ValueError):
                return None

        plan_progress = {}

        # General newsletters
        for n in general_newsletters:
            cap = getattr(n, "max_emails", None)  # Newsletter model has no max_emails, so None
            plan_progress[n.id] = {
                "cap": to_int_or_none(cap),
                "sent": sent_by_plan.get(n.id, 0)
            }

        # School newsletters
        for n in school_newsletters:
            cap = to_int_or_none(n.max_emails)
            plan_progress[n.id] = {
                "cap": cap,
                "sent": sent_by_plan.get(n.id, 0)
            }

        # 🧩 Normalize both general and school newsletters into a single unified list
        def format_general(n):
            return {
                "id": n.id,
                "plan_title": n.plan_title,
                "summary": n.summary,
                "topic": n.topic,
                "tone": n.tone,
                "frequency": n.frequency,
                "next_send_time": n.next_send_time,
                "type": "general"
            }

        import json
        def format_school(n):
            return {
                "id": n.id,
                "plan_title": n.course_name,
                "summary": n.summary,
                "topic": n.topics,
                "tone": "N/A",
                "frequency": n.frequency,
                "next_send_time": n.next_send_time,
                "type": "school",
                "content_types": json.loads(n.content_types) if n.content_types else []
            }

        newsletters_active = [format_general(n) for n in general_newsletters] + [format_school(n) for n in school_newsletters]

        # 💡 Plan limits (max_total only)
        plan_features = PLAN_FEATURES.get(user_plan, PLAN_FEATURES['free'])
        total_limit = plan_features['max_total']

        active_count = len(newsletters_active)
        total_count = active_count  # no paused section anymore

        total_limit_display = total_limit if total_limit is not None else 'Unlimited'

        # ⏳ Set min datetime for rescheduling
        min_datetime = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

        return render_template(
            'dashboard.html',
            newsletters_active=newsletters_active,
            newsletters_paused=[],  # no longer used
            total_newsletters=newsletters_active,
            min_datetime=min_datetime,
            total_limit=total_limit,
            user_plan=user_plan,
            active_count=active_count,
            total_count=total_count,
            total_limit_display=total_limit_display,
            plan_progress=plan_progress
        )

    finally:
        db.close()

#-------------- Edit ---------------------
@app.route('/edit-newsletter', methods=['GET', 'POST'])
def edit_newsletter():
    if 'user_id' not in session:
        flash("Please log in to edit a newsletter.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    plan_id = request.args.get('plan_id') or request.form.get('plan_id')
    plan_type = (request.args.get('plan_type') or request.form.get('plan_type') or '').strip().lower()

    if not plan_id or plan_type not in ('general', 'school'):
        flash("Invalid edit request.")
        return redirect(url_for('dashboard'))

    db = SessionLocal()
    try:
        # Fixed option sets we support in UI
        freq_options = ["daily", "biweekly", "weekly"]
        content_type_options = ["summary", "example", "quiz", "flashcards"]

        if plan_type == 'general':
            plan = db.query(Newsletter).filter(Newsletter.id == plan_id, Newsletter.user_id == user_id).first()
            if not plan:
                flash("Newsletter not found.")
                return redirect(url_for('dashboard'))

            if request.method == 'POST':
                plan.plan_title = request.form.get('plan_title', plan.plan_title)
                freq = request.form.get('frequency')
                if freq in freq_options:
                    plan.frequency = freq
                db.commit()
                flash("Newsletter updated.")
                return redirect(url_for('dashboard'))

            # GET → render form
            return render_template(
                'edit_newsletter.html',
                plan_type='general',
                plan_id=plan.id,
                plan_title=plan.plan_title or '',
                frequency=plan.frequency or '',
                freq_options=freq_options,
                content_type_options=[],          # not used for general
                selected_content_types=[]         # not used for general
            )

        else:
            plan = db.query(SchoolNewsletter).filter(SchoolNewsletter.id == plan_id, SchoolNewsletter.user_id == user_id).first()
            if not plan:
                flash("Study plan not found.")
                return redirect(url_for('dashboard'))

            import json
            if request.method == 'POST':
                plan.course_name = request.form.get('plan_title', plan.course_name)
                freq = request.form.get('frequency')
                if freq in freq_options:
                    plan.frequency = freq

                # Gather selected content types from checkboxes
                selected = request.form.getlist('content_types')
                selected = [c for c in selected if c in content_type_options]
                plan.content_types = json.dumps(selected)
                db.commit()
                flash("Study plan updated.")
                return redirect(url_for('dashboard'))

            # GET → render form
            try:
                selected_ct = json.loads(plan.content_types) if plan.content_types else []
                if not isinstance(selected_ct, list):
                    selected_ct = []
            except Exception:
                selected_ct = []

            return render_template(
                'edit_newsletter.html',
                plan_type='school',
                plan_id=plan.id,
                plan_title=plan.course_name or '',
                frequency=plan.frequency or '',
                freq_options=freq_options,
                content_type_options=content_type_options,
                selected_content_types=selected_ct
            )
    finally:
        db.close()


#-------------- Change Send Timer ---------
from pytz import utc

from pytz import utc

@app.route('/update-send-time', methods=['POST'])
def update_send_time():
    if 'user_id' not in session:
        flash("Please log in to update newsletter times.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    plan_id = request.form.get('plan_id')
    new_time_str = request.form.get('new_send_time')

    try:
        new_send_time = datetime.strptime(new_time_str, "%Y-%m-%dT%H:%M")
        new_send_time = new_send_time.replace(tzinfo=utc).astimezone(utc).replace(tzinfo=None)
    except ValueError:
        flash("Invalid date format.")
        return redirect('/dashboard')

    if new_send_time < datetime.now() + timedelta(days=7):
        flash("Send time must be at least one week from now.")
        return redirect('/dashboard')

    db = SessionLocal()
    try:
        # Try general newsletter first
        newsletter = db.query(Newsletter).filter(
            Newsletter.id == plan_id,
            Newsletter.user_id == user_id
        ).first()

        # If not found, try school newsletter
        if newsletter:
            frequency = newsletter.frequency.lower()
        else:
            newsletter = db.query(SchoolNewsletter).filter(
                SchoolNewsletter.id == plan_id,
                SchoolNewsletter.user_id == user_id
            ).first()
            if not newsletter:
                flash("Newsletter not found or access denied.")
                return redirect('/dashboard')
            frequency = newsletter.frequency.lower()

        delta = {
            "daily": timedelta(days=1),
            "bidaily": timedelta(days=2),
            "weekly": timedelta(weeks=1),
        }.get(frequency, timedelta(weeks=1))

        # Update next send time
        newsletter.next_send_time = new_send_time
        db.commit()

        # Update future unsent emails
        unsent_emails = db.query(Email).filter(
            Email.plan_id == plan_id,
            Email.sent == False
        ).order_by(asc(Email.position_in_plan)).all()

        for i, email in enumerate(unsent_emails):
            email.send_date = new_send_time + i * delta

        db.commit()
        flash("Send time updated successfully.", "rescheduled")
        return redirect('/dashboard')

    finally:
        db.close()



# ----------- Delete Newsletter -----------
@app.route('/delete-newsletter', methods=['POST'])
def delete_newsletter():
    if 'user_id' not in session:
        flash("Please log in to continue.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    plan_id = request.form.get('plan_id')
    plan_type = request.form.get('plan_type')  # added to distinguish between general and school

    db = SessionLocal()
    try:
        # Determine which table to query from
        if plan_type == "school":
            newsletter = db.query(SchoolNewsletter).filter(
                SchoolNewsletter.id == plan_id,
                SchoolNewsletter.user_id == user_id
            ).first()
        elif plan_type == "general":
            newsletter = db.query(Newsletter).filter(
                Newsletter.id == plan_id,
                Newsletter.user_id == user_id
            ).first()
        else:
            flash("Invalid newsletter type.")
            return redirect(url_for('dashboard'))

        if not newsletter:
            flash("Invalid or unauthorized newsletter plan.")
            return redirect(url_for('dashboard'))

        # Delete the newsletter plan
        db.delete(newsletter)

        # Delete only unsent emails
        db.query(Email).filter(
            Email.plan_id == plan_id,
            Email.sent == False
        ).delete()

        db.commit()
        flash("Newsletter plan deleted.", "deleted")
        return redirect(url_for('dashboard'))

    finally:
        db.close()


#----------------- Account settings -------
@app.route('/account-settings', methods=['GET', 'POST'])
def account_settings():
    if 'user_id' not in session:
        flash("Please log in to view account settings.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    email = session['email']
    mode = request.args.get('mode')

    db = SessionLocal()
    try:
        if request.method == 'POST':
            current_password = request.form['current_password']
            new_password = request.form['new_password']
            confirm_password = request.form['confirm_password']

            if new_password != confirm_password:
                flash("New passwords do not match.")
                return redirect(url_for('account_settings', mode='edit'))

            user = db.query(User).filter(User.id == user_id).first()

            if not user or not check_password_hash(user.password_hash, current_password):
                flash("Incorrect current password.")
                return redirect(url_for('account_settings', mode='edit'))

            user.password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
            db.commit()

            flash("Password updated successfully.")
            return redirect(url_for('account_settings'))

        # GET: Fetch subscription details
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            flash("User not found.")
            return redirect(url_for('login'))

        current_plan = user.plan
        raw_date = user.subscription_end_date

        from datetime import datetime
        try:
            parsed = datetime.fromisoformat(raw_date)
            next_billing_date = parsed.strftime("%B %d, %Y")  # e.g. July 07, 2025
        except Exception as e:
            print("⚠️ Failed to format billing date:", e)
            next_billing_date = raw_date or "Unknown"

        plan_costs = {
            'plus': '$5/month',
            'pro': '$15/month'
        }
        plan_cost = plan_costs.get(current_plan)

        return render_template(
            'account_settings.html',
            email=email,
            user_id=user_id,
            edit_mode=(mode == 'edit'),
            current_plan=current_plan,
            plan_cost=plan_cost,
            next_billing_date=next_billing_date
        )

    finally:
        db.close()
@app.route('/stripe-portal')
def stripe_portal():
    if 'user_id' not in session:
        flash("Please log in to access billing.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.stripe_customer_id:
            flash("Stripe billing info not found.")
            return redirect(url_for('account_settings'))

        session_obj = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id,
            return_url=url_for('account_settings', _external=True)
        )
        return redirect(session_obj.url)

    except Exception as e:
        print("⚠️ Stripe error:", e)
        flash("Unable to open billing portal.")
        return redirect(url_for('account_settings'))

    finally:
        db.close()


#--------------- Delete Account -----------
@app.route('/delete-account', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        flash("Please log in first.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    entered_password = request.form['confirm_password']

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()

        # Verify password
        if not user or not check_password_hash(user.password_hash, entered_password):
            flash("Incorrect password.")
            return redirect(url_for('account_settings'))

        # Delete associated data
        db.query(Email).filter(Email.user_id == user_id).delete()
        db.query(Newsletter).filter(Newsletter.user_id == user_id).delete()
        db.delete(user)

        db.commit()
        session.clear()

        flash("Your account and all associated data have been deleted.")
        return redirect(url_for('home'))

    finally:
        db.close()

#-------------- Open Privacy and Terms ----

@app.route('/privacy')
def privacy_policy():
    return render_template('privacy.html')

@app.route('/terms')
def terms_and_conditions():
    return render_template('terms.html')


#------------ Pricing --------------------
@app.route("/pricing")
def pricing():
    current_plan = 'free'
    downgrade_to = None
    subscription_days_left = None
    current_plan_rank = 0

    if 'user_id' in session:
        user_id = session['user_id']
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()

            if user:
                current_plan = user.plan or 'free'
                downgrade_to = user.downgrade_to
                subscription_end_date = user.subscription_end_date

                # Calculate days left until downgrade
                if subscription_end_date:
                    try:
                        dt = datetime.fromisoformat(subscription_end_date)
                        delta = (dt - datetime.utcnow()).total_seconds() / 86400
                        subscription_days_left = max(ceil(delta), 0)
                    except Exception as e:
                        print("⚠️ Error parsing subscription_end_date:", e)

            plan_order = {'free': 0, 'plus': 1, 'pro': 2}
            current_plan_rank = plan_order.get(current_plan, 0)

        finally:
            db.close()

    return render_template(
        "pricing.html",
        current_plan=current_plan,
        current_plan_rank=current_plan_rank,
        downgrade_to=downgrade_to,
        subscription_days_left=subscription_days_left
    )


#-------------- Checkout ----------------
@app.route('/create-checkout-session/<plan>')
def create_checkout_session(plan):
    if 'user_id' not in session:
        flash("Please log in to upgrade.")
        return redirect(url_for('login'))

    if plan not in STRIPE_PRICES:
        flash("Invalid plan.")
        return redirect(url_for('pricing'))

    user_id = session['user_id']
    user_email = get_user_email(user_id)

    db = SessionLocal()
    try:
        # ✅ Get or create Stripe customer ID
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            flash("User not found.")
            return redirect(url_for('pricing'))

        if not user.stripe_customer_id:
            customer = stripe.Customer.create(email=user_email)
            user.stripe_customer_id = customer.id
            db.commit()

        # ✅ Create Stripe Checkout Session for a subscription
        checkout_session = stripe.checkout.Session.create(
            mode='subscription',
            customer=user.stripe_customer_id,
            line_items=[{
                'price': STRIPE_PRICES[plan],
                'quantity': 1,
            }],
            success_url=url_for('dashboard', _external=True),
            cancel_url=url_for('pricing', _external=True),
            allow_promotion_codes=True,
            metadata={
                'user_id': str(user_id),
                'new_plan': plan
            }
        )
        # 303 to avoid form resubmission issues
        return redirect(checkout_session.url, code=303)

    except Exception as e:
        print("⚠️ Stripe Checkout error:", e)
        flash("Unable to start checkout. Please try again.")
        return redirect(url_for('pricing'))

    finally:
        db.close()

#---------------- Webhook -------------


@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('stripe-signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
        import json
        print("📦 Full event payload:")
        print(json.dumps(event, indent=2))
        print(f"🔎 Event type received: {event.get('type')}")

    except ValueError as e:
        print("❌ Invalid payload:", e)
        return 'Bad Request', 400
    except stripe.error.SignatureVerificationError as e:
        print("❌ Invalid signature:", e)
        return 'Unauthorized', 400
    except Exception as e:
        print("❌ General webhook error (construction):", e)
        return 'Error', 400

    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        cursor = conn.cursor()

        if event['type'] == 'checkout.session.completed':
            from datetime import datetime
            session_data = event['data']['object']
            user_id = session_data.get('metadata', {}).get('user_id')
            new_plan = session_data.get('metadata', {}).get('new_plan')
            subscription_id = session_data.get('subscription')
            customer_id = session_data.get('customer')

            if not user_id or not new_plan:
                print(f"⚠️ Missing metadata: user_id={user_id}, new_plan={new_plan}")
                return '', 200

            subscription_obj = stripe.Subscription.retrieve(subscription_id)

            # ⚙️ Compute ends_at with fallbacks for new API versions
            ends_at = subscription_obj.get('current_period_end')
            if not ends_at:
                try:
                    items = (subscription_obj.get('items') or {}).get('data') or []
                    ends_at = items[0].get('current_period_end') if items else None
                    source = 'item.current_period_end'
                except Exception:
                    ends_at = None
                    source = 'none'
            else:
                source = 'subscription.current_period_end'

            subscription_end_date = datetime.utcfromtimestamp(ends_at).isoformat() if ends_at else None
            print(f"🧭 checkout.session.completed: ends_at={ends_at} source={source} sub={subscription_id}")

            cursor.execute("""
                UPDATE users 
                SET plan = %s, subscription_id = %s, stripe_customer_id = %s, subscription_end_date = %s, downgrade_to = NULL
                WHERE id = %s
            """, (new_plan, subscription_id, customer_id, subscription_end_date, int(user_id)))
            updated = cursor.rowcount
            conn.commit()

            print(f"✅ checkout.session.completed persisted for user {user_id} (rows={updated})")
            print(f"📅 Next Billing Date (UTC): {subscription_end_date}")



        elif event['type'] == 'customer.subscription.updated':
            from datetime import datetime
            subscription = event['data']['object']
            stripe_customer_id = subscription.get('customer')
            cancel_at_end = subscription.get("cancel_at_period_end")
            subscription_id = subscription.get('id')

            if not subscription_id:
                print("⚠️ Missing subscription.id in subscription.updated")
                return '', 200

            # There is no email in this event. If needed, we can retrieve the customer to get it.
            customer_email = None
            try:
                if stripe_customer_id:
                    cust = stripe.Customer.retrieve(stripe_customer_id)
                    customer_email = cust.get('email')
            except Exception as _e:
                pass

            ends_at = subscription.get('current_period_end')
            source = 'subscription.current_period_end'
            if not ends_at:
                try:
                    items = (subscription.get('items') or {}).get('data') or []
                    ends_at = items[0].get('current_period_end') if items else None
                    source = 'item.current_period_end'
                except Exception:
                    ends_at = None
                    source = 'none'

            subscription_end_date = datetime.utcfromtimestamp(ends_at).isoformat() if ends_at else None
            print(f"🧭 subscription.updated: ends_at={ends_at} source={source} sub={subscription_id} cancel_at_end={cancel_at_end}")

            # Update end date using the best key we have
            rows_updated = 0
            if stripe_customer_id:
                cursor.execute("""
                    UPDATE users
                    SET subscription_end_date = %s
                    WHERE stripe_customer_id = %s
                """, (subscription_end_date, stripe_customer_id))
                rows_updated = cursor.rowcount

            if rows_updated == 0:
                cursor.execute("""
                    UPDATE users
                    SET subscription_end_date = %s
                    WHERE subscription_id = %s
                """, (subscription_end_date, subscription_id))
                rows_updated = cursor.rowcount

            # 🔑 Final fallback: try matching by email if no match yet
            if rows_updated == 0:
                customer_email = invoice.get('customer_email') or ((invoice.get('customer_details') or {}).get('email'))
                if customer_email:
                    cursor.execute("""
                        UPDATE users
                        SET subscription_end_date = %s
                        WHERE email = %s
                    """, (subscription_end_date, customer_email))
                    rows_updated = cursor.rowcount

                    # If matched, backfill stripe_customer_id and subscription_id for future events
                    if rows_updated > 0:
                        if stripe_customer_id:
                            cursor.execute("""
                                UPDATE users
                                SET stripe_customer_id = %s
                                WHERE email = %s AND (stripe_customer_id IS NULL OR stripe_customer_id = '')
                            """, (stripe_customer_id, customer_email))
                        if subscription_id:
                            cursor.execute("""
                                UPDATE users
                                SET subscription_id = %s
                                WHERE email = %s AND (subscription_id IS NULL OR subscription_id = '')
                            """, (subscription_id, customer_email))
                        print(f"🧷 Fallback matched by email={customer_email} and backfilled IDs.")

            conn.commit()
            print(f"✅ invoice.* updated end_date={subscription_end_date} rows={rows_updated} (cust={stripe_customer_id}, sub={subscription_id})")

            # Find user_id for downgrade_to update
            cursor.execute("SELECT id FROM users WHERE stripe_customer_id = %s", (stripe_customer_id,))
            row = cursor.fetchone()
            if not row:
                cursor.execute("SELECT id FROM users WHERE subscription_id = %s", (subscription_id,))
                row = cursor.fetchone()
            if not row and customer_email:
                cursor.execute("SELECT id FROM users WHERE email = %s", (customer_email,))
                row = cursor.fetchone()
                if row:
                    print(f"🧷 Found user by email={customer_email}")

            if not row:
                print(f"⚠️ No user found for cust={stripe_customer_id} or sub={subscription_id} (and no email match)")
                return '', 200

            user_id = row['id']

            if cancel_at_end:
                cursor.execute("UPDATE users SET downgrade_to = %s WHERE id = %s", ('free', user_id))
                print(f"📌 Pending downgrade saved for user {user_id} (cancel_at_period_end=True)")
            else:
                cursor.execute("UPDATE users SET downgrade_to = NULL WHERE id = %s", (user_id,))
                print(f"✅ Cleared pending downgrade for user {user_id} (cancel_at_period_end=False)")

            conn.commit()



        elif event['type'] == 'customer.subscription.created':
            from datetime import datetime
            subscription = event['data']['object']
            stripe_customer_id = subscription.get('customer')
            subscription_id = subscription.get('id')

            # ⚙️ Compute ends_at with fallbacks
            ends_at = subscription.get('current_period_end')
            source = 'subscription.current_period_end'
            if not ends_at:
                try:
                    items = (subscription.get('items') or {}).get('data') or []
                    ends_at = items[0].get('current_period_end') if items else None
                    source = 'item.current_period_end'
                except Exception:
                    ends_at = None
                    source = 'none'

            subscription_end_date = datetime.utcfromtimestamp(ends_at).isoformat() if ends_at else None
            print(f"🧭 subscription.created: ends_at={ends_at} source={source} sub={subscription_id}")

            # Update by customer first, then subscription_id if needed
            rows_updated = 0
            if stripe_customer_id:
                cursor.execute("""
                    UPDATE users
                    SET subscription_end_date = %s
                    WHERE stripe_customer_id = %s
                """, (subscription_end_date, stripe_customer_id))
                rows_updated = cursor.rowcount

            if rows_updated == 0 and subscription_id:
                cursor.execute("""
                    UPDATE users
                    SET subscription_end_date = %s
                    WHERE subscription_id = %s
                """, (subscription_end_date, subscription_id))
                rows_updated = cursor.rowcount

            conn.commit()
            print(f"✅ subscription.created persisted end_date={subscription_end_date} rows={rows_updated} (cust={stripe_customer_id}, sub={subscription_id})")



        elif event['type'] in ('invoice.payment_succeeded', 'invoice.paid'):
            # Deep debug for renewal → refresh subscription_end_date
            from datetime import datetime

            invoice = event['data']['object']

            # --- Debug: raw keys we rely on ---
            try:
                print("🧾 invoice.id:", invoice.get('id'))
                print("🧾 invoice.customer:", invoice.get('customer'))
                print("🧾 invoice.customer_email:", invoice.get('customer_email'))
                print("🧾 invoice.customer_details.email:", (invoice.get('customer_details') or {}).get('email'))
                print("🧾 invoice.subscription (top-level):", invoice.get('subscription'))
                _parent = invoice.get('parent') or {}
                _sub_details = (_parent.get('subscription_details') or {})
                print("🧾 invoice.parent.subscription_details.subscription:", _sub_details.get('subscription'))

                # Also show the first line’s subscription path (newer API shapes)
                lines = (invoice.get('lines') or {}).get('data') or []
                if lines:
                    parent0 = (lines[0].get('parent') or {})
                    sid = (parent0.get('subscription_item_details') or {}).get('subscription')
                    print("🧾 invoice.lines[0].parent.subscription_item_details.subscription:", sid)
                else:
                    print("🧾 invoice.lines: <empty>")
            except Exception as e:
                print("⚠️ Debug print (invoice keys) failed:", e)

            stripe_customer_id = invoice.get('customer')
            customer_email = invoice.get('customer_email') or ((invoice.get('customer_details') or {}).get('email'))

            # Robust subscription id extraction
            subscription_id = (
                invoice.get('subscription')
                or ((invoice.get('parent') or {}).get('subscription_details') or {}).get('subscription')
            )
            if not subscription_id:
                try:
                    lines = (invoice.get('lines') or {}).get('data') or []
                    if lines:
                        parent = (lines[0].get('parent') or {})
                        sid_details = parent.get('subscription_item_details') or {}
                        subscription_id = sid_details.get('subscription')
                except Exception:
                    subscription_id = None

            print("🔎 Extracted subscription_id:", subscription_id)

            if not subscription_id:
                print("⚠️ invoice.* event missing subscription id; cannot update end date.")
            else:
                try:
                    # Pull subscription to get current_period_end
                    sub = stripe.Subscription.retrieve(subscription_id)

                    # Prefer subscription.current_period_end; fall back to item.current_period_end
                    ends_at = sub.get('current_period_end')
                    source = 'subscription.current_period_end'
                    if not ends_at:
                        try:
                            items = (sub.get('items') or {}).get('data') or []
                            ends_at = items[0].get('current_period_end') if items else None
                            source = 'item.current_period_end'
                        except Exception:
                            ends_at = None
                            source = 'none'

                    subscription_end_date = datetime.utcfromtimestamp(ends_at).isoformat() if ends_at else None
                    print(f"🧭 invoice renewal: ends_at={ends_at} source={source} sub={subscription_id}")
                    print(f"🧭 computed subscription_end_date (UTC ISO): {subscription_end_date}")

                    # --- Debug: show what rows exist BEFORE updates ---
                    def _dbg_select(label, sql, params):
                        try:
                            cursor.execute(sql, params)
                            rows = cursor.fetchall()
                            print(f"🔍 {label} → {len(rows)} row(s)")
                            for r in rows:
                                print("   •", dict(r) if isinstance(r, dict) else r)
                        except Exception as e:
                            print(f"⚠️ Debug select failed for {label}:", e)

                    _dbg_select(
                        "BEFORE: by stripe_customer_id",
                        "SELECT id, email, plan, stripe_customer_id, subscription_id, subscription_end_date FROM users WHERE stripe_customer_id = %s",
                        (stripe_customer_id,)
                    )
                    _dbg_select(
                        "BEFORE: by subscription_id",
                        "SELECT id, email, plan, stripe_customer_id, subscription_id, subscription_end_date FROM users WHERE subscription_id = %s",
                        (subscription_id,)
                    )
                    if customer_email:
                        _dbg_select(
                            "BEFORE: by email",
                            "SELECT id, email, plan, stripe_customer_id, subscription_id, subscription_end_date FROM users WHERE email = %s",
                            (customer_email,)
                        )

                    # --- Perform updates with per-step rowcount logging ---
                    total_updated = 0

                    if stripe_customer_id:
                        cursor.execute("""
                            UPDATE users
                            SET subscription_end_date = %s
                            WHERE stripe_customer_id = %s
                        """, (subscription_end_date, stripe_customer_id))
                        print("🧱 UPDATE by stripe_customer_id rowcount:", cursor.rowcount)
                        total_updated += cursor.rowcount

                    if total_updated == 0:
                        cursor.execute("""
                            UPDATE users
                            SET subscription_end_date = %s
                            WHERE subscription_id = %s
                        """, (subscription_end_date, subscription_id))
                        print("🧱 UPDATE by subscription_id rowcount:", cursor.rowcount)
                        total_updated += cursor.rowcount

                    if total_updated == 0 and customer_email:
                        cursor.execute("""
                            UPDATE users
                            SET subscription_end_date = %s
                            WHERE email = %s
                        """, (subscription_end_date, customer_email))
                        print("🧱 UPDATE by email rowcount:", cursor.rowcount)
                        if cursor.rowcount > 0:
                            # Backfill IDs if missing to avoid future misses
                            if stripe_customer_id:
                                cursor.execute("""
                                    UPDATE users
                                    SET stripe_customer_id = %s
                                    WHERE email = %s AND (stripe_customer_id IS NULL OR stripe_customer_id = '')
                                """, (stripe_customer_id, customer_email))
                                print("🧱 Backfill stripe_customer_id rowcount:", cursor.rowcount)
                            if subscription_id:
                                cursor.execute("""
                                    UPDATE users
                                    SET subscription_id = %s
                                    WHERE email = %s AND (subscription_id IS NULL OR subscription_id = '')
                                """, (subscription_id, customer_email))
                                print("🧱 Backfill subscription_id rowcount:", cursor.rowcount)
                            total_updated += 1  # mark as success

                    conn.commit()
                    print(f"✅ invoice.* updated end_date={subscription_end_date} total_updated_flag={total_updated}")

                    # --- Debug: show rows AFTER updates ---
                    _dbg_select(
                        "AFTER: by stripe_customer_id",
                        "SELECT id, email, plan, stripe_customer_id, subscription_id, subscription_end_date FROM users WHERE stripe_customer_id = %s",
                        (stripe_customer_id,)
                    )
                    _dbg_select(
                        "AFTER: by subscription_id",
                        "SELECT id, email, plan, stripe_customer_id, subscription_id, subscription_end_date FROM users WHERE subscription_id = %s",
                        (subscription_id,)
                    )
                    if customer_email:
                        _dbg_select(
                            "AFTER: by email",
                            "SELECT id, email, plan, stripe_customer_id, subscription_id, subscription_end_date FROM users WHERE email = %s",
                            (customer_email,)
                        )

                    # If still nothing updated, log a loud warning with the identifiers we tried
                    if total_updated == 0:
                        print("🚨 No user row was updated on invoice.*. Tried keys:",
                              {"stripe_customer_id": stripe_customer_id, "subscription_id": subscription_id, "email": customer_email})

                except Exception as e:
                    print(f"❌ Failed to refresh end_date on invoice.* for sub={subscription_id}: {e}")


        elif event['type'] == 'customer.subscription.deleted':
            from datetime import datetime
            subscription = event['data']['object']
            stripe_customer_id = subscription.get('customer')
            ends_at = subscription.get("current_period_end")

            subscription_end_date = (
                datetime.utcfromtimestamp(ends_at).isoformat()
                if ends_at else datetime.utcnow().isoformat()
            )

            if not stripe_customer_id:
                print(f"⚠️ Missing customer in deleted event.")
                return '', 200

            cursor.execute("SELECT id, downgrade_to FROM users WHERE stripe_customer_id = %s", (stripe_customer_id,))
            row = cursor.fetchone()

            if not row:
                print(f"⚠️ No user found for Stripe customer {stripe_customer_id}")
                return '', 200

            user_id, downgrade_to = row['id'], row['downgrade_to']
            new_plan = downgrade_to if downgrade_to else 'free'

            cursor.execute("""
                UPDATE users 
                SET plan = %s, subscription_id = NULL, subscription_end_date = %s, downgrade_to = NULL
                WHERE id = %s
            """, (new_plan, subscription_end_date, user_id))
            updated = cursor.rowcount

            # 🔥 Only enforce max_total now
            limits = PLAN_FEATURES.get(new_plan, {'max_total': 1})

            cursor.execute("""
                SELECT id 
                FROM newsletters 
                WHERE user_id = %s 
                ORDER BY next_send_time ASC
            """, (user_id,))
            newsletters = cursor.fetchall()

            total_count = len(newsletters)
            if limits['max_total'] is not None and total_count > limits['max_total']:
                excess = total_count - limits['max_total']
                ids_to_delete = [n['id'] for n in newsletters][:excess]
                cursor.executemany("""
                    DELETE FROM newsletters 
                    WHERE id = %s AND user_id = %s
                """, [(nid, user_id) for nid in ids_to_delete])
                cursor.executemany("""
                    DELETE FROM emails 
                    WHERE plan_id = %s
                """, [(nid,) for nid in ids_to_delete])
                print(f"🗑️ Deleted {len(ids_to_delete)} newsletters due to total limit.")

            conn.commit()
            print(f"✅ Downgraded user {user_id} to {new_plan} after subscription end. (rows={updated})")
            print(f"📅 Downgrade applied from webhook on {subscription_end_date}")

        cursor.close()
        conn.close()

    except Exception as e:
        print("❌ Webhook handler error:", e)

    return '', 200

#-------------- Reviews ------------------

@app.route('/reviews')
def reviews():
    # Require login
    if "user_id" not in session:
        flash("Please log in to view reviews.")
        return redirect(url_for("login"))

    conn = pg_connect()  # uses the ONE global pg_connect defined earlier
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM reviews ORDER BY id DESC')
    reviews = cursor.fetchall()

    cursor.close()
    conn.close()
    return render_template('reviews.html', reviews=reviews)


@app.route('/reviews/new')
def new_review():
    return render_template('leave_review.html')


@app.route('/reviews/new', methods=['POST'])
def submit_review():
    name = request.form.get('name')
    stars = request.form.get('stars')
    comment = request.form.get('comment', '')

    if not name or not stars:
        return "Missing name or star rating", 400

    conn = pg_connect(dict_cursor=False)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO reviews (name, stars, comment) VALUES (%s, %s, %s)',
        (name, int(stars), comment)
    )
    conn.commit()
    cursor.close()
    conn.close()

    return redirect('/reviews')


# ---------------- Run App ----------------

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
