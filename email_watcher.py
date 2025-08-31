import imap_tools
import re
import os
import sys
import getpass
from datetime import datetime
from imap_tools import MailBox, A

try:
    from sqlcipher3 import dbapi2 as sqlite3
except ImportError:
    sys.exit("Error: sqlcipher3-wheels is not installed. Please install it using: pip install sqlcipher3-wheels")


DB_FILE = "tickets.db"

# Standalone DB connection function for scripts
def get_script_db_connection(password):
    if not password: raise ValueError("A database password is required.")
    con = sqlite3.connect(DB_FILE, timeout=10)
    con.execute(f"PRAGMA key = '{password}';")
    con.row_factory = sqlite3.Row
    return con

def get_creds_from_db(db_password):
    """Reads credentials from the encrypted database."""
    try:
        with get_script_db_connection(db_password) as con:
            cur = con.cursor()
            cur.execute("SELECT api_key, api_endpoint FROM api_keys WHERE service = 'imap'")
            creds = cur.fetchone()
            if not creds:
                raise ValueError("IMAP credentials not found in the database.")
            imap_user, imap_password = creds['api_key'].split(":", 1)
            return creds['api_endpoint'], imap_user, imap_password
    except sqlite3.Error as e:
        sys.exit(f"Database error while fetching credentials: {e}. Is the password correct?")

def process_new_emails(db_password):
    """
    Connects to the mailbox, fetches unread emails, and creates or updates tickets.
    """
    imap_server, imap_user, imap_password = get_creds_from_db(db_password)
    print(f"[*] Connecting to mailbox for {imap_user}...")

    try:
        with MailBox(imap_server).login(imap_user, imap_password) as mailbox:
            found_emails = False
            for msg in mailbox.fetch(A(seen=False), limit=10):
                found_emails = True
                print("\n--- NEW EMAIL FOUND ---")
                print(f"  From:    {msg.from_}")
                print(f"  Subject: {msg.subject}")
                print(f"  Date:    {msg.date_str}")
                print("-----------------------")

                ticket_id_match = re.search(r'\[Ticket #(\d+)\]', msg.subject)

                with get_script_db_connection(db_password) as con:
                    # Find or create the user and company
                    user_email = msg.from_
                    user = con.execute("SELECT * FROM users WHERE email = ?", (user_email,)).fetchone()
                    if not user:
                        # User doesn't exist, create them in the "Unknown" company
                        unknown_company = con.execute("SELECT id FROM companies WHERE name = 'Unknown'").fetchone()
                        if not unknown_company:
                            # This should not happen if init_db.py is run correctly
                            sys.exit("FATAL: 'Unknown' company not found in the database.")
                        
                        cur = con.cursor()
                        # *** BUG FIX HERE ***
                        # Provide a non-null, unusable password hash for new client users.
                        placeholder_hash = '<no-password-set>'
                        cur.execute("INSERT INTO users (username, email, company_id, role, password_hash) VALUES (?, ?, ?, ?, ?)",
                                     (user_email, user_email, unknown_company['id'], 'Client', placeholder_hash))
                        user_id = cur.lastrowid
                        con.commit()
                        user = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                        print(f"  -> Created new user '{user_email}' in 'Unknown' company.")

                    if ticket_id_match:
                        ticket_id = int(ticket_id_match.group(1))
                        con.execute("INSERT INTO ticket_replies (ticket_id, author_id, content, created_at) VALUES (?, ?, ?, ?)",
                                   (ticket_id, user['id'], msg.text or msg.html, msg.date.isoformat()))
                        con.commit()
                        print(f"  -> Added reply to ticket #{ticket_id} from user {user['username']}")
                    else:
                        now = datetime.now().isoformat()
                        cur = con.cursor()
                        cur.execute("INSERT INTO tickets (subject, created_at, updated_at, company_id, user_id) VALUES (?, ?, ?, ?, ?)",
                                     (msg.subject, now, now, user['company_id'], user['id']))
                        new_ticket_id = cur.lastrowid
                        con.execute("INSERT INTO ticket_replies (ticket_id, author_id, content, created_at) VALUES (?, ?, ?, ?)",
                                   (new_ticket_id, user['id'], msg.text or msg.html, msg.date.isoformat()))
                        new_subject = f"[Ticket #{new_ticket_id}] {msg.subject}"
                        con.execute("UPDATE tickets SET subject = ? WHERE id = ?", (new_subject, new_ticket_id))
                        con.commit()
                        print(f"  -> Created new ticket #{new_ticket_id} for user {user['username']} in company ID {user['company_id']}")


            if found_emails:
                print("\n[*] Email processing complete. Found emails were marked as read.")
            else:
                print("\n[+] No unread emails found.")

    except Exception as e:
        print(f"\n[!] An error occurred during email processing: {e}")


if __name__ == "__main__":
    DB_MASTER_PASSWORD = os.environ.get('DB_MASTER_PASSWORD')
    if not DB_MASTER_PASSWORD:
        try:
            DB_MASTER_PASSWORD = getpass.getpass("Please enter the database password: ")
        except (getpass.GetPassWarning, NameError):
             DB_MASTER_PASSWORD = input("Please enter the database password: ")
    if not DB_MASTER_PASSWORD:
        sys.exit("FATAL: No database password provided. Aborting.")
    process_new_emails(DB_MASTER_PASSWORD)
