import os
import sys
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.security import generate_password_hash, check_password_hash
from database import init_app_db, get_db, query_db, execute_db, get_db_connection
from scheduler import run_job
from ai_processing import summarize_text, sanitize_text, chat_with_context

# --- App Configuration ---
app = Flask(__name__)
app.secret_key = 'your_super_secret_key_for_production'
app.config['DB_PASSWORD'] = None
DATABASE = 'tickets.db'

scheduler = BackgroundScheduler()
init_app_db(app)

# --- Helper Functions ---
def get_current_user():
    # *** BUG FIX HERE ***
    # Don't try to query the DB if the password isn't even set yet.
    if not app.config.get('DB_PASSWORD'):
        return None
        
    user_id = session.get('user_id')
    if user_id:
        return query_db("SELECT * FROM users WHERE id = ?", [user_id], one=True)
    return None

@app.context_processor
def inject_user():
    return dict(current_user=get_current_user())

# --- Web Application Routes ---
@app.before_request
def before_request_tasks():
    if request.endpoint in ['unlock_db', 'static', 'user_login']:
        return
    if not app.config.get('DB_PASSWORD'):
        return redirect(url_for('unlock_db'))
    if not session.get('user_id') and request.endpoint != 'user_login':
        return redirect(url_for('user_login'))

@app.route('/unlock', methods=['GET', 'POST'])
def unlock_db():
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
            # Set the password in the environment for other scripts to use
            os.environ['DB_MASTER_PASSWORD'] = password_attempt
            flash('Database unlocked successfully! Please log in.', 'success')
            return redirect(url_for('user_login'))
        except (ValueError, Exception) as e:
            flash(f"Login failed: Invalid master password. {e}", 'error')
    return render_template('unlock.html')

