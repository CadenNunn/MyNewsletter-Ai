import os
import sqlite3
from flask import Flask, session, redirect, request, flash, url_for, render_template
from dotenv import load_dotenv
import stripe
import openai
from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
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


# Plan-based limits
PLAN_FEATURES = {
    'free': {'max_total': 1, 'max_active': 1},
    'plus': {'max_total': None, 'max_active': 1},
    'pro': {'max_total': None, 'max_active': None},
}

# Pricing Keys
STRIPE_PRICES = {
    "plus": "price_1RQLsbRq47T24X5QPjaq9zRi",
    "pro": "price_1RSKC2Rq47T24X5Q6HejslAl"
}

def get_user_email(user_id):
    conn = sqlite3.connect('newsletter.db')
    cursor = conn.cursor()
    cursor.execute("SELECT email FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None



# ---------------- Home ----------------
@app.route('/')
def home():
    return render_template('index.html')


# ------------- Builder ----------------
@app.route('/build-newsletter', methods=['GET'])
def build_newsletter():
    if 'user_id' not in session:
        flash("Please log in to create a newsletter.")
        return redirect(url_for('login'))

    user_id = session['user_id']

    conn = sqlite3.connect('newsletter.db')
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

    cursor.execute("SELECT COUNT(*) FROM newsletters WHERE user_id = ? AND is_active = 1", (user_id,))
    active_count = cursor.fetchone()[0]

    conn.close()

    # Enforce plan limits BEFORE showing the form
    if limits['max_total'] is not None and total_count >= limits['max_total']:
        flash("You’ve reached your plan’s newsletter limit. Upgrade to create more.")
        return redirect(url_for('dashboard'))

    if limits['max_active'] is not None and active_count >= limits['max_active']:
        flash("You already have the maximum number of active newsletters. Upgrade your plan to create another!")
        return redirect(url_for('dashboard'))

    return render_template('build.html')

# ---------------- Step 2: Generate Newsletter Plan ----------------

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

#--------------- create newsletter -------------------------

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
    data = session.get('newsletter', {})
    if not data:
        return redirect(url_for('build_newsletter'))

    # Updated from form
    frequency = request.form.get('frequency')
    send_time = request.form.get('send_time')
    plan_title = request.form.get('plan_title')  # ✅ this is the fix

    # Update session data with final frequency
    data['frequency'] = frequency

    user_id = data.get('user_id')
    email = data.get('email')
    topic = data.get('topic')
    demographic = data.get('demographic')
    tone = data.get('tone')
    section_titles = data.get('section_titles')
    summary = data.get('summary')

    # Determine first_send based on send_time dropdown
    now = datetime.now().replace(second=0, microsecond=0)
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
        first_send = now  # fallback

    # Map frequency to interval spacing
    freq_map = {'daily': 1, 'weekly': 7, 'biweekly': 14, 'monthly': 30}
    interval_days = freq_map.get(frequency.lower(), 7)

    conn = sqlite3.connect('newsletter.db')
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # Insert into newsletters table
    c.execute('''
        INSERT INTO newsletters (
            user_id, email, topic, demographic,
            plan_title, section_titles, summary,
            frequency, tone, next_send_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id, email, topic, demographic,
        plan_title, json.dumps(section_titles), summary,
        frequency, tone, first_send.isoformat()
    ))

    plan_id = c.lastrowid
    c.execute("UPDATE newsletters SET plan_id = ? WHERE rowid = ?", (plan_id, plan_id))

    # Create 5 scheduled emails based on spacing
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

    return render_template("success.html", next_send=first_send.strftime("%B %d, %Y %I:%M %p"))

# ---------------- User Registration ----------------

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')

        conn = sqlite3.connect('newsletter.db')
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, hashed_password))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            flash("Email already registered. Please log in instead.")
            return redirect(url_for('register'))

        conn.close()
        flash("Account created successfully. Please log in.")
        return redirect(url_for('login'))

    return render_template('register.html')

# ---------------- User Login ----------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = sqlite3.connect('newsletter.db')
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        c = conn.cursor()

        c.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['email'] = user['email']
            flash("Logged in successfully!")
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email or password.")
            return redirect(url_for('login'))

    return render_template('login.html')

# ---------------- Dashboard --------------

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash("Please log in to view your dashboard.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = sqlite3.connect('newsletter.db')
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

    # 💡 Define limits based on PLAN_FEATURES
    plan_features = PLAN_FEATURES.get(user_plan, PLAN_FEATURES['free'])
    active_limit = plan_features['max_active']
    total_limit = plan_features['max_total']

    # 🧮 Total newsletters = active + paused
    total_newsletters = newsletters_active + newsletters_paused

    conn.close()

    # ⏳ Minimum reschedule time
    min_datetime = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M')

    return render_template(
        'dashboard.html',
        newsletters_active=newsletters_active,
        newsletters_paused=newsletters_paused,
        total_newsletters=total_newsletters,
        min_datetime=min_datetime,
        active_limit=active_limit,
        total_limit=total_limit,
        user_plan=user_plan
    )

# ----------- Delete Newsletter -----------

@app.route('/delete-newsletter', methods=['POST'])
def delete_newsletter():
    if 'user_id' not in session:
        flash("Please log in to continue.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    plan_id = request.form.get('plan_id')

    conn = sqlite3.connect('newsletter.db')
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


# ---------------- Logout -----------------

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for('home'))

#----------------- Account settings -------

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

        conn = sqlite3.connect('newsletter.db')
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

    return render_template('account_settings.html', email=email, user_id=user_id, edit_mode=(mode == 'edit'))

#-------------- Change Send Timer ---------

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
    except ValueError:
        flash("Invalid date format.")
        return redirect('/dashboard')

    # Enforce minimum of 7 days in future
    if new_send_time < datetime.now() + timedelta(days=7):
        flash("Send time must be at least one week from now.")
        return redirect('/dashboard')

    conn = sqlite3.connect('newsletter.db')
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

#--------------- Delete Account -----------

@app.route('/delete-account', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        flash("Please log in first.")
        return redirect(url_for('login'))

    user_id = session['user_id']
    entered_password = request.form['confirm_password']

    conn = sqlite3.connect('newsletter.db')
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

#---------- Deactivate Newsletter ---------

@app.route('/deactivate-newsletter', methods=['POST'])
def deactivate_newsletter():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    newsletter_id = request.form.get('newsletter_id')

    conn = sqlite3.connect('newsletter.db')
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

#--------- Toggle Newsletter --------------

@app.route('/toggle-newsletter-status', methods=['POST'])
def toggle_newsletter_status():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    newsletter_id = request.form.get('newsletter_id')

    conn = sqlite3.connect('newsletter.db')
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
    new_status = 0 if current_status == 1 else 1
    cursor.execute("UPDATE newsletters SET is_active = ? WHERE id = ? AND user_id = ?", (new_status, newsletter_id, user_id))
    conn.commit()
    conn.close()

    flash("Newsletter status updated.")
    return redirect(url_for('dashboard'))

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
    conn = sqlite3.connect('newsletter.db')
    cursor = conn.cursor()
    cursor.execute("SELECT plan FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()

    current_plan = row[0] if row else 'free'
    plan_order = {'free': 0, 'plus': 1, 'pro': 2}
    current_plan_rank = plan_order.get(current_plan, 0)

    return render_template("pricing.html", current_plan=current_plan, current_plan_rank=current_plan_rank)

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
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            mode='subscription',
            customer_email=user_email,
            line_items=[{
                'price': STRIPE_PRICES[plan],
                'quantity': 1
            }],
            success_url=url_for('payment_success', _external=True),
            cancel_url=url_for('payment_cancel', _external=True),
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

@app.route('/payment-success')
def payment_success():
    flash("✅ Your payment was successful and your plan will be updated shortly.")
    return redirect(url_for('dashboard'))

@app.route('/payment-cancel')
def payment_cancel():
    flash("❌ Payment was canceled.")
    return redirect(url_for('pricing'))

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    print("✅ /webhook route was triggered")

    try:
        event = request.get_json(force=True)  # <- FORCE JSON parsing
        print("📦 Webhook received:", event)
    except Exception as e:
        print("❌ Failed to parse JSON:", e)
        return 'Bad Request', 400

    if event and event.get('type') == 'checkout.session.completed':
        session = event['data']['object']
        user_id = session['metadata']['user_id']
        new_plan = session['metadata']['new_plan']

        conn = sqlite3.connect('newsletter.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET plan = ? WHERE id = ?", (new_plan, user_id))
        conn.commit()
        conn.close()

        print(f"✅ Plan updated: user {user_id} → {new_plan}")

    return '', 200

# ---------------- Run App ----------------

if __name__ == '__main__':
    app.run(debug=True)
