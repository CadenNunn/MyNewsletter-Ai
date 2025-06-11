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

def get_user_email(user_id):
    conn = sqlite3.connect('/mnt/data/newsletter.db')

    cursor = conn.cursor()
    cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


#---------- Scheduler auto delete ------------
def delete_newsletter_plan(plan_id, user_id, retries=5, delay=0.5):
    for attempt in range(retries):
        try:
            conn = sqlite3.connect('/mnt/data/newsletter.db')

            c = conn.cursor()
            c.execute("DELETE FROM newsletters WHERE id = ?", (plan_id,))
            c.execute("DELETE FROM emails WHERE plan_id = ?", (plan_id,))
            c.execute("DELETE FROM past_newsletters WHERE plan_id = ?", (plan_id,))
            conn.commit()
            conn.close()
            print(f"✅ Plan {plan_id} successfully deleted.")
            return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < retries - 1:
                print(f"⚠️ DB locked, retrying... ({attempt + 1})")
                time.sleep(delay)
            else:
                print(f"❌ Final DB error: {e}")
                return False

#----------- Scheduler auto delete check -------
@app.route('/check-and-delete-plan', methods=['POST'])
def check_and_delete_plan():
    import time
    import sqlite3

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

    # ✅ Short delay to allow scheduler DB commits to flush
    time.sleep(1)

    # 🔍 Get user_id for the plan
    conn1 = sqlite3.connect('/mnt/data/newsletter.db')

    conn1.row_factory = sqlite3.Row
    c1 = conn1.cursor()
    c1.execute("SELECT user_id FROM newsletters WHERE id = ?", (plan_id,))
    row = c1.fetchone()
    if not row:
        conn1.close()
        print(f"❌ [Flask] No newsletter plan found for ID {plan_id}")
        return "Plan not found", 404
    user_id = row["user_id"]
    conn1.close()
    print(f"👤 Found user_id={user_id} for plan_id={plan_id}")

    # 🔁 Re-check total vs sent counts
    conn2 = sqlite3.connect('/mnt/data/newsletter.db')

    c2 = conn2.cursor()

    c2.execute("SELECT COUNT(*) FROM emails WHERE plan_id = ?", (plan_id,))
    total = c2.fetchone()[0]

    c2.execute("SELECT COUNT(*) FROM emails WHERE plan_id = ? AND sent = 1", (plan_id,))
    sent = c2.fetchone()[0]

    conn2.close()

    print(f"📊 Total emails in plan {plan_id}: {total}")
    print(f"📬 Sent emails for plan {plan_id}: {sent}")

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

    try:
        conn = sqlite3.connect('/mnt/data/newsletter.db')

        cursor = conn.cursor()
        cursor.execute("""
            SELECT subscription_end_date, downgrade_to 
            FROM users 
            WHERE id = ?
        """, (session['user_id'],))
        row = cursor.fetchone()
        conn.close()

        downgrade_to = row[1] if row else None
        subscription_end_date = row[0] if row else None

        days_left = None
        if subscription_end_date and subscription_end_date.strip():
            from datetime import datetime, timedelta, timezone
            try:
                end_date = datetime.fromisoformat(subscription_end_date)
                from datetime import date, timedelta, timezone
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


