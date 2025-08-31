import sys
import os
import getpass
import time
import shutil
from werkzeug.security import generate_password_hash
from datetime import datetime

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    print("Error: sqlcipher3-wheels is not installed. Please install it using: pip install sqlcipher3-wheels", file=sys.stderr)
    sys.exit(1)


DB_FILE = "tickets.db"

def extract_keys_from_existing_db(password):
    """
    Connects to the EXISTING tickets.db, extracts API keys, and returns them.
    """
    print(f"[*] Connecting to the existing '{DB_FILE}' to extract API keys...")
    try:
        con = sqlite3.connect(DB_FILE)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(f"PRAGMA key = '{password}';")
        # Test the key to make sure it's correct
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys';")
        if cur.fetchone() is None:
            print(f"[!] 'api_keys' table not found in '{DB_FILE}'. Cannot migrate keys.", file=sys.stderr)
            return None

        cur.execute("SELECT service, api_key, api_endpoint FROM api_keys;")
        keys = cur.fetchall()
        con.close()
        if not keys:
            print("[!] No API keys found in the existing database.")
            return None
        print("[*] Successfully extracted API keys.")
        return [dict(key) for key in keys]
    except sqlite3.DatabaseError:
        print(f"\n[!] Incorrect password for '{DB_FILE}'. Unable to migrate keys.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"\n[!] An error occurred while reading the existing database: {e}", file=sys.stderr)
        return None

def create_database(password, imported_keys=None):
    """
    Initializes a new encrypted database, creates the schema, and populates it.
    """
    if not password:
        print("Error: A master password is required for the new database.", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute(f"PRAGMA key = '{password}';")
    cur.execute("PRAGMA foreign_keys = ON;")

    print("\n[*] Creating new database schema...")
    # --- Schema Definition ---
    cur.execute("CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'Client', company_id INTEGER NOT NULL,
            FOREIGN KEY (company_id) REFERENCES companies (id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Open',
            priority TEXT NOT NULL DEFAULT 'Low', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            company_id INTEGER, user_id INTEGER, assigned_to_id INTEGER, summary TEXT,
            FOREIGN KEY (company_id) REFERENCES companies (id), FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (assigned_to_id) REFERENCES users (id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, author_id INTEGER,
            content TEXT NOT NULL, created_at TEXT NOT NULL, is_internal_note BOOLEAN DEFAULT 0,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id) ON DELETE CASCADE,
            FOREIGN KEY (author_id) REFERENCES users (id)
        )
    """)

    cur.execute("CREATE TABLE IF NOT EXISTS company_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE)")
    cur.execute("CREATE TABLE IF NOT EXISTS user_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE)")

    cur.execute("CREATE TABLE IF NOT EXISTS api_keys (service TEXT PRIMARY KEY, api_key TEXT, api_endpoint TEXT)")
    cur.execute("""
        CREATE TABLE scheduler_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_name TEXT NOT NULL UNIQUE, script_path TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL, enabled BOOLEAN NOT NULL CHECK (enabled IN (0, 1)),
            last_run TEXT, last_status TEXT, last_run_log TEXT
        )
    """)
    print("[*] Schema creation complete.")

    # --- Data Population ---
    print("\n[*] Populating with default data...")
    # Default companies
    cur.execute("INSERT INTO companies (name) VALUES (?)", ('Unknown',))
    cur.execute("INSERT INTO companies (name) VALUES (?)", ('Internal',))
    internal_company_id = cur.lastrowid
    print("    - Populated default companies.")

    # Default users
    admin_pass_hash = generate_password_hash('admin')
    cur.execute("INSERT INTO users (username, email, password_hash, role, company_id) VALUES (?, ?, ?, ?, ?)",
                ('admin', 'admin@local.host', admin_pass_hash, 'Admin', internal_company_id))
    print("    - Populated default admin user (login: admin/admin).")


    default_jobs = [('Email Watcher', 'email_watcher.py', 1, 1)]
    cur.executemany("INSERT INTO scheduler_jobs (job_name, script_path, interval_minutes, enabled) VALUES (?, ?, ?, ?)", default_jobs)
    print("    - Populated default scheduler jobs.")

    cur.execute("INSERT INTO company_notes (company_id, content, created_at) VALUES (?, ?, ?)", (1, 'Default company for new, unrecognized emails.', datetime.now().isoformat()))


    if imported_keys:
        print("[*] Importing existing API keys...")
        for key in imported_keys:
            cur.execute("INSERT OR REPLACE INTO api_keys (service, api_key, api_endpoint) VALUES (?, ?, ?)",
                        (key['service'], key.get('api_key'), key.get('api_endpoint')))
        print("[*] API keys have been successfully migrated.")
    else:
        get_and_set_api_keys(cur)

    con.commit()
    con.close()

def get_and_set_api_keys(cursor):
    """Prompts the user for API keys and saves them to the database."""
    print("\nPlease enter your API credentials for the ticketing system.")
    imap_server = input("  - IMAP Server (e.g., imap.gmail.com): ")
    imap_user = input("  - IMAP User (e.g., your.email@example.com): ")
    imap_password = getpass.getpass("  - IMAP Password: ")
    ollama_endpoint = input("  - Ollama API Endpoint (e.g., http://localhost:11434): ")

    if not all([imap_server, imap_user, imap_password, ollama_endpoint]):
        print("[!] Error: All API credentials are required.", file=sys.stderr)
        sys.exit(1)

    cursor.execute("DELETE FROM api_keys;")
    cursor.execute("INSERT INTO api_keys (service, api_key, api_endpoint) VALUES (?, ?, ?)",
                   ("imap", f"{imap_user}:{imap_password}", imap_server))
    cursor.execute("INSERT INTO api_keys (service, api_endpoint) VALUES (?, ?)",
                   ("ollama", ollama_endpoint))
    print("[*] API keys have been saved.")


if __name__ == "__main__":
    print("--- Ticketing System Database Setup ---")
    imported_api_keys = None

    if os.path.exists(DB_FILE):
        print(f"\n[!] Existing database file ('{DB_FILE}') found.")
        reinitialize = input("    - Do you want to re-initialize it (this will back up and replace the current file)? (y/n): ").lower()
        if reinitialize == 'y':
            old_password = getpass.getpass("    - Enter the password for the EXISTING database to migrate its keys: ")
            imported_api_keys = extract_keys_from_existing_db(old_password)

            if imported_api_keys is not None:
                try:
                    backup_filename = f"{DB_FILE}.{int(time.time())}.bak"
                    shutil.move(DB_FILE, backup_filename)
                    print(f"[*] Backed up existing database to '{backup_filename}'")
                except Exception as e:
                    print(f"[!] Could not back up existing database: {e}", file=sys.stderr)
                    sys.exit(1)

                new_password = getpass.getpass("\nEnter a master password for the NEW database: ")
                create_database(new_password, imported_keys=imported_api_keys)
                print(f"\n✅ Success! New encrypted database '{DB_FILE}' created and populated with migrated keys.")
            else:
                print("[!] Halting initialization due to key extraction failure.")
        else:
            print("[*] Exiting without making changes.")
    else:
        print("\n[*] No existing database found.")
        new_password = getpass.getpass("    - Enter a master password for the NEW database: ")
        create_database(new_password)
        print(f"\n✅ Success! New encrypted database '{DB_FILE}' created and configured.")
