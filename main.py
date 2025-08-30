import os
import sys
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from database import init_app_db, get_db, query_db, execute_db, get_db_connection
from scheduler import run_job
from ai_processing import summarize_text, sanitize_text

# --- App Configuration ---
app = Flask(__name__)
app.secret_key = 'your_super_secret_key_for_production'
app.config['DB_PASSWORD'] = None
DATABASE = 'tickets.db'

scheduler = BackgroundScheduler()
init_app_db(app)

# --- Web Application Routes ---
@app.before_request
def before_request_tasks():
    if request.endpoint in ['login', 'static']:
        return
    if not app.config.get('DB_PASSWORD'):
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password_attempt = request.form.get('password')
        try:
            # Test the password
            with get_db_connection(password_attempt) as con:
                # If scheduler isn't running, this is the first successful login
                if not scheduler.running:
                    print("--- First successful login. Starting background scheduler. ---")
                    jobs = con.execute("SELECT id, script_path, interval_minutes FROM scheduler_jobs WHERE enabled = 1").fetchall()
                    for job in jobs:
                        scheduler.add_job(
                            run_job,
                            'interval',
                            minutes=job['interval_minutes'],
                            args=[job['id'], job['script_path'], password_attempt],
                            id=str(job['id']),
                            next_run_time=datetime.now() + timedelta(seconds=10) # Start after 10s
                        )
                    scheduler.start()
            # Store password in app's config
            app.config['DB_PASSWORD'] = password_attempt
            flash('Database unlocked successfully!', 'success')
            return redirect(url_for('tickets_list'))
        except (ValueError, Exception) as e:
            flash(f"Login failed: Invalid master password. {e}", 'error')
    return render_template('login.html')

@app.route('/')
def tickets_list():
    tickets = query_db("SELECT * FROM tickets ORDER BY updated_at DESC")
    return render_template('tickets.html', tickets=tickets)

@app.route('/ticket/<int:ticket_id>')
def ticket_details(ticket_id):
    ticket = query_db("SELECT * FROM tickets WHERE id = ?", [ticket_id], one=True)
    replies = query_db("SELECT * FROM ticket_replies WHERE ticket_id = ? ORDER BY created_at ASC", [ticket_id])
    return render_template('ticket_details.html', ticket=ticket, replies=replies)

@app.route('/ticket/<int:ticket_id>/reply', methods=['POST'])
def add_reply(ticket_id):
    content = request.form.get('content')
    if content:
        now = datetime.now().isoformat()
        execute_db("INSERT INTO ticket_replies (ticket_id, content, created_at) VALUES (?, ?, ?)",
                   (ticket_id, content, now))
        execute_db("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
        flash("Reply added successfully.", "success")
    else:
        flash("Reply content cannot be empty.", "error")
    return redirect(url_for('ticket_details', ticket_id=ticket_id))

@app.route('/settings')
def settings():
    jobs = query_db("SELECT * FROM scheduler_jobs")
    return render_template('settings.html', jobs=jobs)

@app.route('/summarize', methods=['POST'])
def summarize():
    text = request.json.get('text')
    summary = summarize_text(text)
    return jsonify({'summary': summary})

@app.route('/sanitize', methods=['POST'])
def sanitize():
    text = request.json.get('text')
    sanitized = sanitize_text(text)
    return jsonify({'sanitized': sanitized})

if __name__ == '__main__':
    if not os.path.exists(DATABASE):
        print(f"Database not found. Run 'python init_db.py' first.", file=sys.stderr)
        sys.exit(1)
    try:
        app.run(debug=True, host='0.0.0.0', port=5003)
    finally:
        if scheduler.running:
            print("--- Shutting down scheduler ---")
            scheduler.shutdown()