# ---------------- Home ----------------
@app.route('/')
def home():
    return render_template('index.html')


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

    conn = sqlite3.connect('/mnt/data/newsletter.db')

    cursor = conn.cursor()

    # Get user's plan
    cursor.execute("SELECT plan FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        flash("Could not verify your plan. Please try again.")
        return redirect(url_for('dashboard'))

    plan = row[0]
    limits = PLAN_FEATURES.get(plan, {'max_total': 1, 'max_active': 1})

    # Count newsletters
    cursor.execute("SELECT COUNT(*) FROM newsletters WHERE user_id = ?", (user_id,))
    total_count = cursor.fetchone()[0]

    conn.close()

    # ✅ Only enforce total limit (not active limit)
    if limits['max_total'] is not None and total_count >= limits['max_total']:
        flash("You’ve reached your plan’s newsletter limit. Upgrade to create more.")
        return redirect(url_for('dashboard'))

    return render_template('build.html')

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
        send_time_str = request.form.get('send_time')

        try:
            # Parse send_time from UTC ISO format (e.g. "2025-06-10T13:00")
            first_send = datetime.strptime(send_time_str, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        except:
            first_send = now  # fallback

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

    freq_map = {'daily': 1, 'weekly': 7, 'biweekly': 14, 'monthly': 30}
    interval_days = freq_map.get(frequency.lower(), 7)

    conn = sqlite3.connect('/mnt/data/newsletter.db')

    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # ✅ Plan-based activation decision
    c.execute("SELECT plan FROM users WHERE id = ?", (user_id,))
    user_plan_row = c.fetchone()
    plan = user_plan_row[0].lower() if user_plan_row else 'free'
    limits = PLAN_FEATURES.get(plan, {'max_active': 1})

    c.execute("SELECT COUNT(*) FROM newsletters WHERE user_id = ? AND is_active = 1", (user_id,))
    active_count = c.fetchone()[0]

    is_active = 1 if limits['max_active'] is None or active_count < limits['max_active'] else 0
    is_active = int(is_active)  # Ensure value is safe

    print("📊 Plan:", plan)
    print("📊 Active Count:", active_count)
    print("📌 Final is_active value:", is_active)

    # ✅ Insert newsletter
    c.execute('''
        INSERT INTO newsletters (
            user_id, email, topic, demographic,
            plan_title, section_titles, summary,
            frequency, tone, next_send_time, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id, email, topic, demographic,
        plan_title, json.dumps(section_titles), summary,
        frequency, tone, first_send.isoformat(), is_active
    ))

    plan_id = c.lastrowid
    c.execute("UPDATE newsletters SET plan_id = ? WHERE rowid = ?", (plan_id, plan_id))

    # 🧾 Verify the value in DB
    c.execute("SELECT id, is_active FROM newsletters WHERE id = ?", (plan_id,))
    result = c.fetchone()
    print("🧾 Inserted Newsletter ID:", result[0], "| is_active =", result[1])

    # Insert scheduled emails
    for i, title in enumerate(section_titles):
        send_date = first_send + timedelta(days=interval_days * i)
        c.execute('''
            INSERT INTO emails (
                user_id, plan_id, position_in_plan,
                title, send_date, sent
            ) VALUES (?, ?, ?, ?, ?, 0)
        ''', (
            user_id, plan_id, i + 1, title, send_date.isoformat()
        ))

    conn.commit()
    conn.close()

    return render_template("success.html", next_send=first_send.isoformat())

# ---------------- User Registration ----------------
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

        conn = sqlite3.connect('/mnt/data/newsletter.db')

        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, hashed_password))
            conn.commit()

            # ✅ Auto-login after successful registration
            c.execute("SELECT id FROM users WHERE email = ?", (email,))
            user = c.fetchone()
            session['user_id'] = user['id']
            session['email'] = email
            flash("Account created and logged in successfully!", "auth")
            conn.close()
            return redirect(url_for('dashboard'))

        except sqlite3.IntegrityError:
            conn.close()
            flash("Email already registered. Please log in instead.", "auth")
            return redirect(url_for('register'))

    return render_template('register.html')

# ---------------- User Login ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = sqlite3.connect('/mnt/data/newsletter.db')

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()

        c.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        conn.close()

        if user:
            if check_password_hash(user['password_hash'], password):
                session['user_id'] = user['id']
                session['email'] = user['email']
                flash("Logged in successfully!", "auth")
                return redirect(url_for('dashboard'))
            else:
                flash("Incorrect password. Please try again.", "auth")
                return redirect(url_for('login'))
        else:
            flash("No account found with that email.", "auth")
            return redirect(url_for('login'))

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
    conn = sqlite3.connect('/mnt/data/newsletter.db')

    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 🔵 Active newsletters
    c.execute("SELECT * FROM newsletters WHERE user_id = ? AND is_active = 1 ORDER BY next_send_time DESC", (user_id,))
    newsletters_active = c.fetchall()

    # 🟡 Paused newsletters
    c.execute("SELECT * FROM newsletters WHERE user_id = ? AND is_active = 0 ORDER BY next_send_time DESC", (user_id,))
    newsletters_paused = c.fetchall()

    # 🧠 Fetch user's plan from the users table
    c.execute("SELECT plan FROM users WHERE id = ?", (user_id,))
    user_row = c.fetchone()
    user_plan = user_row['plan'].lower() if user_row and 'plan' in user_row.keys() else 'free'

    # 🧠 Get progress per plan (sent vs total)
    c.execute("""
        SELECT plan_id, COUNT(*) AS total,
               SUM(CASE WHEN sent = 1 THEN 1 ELSE 0 END) AS sent
        FROM emails
        WHERE user_id = ?
        GROUP BY plan_id
    """, (user_id,))
    plan_progress = {row[0]: {'total': row[1], 'sent': row[2]} for row in c.fetchall()}

    # 💡 Define limits based on PLAN_FEATURES
    plan_features = PLAN_FEATURES.get(user_plan, PLAN_FEATURES['free'])
    active_limit = plan_features['max_active']
    total_limit = plan_features['max_total']

    # 🧮 Total newsletters = active + paused
    total_newsletters = newsletters_active + newsletters_paused

    conn.close()

    # ⏳ Minimum reschedule time
    min_datetime = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

    active_count = len(newsletters_active)
    total_count = len(newsletters_active) + len(newsletters_paused)

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

    conn = sqlite3.connect('/mnt/data/newsletter.db')

    c = conn.cursor()

    # Check ownership + get frequency
    c.execute("SELECT frequency FROM newsletters WHERE id = ? AND user_id = ?", (plan_id, user_id))
    row = c.fetchone()
    if not row:
        conn.close()
        flash("Newsletter not found or access denied.")
        return redirect('/dashboard')

    frequency = row[0].lower()
    delta = {
        "weekly": timedelta(weeks=1),
        "biweekly": timedelta(weeks=2),
        "monthly": timedelta(weeks=4)
    }.get(frequency, timedelta(weeks=1))

    # Update newsletter next_send_time
    c.execute("UPDATE newsletters SET next_send_time = ? WHERE id = ?", (new_send_time, plan_id))

    # Update future unsent emails
    c.execute("""
        SELECT email_id, position_in_plan FROM emails
        WHERE plan_id = ? AND sent = 0
        ORDER BY position_in_plan ASC
    """, (plan_id,))
    emails = c.fetchall()

    for i, (email_id, _) in enumerate(emails):
        scheduled_date = new_send_time + i * delta
        c.execute("UPDATE emails SET send_date = ? WHERE email_id = ?", (scheduled_date, email_id))

    conn.commit()
    conn.close()

    flash("Send time updated successfully.", "rescheduled")
    return redirect('/dashboard')

# ----------- Delete Newsletter -----------

@app.route('/delete-newsletter', methods=['POST'])
def delete_newsletter():
    if 'user_id' not in session:
        flash("Please log in to continue.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    plan_id = request.form.get('plan_id')

    conn = sqlite3.connect('/mnt/data/newsletter.db')
    c = conn.cursor()

    # Verify the plan belongs to the user
    c.execute("SELECT id FROM newsletters WHERE id = ? AND user_id = ?", (plan_id, user_id))
    if not c.fetchone():
        conn.close()
        flash("Invalid or unauthorized newsletter plan.")
        return redirect(url_for('dashboard'))

    # Delete the newsletter plan
    c.execute("DELETE FROM newsletters WHERE id = ?", (plan_id,))

    # Delete only unsent emails
    c.execute("DELETE FROM emails WHERE plan_id = ? AND sent = 0", (plan_id,))
    conn.commit()
    conn.close()

    flash("Newsletter plan deleted.", "deleted")
    return redirect(url_for('dashboard'))


#--------- Toggle Newsletter --------------

@app.route('/toggle-newsletter-status', methods=['POST'])
def toggle_newsletter_status():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    newsletter_id = request.form.get('newsletter_id')

    conn = sqlite3.connect('/mnt/data/newsletter.db')

    cursor = conn.cursor()

    # Check current status
    cursor.execute("SELECT is_active FROM newsletters WHERE id = ? AND user_id = ?", (newsletter_id, user_id))
    row = cursor.fetchone()

    if not row:
        conn.close()
        flash("Newsletter not found.")
        return redirect(url_for('dashboard'))

    current_status = row[0]

    # Only check plan limits if activating
    if current_status == 0:
        # Get user's plan
        cursor.execute("SELECT plan FROM users WHERE id = ?", (user_id,))
        user_row = cursor.fetchone()

        if not user_row:
            conn.close()
            flash("Could not verify your plan.")
            return redirect(url_for('dashboard'))

        plan = user_row[0]
        limits = PLAN_FEATURES.get(plan, {'max_active': 1})

        # Count current active newsletters
        cursor.execute("SELECT COUNT(*) FROM newsletters WHERE user_id = ? AND is_active = 1", (user_id,))
        active_count = cursor.fetchone()[0]

        if limits['max_active'] is not None and active_count >= limits['max_active']:
            conn.close()
            flash("You’ve reached your active newsletter limit. Pause one to activate another.")
            return redirect(url_for('dashboard'))

    # Toggle status
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    cursor.execute("SELECT frequency FROM newsletters WHERE id = ?", (newsletter_id,))
    frequency_row = cursor.fetchone()
    frequency = frequency_row[0].lower() if frequency_row else 'weekly'

    # Map frequency to interval
    freq_map = {'daily': 1, 'weekly': 7, 'biweekly': 14, 'monthly': 30}
    interval_days = freq_map.get(frequency, 7)

    if current_status == 1:
        # PAUSING: set is_active=0, clear future send dates
        cursor.execute("UPDATE newsletters SET is_active = 0 WHERE id = ? AND user_id = ?", (newsletter_id, user_id))
        cursor.execute("""
            UPDATE emails SET send_date = NULL 
            WHERE plan_id = ? AND user_id = ? AND sent = 0
        """, (newsletter_id, user_id))
    else:
        # RESUMING: set is_active=1, re-anchor from now
        cursor.execute("UPDATE newsletters SET is_active = 1, next_send_time = ? WHERE id = ? AND user_id = ?", (now.isoformat(), newsletter_id, user_id))

        # Get all unsent emails ordered
        cursor.execute("""
            SELECT email_id FROM emails 
            WHERE plan_id = ? AND user_id = ? AND sent = 0
            ORDER BY position_in_plan ASC
        """, (newsletter_id, user_id))
        unsent_emails = cursor.fetchall()

        # Update send dates from now, spaced by interval
        for i, (email_id,) in enumerate(unsent_emails):
            new_send = now + timedelta(days=interval_days * i)
            cursor.execute("UPDATE emails SET send_date = ? WHERE email_id = ?", (new_send.isoformat(), email_id))

    conn.commit()
    conn.close()

    flash("Newsletter status updated.")
    return redirect(url_for('dashboard'))

#---------- Deactivate Newsletter ---------

@app.route('/deactivate-newsletter', methods=['POST'])
def deactivate_newsletter():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    newsletter_id = request.form.get('newsletter_id')

    conn = sqlite3.connect('/mnt/data/newsletter.db')

    cursor = conn.cursor()

    cursor.execute("""
        UPDATE newsletters
        SET is_active = 0
        WHERE id = ? AND user_id = ?
    """, (newsletter_id, user_id))

    conn.commit()
    conn.close()

    flash("Newsletter deactivated successfully.", "rescheduled")
    return redirect(url_for('dashboard'))



#----------------- Account settings -------
import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import stripe

# Stripe config
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

@app.route('/account-settings', methods=['GET', 'POST'])
def account_settings():
    if 'user_id' not in session:
        flash("Please log in to view account settings.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    email = session['email']
    mode = request.args.get('mode')

    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        if new_password != confirm_password:
            flash("New passwords do not match.")
            return redirect(url_for('account_settings', mode='edit'))

        conn = sqlite3.connect('/mnt/data/newsletter.db')

        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()

        if not user or not check_password_hash(user['password_hash'], current_password):
            flash("Incorrect current password.")
            conn.close()
            return redirect(url_for('account_settings', mode='edit'))

        # Update password
        new_hash = generate_password_hash(new_password, method='pbkdf2:sha256')
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
        conn.commit()
        conn.close()

        flash("Password updated successfully.")
        return redirect(url_for('account_settings'))

    # Fetch subscription details
    conn = sqlite3.connect('/mnt/data/newsletter.db')

    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT plan, subscription_end_date FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    conn.close()

    current_plan = user['plan']
    from datetime import datetime, timedelta, timezone

    raw_date = user['subscription_end_date']
    try:
        parsed = datetime.fromisoformat(raw_date)
        next_billing_date = parsed.strftime("%B %d, %Y")  # e.g., "July 07, 2025"
    except Exception as e:
        print("⚠️ Failed to format billing date:", e)
        next_billing_date = raw_date or "Unknown"


    # Define cost based on plan
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


# ✅ Dynamic Stripe Portal session (TEST MODE ready)
@app.route('/stripe-portal')
def stripe_portal():
    if 'user_id' not in session:
        flash("Please log in to access billing.")
        return redirect(url_for('login'))

    user_id = session['user_id']

    # Get Stripe customer ID from DB
    conn = sqlite3.connect('/mnt/data/newsletter.db')

    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT stripe_customer_id FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    conn.close()

    if not user or not user['stripe_customer_id']:
        flash("Stripe billing info not found.")
        return redirect(url_for('account_settings'))

    try:
        session_obj = stripe.billing_portal.Session.create(
            customer=user['stripe_customer_id'],
            return_url=url_for('account_settings', _external=True)
        )
        return redirect(session_obj.url)
    except Exception as e:
        print("⚠️ Stripe error:", e)
        flash("Unable to open billing portal.")
        return redirect(url_for('account_settings'))


#--------------- Delete Account -----------

@app.route('/delete-account', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        flash("Please log in first.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    entered_password = request.form['confirm_password']

    conn = sqlite3.connect('/mnt/data/newsletter.db')

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # Verify password
    c.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    if not user or not check_password_hash(user['password_hash'], entered_password):
        conn.close()
        flash("Incorrect password.")
        return redirect(url_for('account_settings'))

    # Delete associated data
    c.execute("DELETE FROM emails WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM newsletters WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()

    session.clear()
    flash("Your account and all associated data have been deleted.")
    return redirect(url_for('home'))


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
    if 'user_id' not in session:
        flash("Please log in to view pricing.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = sqlite3.connect('/mnt/data/newsletter.db')

    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT plan, downgrade_to, subscription_end_date FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    current_plan = row['plan'] if row else 'free'
    downgrade_to = row['downgrade_to'] if row else None
    subscription_end_date = row['subscription_end_date']

    # Calculate days left until downgrade (if applicable)
    subscription_days_left = None
    if subscription_end_date:
        from datetime import datetime, timedelta, timezone
        from math import ceil
        try:
            dt = datetime.fromisoformat(subscription_end_date)
            delta = (dt - datetime.utcnow()).total_seconds() / 86400  # full days
            subscription_days_left = max(ceil(delta), 0)
        except Exception as e:
            print("⚠️ Error parsing subscription_end_date:", e)

    plan_order = {'free': 0, 'plus': 1, 'pro': 2}
    current_plan_rank = plan_order.get(current_plan, 0)

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

    try:
        # ✅ Check if user already has a Stripe customer ID
        conn = sqlite3.connect('/mnt/data/newsletter.db')

        cursor = conn.cursor()
        cursor.execute("SELECT stripe_customer_id FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        stripe_customer_id = row[0] if row else None

        # ✅ If not, create a new Stripe customer and store it
        if not stripe_customer_id:
            customer = stripe.Customer.create(email=user_email)
            stripe_customer_id = customer.id
            cursor.execute("UPDATE users SET stripe_customer_id = ? WHERE id = ?", (stripe_customer_id, user_id))
            conn.commit()
        conn.close()

        # ✅ Use customer ID for session (not just email)
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='subscription',
            customer=stripe_customer_id,
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
        # ✅ New subscription via checkout
        if event['type'] == 'checkout.session.completed':
            from datetime import datetime, timedelta, timezone
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

            conn = sqlite3.connect('/mnt/data/newsletter.db')

            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET plan = ?, subscription_id = ?, stripe_customer_id = ?, subscription_end_date = ?, downgrade_to = NULL
                WHERE id = ?
            """, (new_plan, subscription_id, customer_id, subscription_end_date, int(user_id)))
            conn.commit()
            conn.close()

            print(f"✅ Plan updated: user {user_id} → {new_plan}")
            print(f"📅 Next Billing Date: {subscription_end_date}")

            #✅ Subscription updated
        elif event['type'] == 'customer.subscription.updated':
            from datetime import datetime, timedelta, timezone
            subscription = event['data']['object']
            stripe_customer_id = subscription.get('customer')
            cancel_at_end = subscription.get("cancel_at_period_end")
            current_price_id = subscription['items']['data'][0]['price']['id']

            if not stripe_customer_id:
                print("⚠️ Missing Stripe customer ID in subscription.updated")
                return '', 200

            # ✅ Always update subscription_end_date using correct path
            items_data = subscription.get('items', {}).get('data', [])
            ends_at = items_data[0].get("current_period_end") if items_data else None
            subscription_end_date = datetime.utcfromtimestamp(ends_at).isoformat() if ends_at else None

            conn = sqlite3.connect('/mnt/data/newsletter.db')

            cursor = conn.cursor()
            cursor.execute("""
                UPDATE users 
                SET subscription_end_date = ?
                WHERE stripe_customer_id = ?
            """, (subscription_end_date, stripe_customer_id))
            conn.commit()
            conn.close()

            print(f"📅 Subscription end date updated to {subscription_end_date} for customer {stripe_customer_id}")

            if cancel_at_end:
                conn = sqlite3.connect('/mnt/data/newsletter.db')

                cursor = conn.cursor()

                # Map current Stripe price ID to plan name for logging
                price_map = {
                    "price_1RSVeL2MKajKZrXP4yIxSw58": "pro",
                    "price_1RSVe72MKajKZrXPrsuvcvnO": "plus"
                }
                current_plan_name = price_map.get(current_price_id, "unknown")

                # Always downgrade to 'free'
                downgrade_to = "free"

                cursor.execute("SELECT id FROM users WHERE stripe_customer_id = ?", (stripe_customer_id,))
                row = cursor.fetchone()

                if not row:
                    print(f"⚠️ No user found with customer_id={stripe_customer_id}")
                    conn.close()
                    return '', 200

                user_id = row[0]
                cursor.execute("UPDATE users SET downgrade_to = ? WHERE id = ?", (downgrade_to, user_id))
                conn.commit()
                conn.close()

                print(f"📌 Saved downgrade_to='{downgrade_to}' for user {user_id} (currently on {current_plan_name}, cancel_at_period_end=True)")

        # ✅ Subscription cancelled
        elif event['type'] == 'customer.subscription.deleted':
            from datetime import datetime, timedelta, timezone
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

            conn = sqlite3.connect('/mnt/data/newsletter.db')

            cursor = conn.cursor()
            cursor.execute("SELECT id, downgrade_to FROM users WHERE stripe_customer_id = ?", (stripe_customer_id,))
            row = cursor.fetchone()

            if not row:
                print(f"⚠️ No user found for Stripe customer {stripe_customer_id}")
                conn.close()
                return '', 200

            user_id, downgrade_to = row
            if user_id is None:
                print("⚠️ Fetched user_id is None")
                conn.close()
                return '', 200

            user_id = int(user_id)  # ✅ Ensure correct type

            new_plan = downgrade_to if downgrade_to else 'free'
            if not downgrade_to:
                print(f"⚠️ downgrade_to was not previously saved for user {user_id}, defaulting to 'free'")

            cursor.execute("""
                UPDATE users 
                SET plan = ?, subscription_id = NULL, subscription_end_date = ?, downgrade_to = NULL
                WHERE id = ?
            """, (new_plan, subscription_end_date, user_id))

            # ✅ Enforce plan limits after downgrade
            limits = PLAN_FEATURES.get(new_plan, {'max_total': 1, 'max_active': 1})

            cursor.execute("""
                SELECT id, is_active 
                FROM newsletters 
                WHERE user_id = ? 
                ORDER BY next_send_time ASC
            """, (user_id,))
            newsletters = cursor.fetchall()

            active_ids = [n[0] for n in newsletters if n[1] == 1]
            paused_ids = [n[0] for n in newsletters if n[1] == 0]

            # Enforce active limit
            if limits['max_active'] is not None and len(active_ids) > limits['max_active']:
                ids_to_pause = active_ids[limits['max_active']:]
                cursor.executemany("""
                    UPDATE newsletters 
                    SET is_active = 0 
                    WHERE id = ? AND user_id = ?
                """, [(nid, user_id) for nid in ids_to_pause])
                print(f"⏸️ Paused {len(ids_to_pause)} active newsletters due to downgrade.")

                # 🔁 Recalculate paused and active after pausing
                cursor.execute("""
                    SELECT id, is_active 
                    FROM newsletters 
                    WHERE user_id = ? 
                    ORDER BY next_send_time ASC
                """, (user_id,))
                newsletters = cursor.fetchall()
                active_ids = [n[0] for n in newsletters if n[1] == 1]
                paused_ids = [n[0] for n in newsletters if n[1] == 0]

            # Enforce total limit
            total_count = len(active_ids) + len(paused_ids)
            if limits['max_total'] is not None and total_count > limits['max_total']:
                excess = total_count - limits['max_total']
                ids_to_delete = paused_ids[:excess]  # Only delete paused ones
                cursor.executemany("""
                    DELETE FROM newsletters 
                    WHERE id = ? AND user_id = ?
                """, [(nid, user_id) for nid in ids_to_delete])
                cursor.executemany("""
                    DELETE FROM emails 
                    WHERE plan_id = ?
                """, [(nid,) for nid in ids_to_delete])
                print(f"🗑️ Deleted {len(ids_to_delete)} paused newsletters due to total limit.")

            conn.commit()
            conn.close()


            print(f"✅ Downgraded user {user_id} to {new_plan} after subscription end.")
            print(f"📅 Downgrade applied from webhook on {subscription_end_date}")

    except Exception as e:
        print("❌ Webhook handler error:", e)

    return '', 200

# ---------------- Run App ----------------

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
