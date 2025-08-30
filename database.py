import os
import sys
from flask import g, current_app
from datetime import datetime, timezone

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    print("Error: sqlcipher3-wheels is not installed. Please install it using: pip install sqlcipher3-wheels", file=sys.stderr)
    sys.exit(1)

DATABASE = 'tickets.db'

def get_db_connection(password):
    """Establishes a connection to the encrypted database."""
    if not password:
        raise ValueError("A database password is required.")
    con = sqlite3.connect(DATABASE, timeout=10)
    con.execute(f"PRAGMA key = '{password}';")
    con.row_factory = sqlite3.Row
    return con

def get_db():
    """Opens a new database connection for the Flask app context."""
    if not hasattr(g, '_database'):
        password = current_app.config.get('DB_PASSWORD')
        if not password:
            raise ValueError("Database password not found in app config.")
        try:
            g._database = get_db_connection(password)
        except sqlite3.DatabaseError:
            g._database = None
            raise ValueError("Invalid master password.")
    return g._database

def close_connection(exception):
    """Closes the database connection at the end of the request."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def query_db(query, args=(), one=False):
    """Queries the database and returns a list of dictionaries."""
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return rv[0] if rv and one else rv

def execute_db(query, args=()):
    """Executes a database write operation within the Flask app context."""
    db = get_db()
    try:
        cur = db.execute(query, args)
        db.commit()
        return cur
    except Exception as e:
        db.rollback()
        raise e

def init_app_db(app):
    """Register database functions with the Flask app."""
    app.teardown_appcontext(close_connection)