@app.route('/login', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = query_db("SELECT * FROM users WHERE username = ?", [username], one=True)

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['role'] = user['role']
            flash(f"Welcome, {user['username']}!", 'success')
            return redirect(url_for('tickets_list'))
        else:
            flash("Invalid username or password.", 'error')
    return render_template('user_login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for('unlock_db'))


@app.route('/')
def tickets_list():
    tickets = query_db("""
        SELECT t.*, c.name as company_name, u.username as user_username
        FROM tickets t
        JOIN companies c ON t.company_id = c.id
        JOIN users u ON t.user_id = u.id
        ORDER BY t.updated_at DESC
    """)
    return render_template('tickets.html', tickets=tickets)

@app.route('/ticket/<int:ticket_id>')
def ticket_details(ticket_id):
    ticket = query_db("SELECT t.*, c.name as company_name, u.username as user_username FROM tickets t JOIN companies c ON t.company_id = c.id JOIN users u ON t.user_id = u.id WHERE t.id = ?", [ticket_id], one=True)
    company_notes_rows = query_db("SELECT content FROM company_notes WHERE company_id = ?", [ticket['company_id']])
    user_notes_rows = query_db("SELECT content FROM user_notes WHERE user_id = ?", [ticket['user_id']])
    
    # Convert Row objects to dictionaries
    company_notes = [dict(row) for row in company_notes_rows]
    user_notes = [dict(row) for row in user_notes_rows]

    replies = query_db("SELECT r.*, u.username as author_name FROM ticket_replies r LEFT JOIN users u ON r.author_id = u.id WHERE r.ticket_id = ? ORDER BY r.created_at ASC", [ticket_id])
    return render_template('ticket_details.html', ticket=ticket, replies=replies, company_notes=company_notes, user_notes=user_notes)

@app.route('/ticket/<int:ticket_id>/reply', methods=['POST'])
def add_reply(ticket_id):
    content = request.form.get('content')
    current_user = get_current_user()
    if content and current_user and current_user['role'] in ['Admin', 'Technician']:
        now = datetime.now().isoformat()
        execute_db("INSERT INTO ticket_replies (ticket_id, content, created_at, author_id) VALUES (?, ?, ?, ?)",
                   (ticket_id, content, now, current_user['id']))
        execute_db("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
        flash("Reply added successfully.", "success")
    else:
        flash("Reply content cannot be empty or you do not have permission.", "error")
    return redirect(url_for('ticket_details', ticket_id=ticket_id))

@app.route('/settings')
def settings():
    jobs = query_db("SELECT * FROM scheduler_jobs")
    return render_template('settings.html', jobs=jobs)

# --- Company Management ---
@app.route('/settings/companies')
def list_companies():
    companies = query_db("SELECT * FROM companies ORDER BY name")
    return render_template('companies.html', companies=companies)

@app.route('/settings/company/new', methods=['GET', 'POST'])
def create_company():
    if request.method == 'POST':
        name = request.form.get('name')
        if name:
            execute_db("INSERT INTO companies (name) VALUES (?)", (name,))
            flash("Company created successfully.", "success")
            return redirect(url_for('list_companies'))
        else:
            flash("Company name is required.", "error")
    return render_template('edit_company.html', company=None, notes=[], users=[])

@app.route('/settings/company/<int:company_id>/edit', methods=['GET', 'POST'])
def edit_company(company_id):
    company = query_db("SELECT * FROM companies WHERE id = ?", [company_id], one=True)
    if request.method == 'POST':
        name = request.form.get('name')
        if name:
            execute_db("UPDATE companies SET name = ? WHERE id = ?", (name, company_id))
            flash("Company updated successfully.", "success")
            return redirect(url_for('list_companies'))
        else:
            flash("Company name is required.", "error")

    notes = query_db("SELECT * FROM company_notes WHERE company_id = ? ORDER BY created_at DESC", [company_id])
    users = query_db("SELECT * FROM users WHERE company_id = ? ORDER BY username", [company_id])
    return render_template('edit_company.html', company=company, notes=notes, users=users)

@app.route('/settings/company/<int:company_id>/notes/add', methods=['POST'])
def add_company_note(company_id):
    content = request.form.get('content')
    if content:
        execute_db("INSERT INTO company_notes (company_id, content, created_at) VALUES (?, ?, ?)",
                   (company_id, content, datetime.now().isoformat()))
        flash("Note added.", "success")
    return redirect(url_for('edit_company', company_id=company_id))

@app.route('/settings/company/notes/<int:note_id>/delete', methods=['POST'])
def delete_company_note(note_id):
    note = query_db("SELECT company_id FROM company_notes WHERE id = ?", [note_id], one=True)
    if note:
        execute_db("DELETE FROM company_notes WHERE id = ?", [note_id])
        flash("Note deleted.", "success")
        return redirect(url_for('edit_company', company_id=note['company_id']))
    return redirect(url_for('list_companies'))


# --- User Management ---
@app.route('/settings/users')
def list_users():
    users = query_db("SELECT u.*, c.name as company_name FROM users u JOIN companies c ON u.company_id = c.id ORDER BY u.username")
    return render_template('users.html', users=users)

@app.route('/settings/user/new', methods=['GET', 'POST'])
def create_user():
    companies = query_db("SELECT id, name FROM companies ORDER BY name")
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        company_id = request.form.get('company_id')
        role = request.form.get('role')

        if not all([username, email, password, company_id, role]):
            flash("All fields are required.", "error")
        else:
            password_hash = generate_password_hash(password)
            execute_db("INSERT INTO users (username, email, password_hash, company_id, role) VALUES (?, ?, ?, ?, ?)",
                       (username, email, password_hash, company_id, role))
            flash("User created successfully.", "success")
            return redirect(url_for('list_users'))
    return render_template('edit_user.html', user=None, companies=companies, notes=[])

@app.route('/settings/user/<int:user_id>/edit', methods=['GET', 'POST'])
def edit_user(user_id):
    user = query_db("SELECT u.*, c.name as company_name FROM users u JOIN companies c ON u.company_id = c.id WHERE u.id = ?", [user_id], one=True)
    companies = query_db("SELECT id, name FROM companies ORDER BY name")
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        company_id = request.form.get('company_id')
        role = request.form.get('role')

        if not all([username, email, company_id, role]):
            flash("Username, email, company, and role are required.", "error")
        else:
            if password:
                password_hash = generate_password_hash(password)
                execute_db("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

            execute_db("UPDATE users SET username = ?, email = ?, company_id = ?, role = ? WHERE id = ?",
                       (username, email, company_id, role, user_id))
            flash("User updated successfully.", "success")
            return redirect(url_for('list_users'))

    notes = query_db("SELECT * FROM user_notes WHERE user_id = ? ORDER BY created_at DESC", [user_id])
    return render_template('edit_user.html', user=user, companies=companies, notes=notes)

@app.route('/settings/user/<int:user_id>/notes/add', methods=['POST'])
def add_user_note(user_id):
    content = request.form.get('content')
    if content:
        execute_db("INSERT INTO user_notes (user_id, content, created_at) VALUES (?, ?, ?)",
                   (user_id, content, datetime.now().isoformat()))
        flash("Note added.", "success")
    return redirect(url_for('edit_user', user_id=user_id))

@app.route('/settings/user/notes/<int:note_id>/delete', methods=['POST'])
def delete_user_note(note_id):
    note = query_db("SELECT user_id FROM user_notes WHERE id = ?", [note_id], one=True)
    if note:
        execute_db("DELETE FROM user_notes WHERE id = ?", [note_id])
        flash("Note deleted.", "success")
        return redirect(url_for('edit_user', user_id=note['user_id']))
    return redirect(url_for('list_users'))


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

@app.route('/chat', methods=['POST'])
def chat():
    context = request.json.get('context')
    question = request.json.get('question')
    response = chat_with_context(context, question)
    return jsonify({'response': response})

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
