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

from scheduler import scheduler
scheduler.start()


# Plan-based limits
PLAN_FEATURES = {
    'free': {'max_total': 1, 'max_active': 1},
    'plus': {'max_total': None, 'max_active': 1},
    'pro': {'max_total': None, 'max_active': None},
}

# Pricing Keys
STRIPE_PRICES = {
    "plus": "price_1RSVe72MKajKZrXPrsuvcvnO",
    "pro": "price_1RSVeL2MKajKZrXP4yIxSw58"
}

from db import SessionLocal
from models import User

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

        days_left = None
        if subscription_end_date and subscription_end_date.strip():
            from datetime import datetime, date
            try:
                end_date = datetime.fromisoformat(subscription_end_date)
                days_left = (end_date.date() - date.today()).days
                if days_left < 0:
                    days_left = None
            except Exception as e:
                print(f"⚠️ Invalid subscription_end_date format: {subscription_end_date}")

        return {
            'subscription_days_left': days_left,
            'downgrade_to': downgrade_to
        }

    except Exception as e:
        print("❌ Error injecting subscription context:", e)
        return {}
    finally:
        db.close()


# ---------------- Home ----------------
@app.route('/')
def home():
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
        return render_template('index.html', reviews=reviews)
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
        limits = PLAN_FEATURES.get(plan, {'max_total': 1, 'max_active': 1})

        total_count = db.query(Newsletter).filter(Newsletter.user_id == user_id).count()

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

    # ✅ Plan-based activation decision
    user = db.query(User).filter(User.id == user_id).first()
    plan = user.plan.lower() if user and user.plan else 'free'
    limits = PLAN_FEATURES.get(plan, {'max_active': 1})

    active_count = db.query(func.count()).select_from(Newsletter).filter(
        Newsletter.user_id == user_id,
        Newsletter.is_active == True
    ).scalar()

    is_active = 1 if limits['max_active'] is None or active_count < limits['max_active'] else 0

    print("📊 Plan:", plan)
    print("📊 Active Count:", active_count)
    print("📌 Final is_active value:", is_active)

    # ✅ Insert newsletter
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
        is_active=is_active
    )
    db.add(new_newsletter)
    db.commit()

    new_newsletter.plan_id = new_newsletter.id
    db.commit()

    print("🧾 Inserted Newsletter ID:", new_newsletter.id, "| is_active =", new_newsletter.is_active)

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
        # 🔵 Active newsletters
        newsletters_active = (
            db.query(Newsletter)
            .filter(Newsletter.user_id == user_id, Newsletter.is_active == True)
            .order_by(Newsletter.next_send_time.desc())
            .all()
        )

        # 🟡 Paused newsletters
        newsletters_paused = (
            db.query(Newsletter)
            .filter(Newsletter.user_id == user_id, Newsletter.is_active == False)
            .order_by(Newsletter.next_send_time.desc())
            .all()
        )

        # 🧠 Fetch user's plan
        user = db.query(User).filter(User.id == user_id).first()
        user_plan = user.plan.lower() if user and user.plan else 'free'

        # 🧠 Get progress per plan (sent vs total)
        email_rows = (
            db.query(Email.plan_id)
            .filter(Email.user_id == user_id)
            .with_entities(
                Email.plan_id,
                func.count().label("total"),
                func.sum(func.cast(Email.sent, Integer)).label("sent")
            )
            .group_by(Email.plan_id)
            .all()
        )
        plan_progress = {
            row.plan_id: {"total": row.total, "sent": row.sent or 0}
            for row in email_rows
        }

        # 💡 Limits
        plan_features = PLAN_FEATURES.get(user_plan, PLAN_FEATURES['free'])
        active_limit = plan_features['max_active']
        total_limit = plan_features['max_total']

        total_newsletters = newsletters_active + newsletters_paused

        # ⏳ Reschedule boundary
        min_datetime = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

        active_count = len(newsletters_active)
        total_count = len(total_newsletters)

        active_limit_display = active_limit if active_limit is not None else 'Unlimited'
        total_limit_display = total_limit if total_limit is not None else 'Unlimited'

        return render_template(
            'dashboard.html',
            newsletters_active=newsletters_active,
            newsletters_paused=newsletters_paused,
            total_newsletters=total_newsletters,
            min_datetime=min_datetime,
            active_limit=active_limit,
            total_limit=total_limit,
            user_plan=user_plan,
            active_count=active_count,
            total_count=total_count,
            active_limit_display=active_limit_display,
            total_limit_display=total_limit_display,
            plan_progress=plan_progress
        )

    finally:
        db.close()


