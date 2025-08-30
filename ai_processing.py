import requests
import json
import os
import sys
import getpass
from database import get_db_connection

DB_FILE = "tickets.db"

def get_ollama_endpoint(db_password):
    """Reads the Ollama endpoint from the database."""
    try:
        con = get_db_connection(DB_FILE, db_password)
        cur = con.cursor()
        cur.execute("SELECT api_endpoint FROM api_keys WHERE service = 'ollama'")
        creds = cur.fetchone()
        con.close()
        if not creds:
            raise ValueError("Ollama endpoint not found in the database.")
        return creds[0]
    except Exception as e:
        print(f"Database error while fetching Ollama endpoint: {e}", file=sys.stderr)
        return None

OLLAMA_ENDPOINT = None

def get_endpoint():
    global OLLAMA_ENDPOINT
    if OLLAMA_ENDPOINT is None:
        DB_MASTER_PASSWORD = os.environ.get('DB_MASTER_PASSWORD')
        if not DB_MASTER_PASSWORD:
            try:
                DB_MASTER_PASSWORD = getpass.getpass("Please enter the database password for AI processing: ")
            except (getpass.GetPassWarning, NameError):
                 DB_MASTER_PASSWORD = input("Please enter the database password for AI processing: ")
        OLLAMA_ENDPOINT = get_ollama_endpoint(DB_MASTER_PASSWORD)
    return OLLAMA_ENDPOINT

def summarize_text(text):
    """Summarizes text using the Ollama Mistral model."""
    endpoint = get_endpoint()
    if not endpoint:
        return "Ollama endpoint not configured."

    prompt = f"Summarize the following text:\n\n{text}"
    try:
        response = requests.post(
            f"{endpoint}/api/generate",
            json={"model": "mistral", "prompt": prompt},
            stream=True
        )
        response.raise_for_status()
        summary = ""
        for line in response.iter_lines():
            if line:
                decoded_line = json.loads(line.decode('utf-8'))
                summary += decoded_line.get("response", "")
        return summary
    except requests.exceptions.RequestException as e:
        return f"Error communicating with Ollama: {e}"

def sanitize_text(text):
    """Sanitizes text by removing PII using the Ollama Mistral model."""
    endpoint = get_endpoint()
    if not endpoint:
        return "Ollama endpoint not configured."
    prompt = f"Remove all personally identifiable information (PII) from the following text, replacing it with placeholders like [NAME], [EMAIL], [PHONE], etc.:\n\n{text}"
    try:
        response = requests.post(
            f"{endpoint}/api/generate",
            json={"model": "mistral", "prompt": prompt},
            stream=True
        )
        response.raise_for_status()
        sanitized_text = ""
        for line in response.iter_lines():
            if line:
                decoded_line = json.loads(line.decode('utf-8'))
                sanitized_text += decoded_line.get("response", "")
        return sanitized_text
    except requests.exceptions.RequestException as e:
        return f"Error communicating with Ollama: {e}"
