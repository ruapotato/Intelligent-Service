import os
import sys
import subprocess
from datetime import datetime
from database import get_db_connection

def run_job(job_id, script_path, password):
    """Runs a sync script as a subprocess and logs the result."""
    print(f"[{datetime.now()}] SCHEDULER: Running job '{job_id}': {script_path}")
    log_output, status = "", "Failure"
    try:
        python_executable = sys.executable
        env = os.environ.copy()
        env['DB_MASTER_PASSWORD'] = password
        result = subprocess.run(
            [python_executable, script_path],
            capture_output=True, text=True, check=False, timeout=300,
            encoding='utf-8', errors='replace', env=env
        )
        log_output = f"--- STDOUT ---\n{result.stdout}\n\n--- STDERR ---\n{result.stderr}"
        if result.returncode == 0:
            status = "Success"
        print(f"[{datetime.now()}] SCHEDULER: Finished job '{job_id}' with status: {status}")
    except Exception as e:
        log_output = f"Scheduler failed to run script: {e}"
        print(f"[{datetime.now()}] SCHEDULER: FATAL ERROR running job '{job_id}': {e}", file=sys.stderr)
    finally:
        try:
            with get_db_connection(password) as con:
                con.execute("UPDATE scheduler_jobs SET last_run = ?, last_status = ?, last_run_log = ? WHERE id = ?",
                            (datetime.now().isoformat(timespec='seconds'), status, log_output, job_id))
                con.commit()
        except Exception as e:
            print(f"[{datetime.now()}] SCHEDULER: Failed to log job result to DB: {e}", file=sys.stderr)