#-------------- Change Send Timer ---------
from pytz import utc

@app.route('/update-send-time', methods=['POST'])
def update_send_time():
    if 'user_id' not in session:
        flash("Please log in to update newsletter times.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    plan_id = request.form.get('plan_id')
    new_time_str = request.form.get('new_send_time')

    # Parse datetime-local input
    try:
        new_send_time = datetime.strptime(new_time_str, "%Y-%m-%dT%H:%M")
        new_send_time = new_send_time.replace(tzinfo=utc).astimezone(utc).replace(tzinfo=None)
    except ValueError:
        flash("Invalid date format.")
        return redirect('/dashboard')

    # Enforce minimum of 7 days in future
    if new_send_time < datetime.now() + timedelta(days=7):
        flash("Send time must be at least one week from now.")
        return redirect('/dashboard')

    db = SessionLocal()
    try:
        # Check ownership and get frequency
        newsletter = db.query(Newsletter).filter(
            Newsletter.id == plan_id,
            Newsletter.user_id == user_id
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

        # Update newsletter next_send_time
        newsletter.next_send_time = new_send_time
        db.commit()

        # Get future unsent emails
        unsent_emails = db.query(Email).filter(
            Email.plan_id == plan_id,
            Email.sent == False
        ).order_by(asc(Email.position_in_plan)).all()

        # Update send dates
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

    db = SessionLocal()
    try:
        # Verify the plan belongs to the user
        newsletter = db.query(Newsletter).filter(
            Newsletter.id == plan_id,
            Newsletter.user_id == user_id
        ).first()

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


#--------- Toggle Newsletter --------------
@app.route('/toggle-newsletter-status', methods=['POST'])
def toggle_newsletter_status():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    newsletter_id = request.form.get('newsletter_id')

    db = SessionLocal()
    try:
        # Get current status
        newsletter = db.query(Newsletter).filter(
            Newsletter.id == newsletter_id,
            Newsletter.user_id == user_id
        ).first()

        if not newsletter:
            flash("Newsletter not found.")
            return redirect(url_for('dashboard'))

        current_status = newsletter.is_active

        # Check plan limits if activating
        if current_status == False:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                flash("Could not verify your plan.")
                return redirect(url_for('dashboard'))

            plan = user.plan
            limits = PLAN_FEATURES.get(plan, {'max_active': 1})

            active_count = db.query(Newsletter).filter(
                Newsletter.user_id == user_id,
                Newsletter.is_active == True
            ).count()

            if limits['max_active'] is not None and active_count >= limits['max_active']:
                flash("You’ve reached your active newsletter limit. Pause one to activate another.")
                return redirect(url_for('dashboard'))

        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        freq_map = {'daily': 1, 'bidaily': 2, 'weekly': 7}
        frequency = (newsletter.frequency or "weekly").lower()
        interval_days = freq_map.get(frequency, 7)

        if current_status == True:
            # PAUSING: is_active = False, clear future unsent emails and next_send_time
            newsletter.is_active = False
            newsletter.next_send_time = None

            db.query(Email).filter(
                Email.plan_id == newsletter_id,
                Email.user_id == user_id,
                Email.sent == False
            ).update({Email.send_date: None})
        else:
            # RESUMING
            newsletter.is_active = True

            # Only reassign if all send dates were cleared
            first_unsent = db.query(Email).filter(
                Email.plan_id == newsletter_id,
                Email.user_id == user_id,
                Email.sent == False
            ).order_by(Email.position_in_plan.asc()).first()

            if not first_unsent or first_unsent.send_date is None:
                # Apply a 1-day grace buffer before resuming
                resume_anchor = now + timedelta(days=1)
                newsletter.next_send_time = resume_anchor

                unsent_emails = db.query(Email).filter(
                    Email.plan_id == newsletter_id,
                    Email.user_id == user_id,
                    Email.sent == False
                ).order_by(Email.position_in_plan.asc()).all()

                for i, email in enumerate(unsent_emails):
                    email.send_date = resume_anchor + timedelta(days=interval_days * i)

        db.commit()
        flash("Newsletter status updated.")
        return redirect(url_for('dashboard'))

    finally:
        db.close()

#---------- Deactivate Newsletter ---------
@app.route('/deactivate-newsletter', methods=['POST'])
def deactivate_newsletter():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    newsletter_id = request.form.get('newsletter_id')

    db = SessionLocal()
    try:
        newsletter = db.query(Newsletter).filter(
            Newsletter.id == newsletter_id,
            Newsletter.user_id == user_id
        ).first()

        if newsletter:
            newsletter.is_active = False
            db.commit()

        flash("Newsletter deactivated successfully.", "rescheduled")
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
    user_email = get_user_email(user_id)  # ✅ Already converted to use SQLAlchemy earlier

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

        # ✅ Create Stripe checkout session
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='subscription',
            customer=user.stripe_customer_id,
            line_items=[{
                'price': STRIPE_PRICES[plan],
                'quantity': 1
            }],
            allow_promotion_codes=True,
            success_url=url_for('dashboard', _external=True),
            cancel_url=url_for('pricing', _external=True),
            metadata={
                'user_id': user_id,
                'new_plan': plan
            }
        )
        return redirect(checkout_session.url)

    except Exception as e:
        print("Stripe Checkout Error:", e)
        flash("Something went wrong starting your payment.")
        return redirect(url_for('pricing'))
    finally:
        db.close()

#---------------- Webhook -------------
import psycopg2
from psycopg2.extras import RealDictCursor

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

        # ✅ New subscription via checkout
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
            items_data = subscription_obj.get('items', {}).get('data', [])
            ends_at = items_data[0].get('current_period_end') if items_data else None
            subscription_end_date = datetime.utcfromtimestamp(ends_at).isoformat() if ends_at else None

            cursor.execute("""
                UPDATE users 
                SET plan = %s, subscription_id = %s, stripe_customer_id = %s, subscription_end_date = %s, downgrade_to = NULL
                WHERE id = %s
            """, (new_plan, subscription_id, customer_id, subscription_end_date, int(user_id)))
            conn.commit()

            print(f"✅ Plan updated: user {user_id} → {new_plan}")
            print(f"📅 Next Billing Date: {subscription_end_date}")

        elif event['type'] == 'customer.subscription.updated':
            from datetime import datetime
            subscription = event['data']['object']
            stripe_customer_id = subscription.get('customer')
            cancel_at_end = subscription.get("cancel_at_period_end")
            current_price_id = subscription['items']['data'][0]['price']['id']

            if not stripe_customer_id:
                print("⚠️ Missing Stripe customer ID in subscription.updated")
                return '', 200

            items_data = subscription.get('items', {}).get('data', [])
            ends_at = items_data[0].get("current_period_end") if items_data else None
            subscription_end_date = datetime.utcfromtimestamp(ends_at).isoformat() if ends_at else None

            cursor.execute("""
                UPDATE users 
                SET subscription_end_date = %s
                WHERE stripe_customer_id = %s
            """, (subscription_end_date, stripe_customer_id))
            conn.commit()

            print(f"📅 Subscription end date updated to {subscription_end_date} for customer {stripe_customer_id}")

            if cancel_at_end:
                price_map = {
                    "price_1RSVeL2MKajKZrXP4yIxSw58": "pro",
                    "price_1RSVe72MKajKZrXPrsuvcvnO": "plus"
                }
                current_plan_name = price_map.get(current_price_id, "unknown")
                downgrade_to = "free"

                cursor.execute("SELECT id FROM users WHERE stripe_customer_id = %s", (stripe_customer_id,))
                row = cursor.fetchone()

                if not row:
                    print(f"⚠️ No user found with customer_id={stripe_customer_id}")
                    return '', 200

                user_id = row['id']
                cursor.execute("UPDATE users SET downgrade_to = %s WHERE id = %s", (downgrade_to, user_id))
                conn.commit()

                print(f"📌 Saved downgrade_to='{downgrade_to}' for user {user_id} (currently on {current_plan_name}, cancel_at_period_end=True)")

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

            if ends_at is None:
                print(f"⚠️ Ends_at was missing, using current UTC time instead → {subscription_end_date}")

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

            limits = PLAN_FEATURES.get(new_plan, {'max_total': 1, 'max_active': 1})

            cursor.execute("""
                SELECT id, is_active 
                FROM newsletters 
                WHERE user_id = %s 
                ORDER BY next_send_time ASC
            """, (user_id,))
            newsletters = cursor.fetchall()

            active_ids = [n['id'] for n in newsletters if n['is_active'] == 1]
            paused_ids = [n['id'] for n in newsletters if n['is_active'] == 0]

            if limits['max_active'] is not None and len(active_ids) > limits['max_active']:
                ids_to_pause = active_ids[limits['max_active']:]
                cursor.executemany("""
                    UPDATE newsletters 
                    SET is_active = 0 
                    WHERE id = %s AND user_id = %s
                """, [(nid, user_id) for nid in ids_to_pause])
                print(f"⏸️ Paused {len(ids_to_pause)} active newsletters due to downgrade.")

            cursor.execute("""
                SELECT id, is_active 
                FROM newsletters 
                WHERE user_id = %s 
                ORDER BY next_send_time ASC
            """, (user_id,))
            newsletters = cursor.fetchall()
            active_ids = [n['id'] for n in newsletters if n['is_active'] == 1]
            paused_ids = [n['id'] for n in newsletters if n['is_active'] == 0]

            total_count = len(active_ids) + len(paused_ids)
            if limits['max_total'] is not None and total_count > limits['max_total']:
                excess = total_count - limits['max_total']
                ids_to_delete = paused_ids[:excess]
                cursor.executemany("""
                    DELETE FROM newsletters 
                    WHERE id = %s AND user_id = %s
                """, [(nid, user_id) for nid in ids_to_delete])
                cursor.executemany("""
                    DELETE FROM emails 
                    WHERE plan_id = %s
                """, [(nid,) for nid in ids_to_delete])
                print(f"🗑️ Deleted {len(ids_to_delete)} paused newsletters due to total limit.")

            conn.commit()
            print(f"✅ Downgraded user {user_id} to {new_plan} after subscription end.")
            print(f"📅 Downgrade applied from webhook on {subscription_end_date}")

        cursor.close()
        conn.close()

    except Exception as e:
        print("❌ Webhook handler error:", e)

    return '', 200

#-------------- Reviews ------------------
from flask import request, render_template, redirect
import psycopg2
import psycopg2.extras
import os

DATABASE_URL = os.getenv('DATABASE_URL')

@app.route('/reviews')
def reviews():
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cursor.execute('SELECT * FROM reviews ORDER BY id DESC')
    reviews = cursor.fetchall()

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

    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO reviews (name, stars, comment) VALUES (%s, %s, %s)',
        (name, int(stars), comment)
    )
    conn.commit()
    conn.close()

    return redirect('/reviews')


# ---------------- Run App ----------------

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
