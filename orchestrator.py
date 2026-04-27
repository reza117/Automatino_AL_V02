import pandas as pd
import sqlite3
import subprocess
import concurrent.futures
import os

# --- CONFIGURATION ---
CSV_INPUT = 'list.csv'
DB_NAME = 'wdc_inventory.db'
CSV_OUTPUT = 'list_verified.csv'

# Credentials to test
CREDS = [
    ('Wigner', 'Wigner'),
    ('root', 'root'),
    ('admin', 'admin'),
    ('Wigner', 'WignerALICEAF'),
    ('root', 'WignerALICEAF'),
    ('root', 'Wigner'),
    ('root', 'wigner'),
    ('ADMIN', 'ADMIN')
]

def init_db():
    # Load CSV and move to SQLite
    df = pd.read_csv(CSV_INPUT)
    # Ensure columns C and D exist for results
    df['Username'] = ""
    df['Password'] = ""
    df['Status'] = "Pending"
    
    conn = sqlite3.connect(DB_NAME)
    df.to_sql('servers', conn, if_exists='replace', index=False)
    conn.close()
    print(f"[*] Database initialized with {len(df)} servers.")

def test_ipmi(ip):
    for user, pw in CREDS:
        # We run a simple chassis status check with a 5-second timeout
        cmd = [
            "ipmitool", "-I", "lanplus", "-H", ip, 
            "-U", user, "-P", pw, "chassis", "status"
        ]
        try:
            # check_call returns 0 if success, raises CalledProcessError otherwise
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            return user, pw, "Success"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return None, None, "Failed"

def worker(ip):
    user, pw, status = test_ipmi(ip)
    
    # Update SQLite (Thread-safe connection)
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE servers 
        SET Username = ?, Password = ?, Status = ? 
        WHERE "IP" = ?
    """, (user, pw, status, ip))
    conn.commit()
    conn.close()
    print(f"[+] Processed {ip}: {status}")

def run_orchestrator():
    conn = sqlite3.connect(DB_NAME)
    # Fetch all IPs that need checking
    ips = [row[0] for row in conn.execute('SELECT "IP" FROM servers').fetchall()]
    conn.close()

    print(f"[*] Starting concurrent check on {len(ips)} IPs...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=200) as executor:
        executor.map(worker, ips)

    # Export final result back to CSV
    conn = sqlite3.connect(DB_NAME)
    final_df = pd.read_sql_query("SELECT * FROM servers", conn)
    final_df.to_csv(CSV_OUTPUT, index=False)
    conn.close()
    print(f"[*] Phase One Complete. Results saved to {CSV_OUTPUT}")

if __name__ == "__main__":
    if not os.path.exists(CSV_INPUT):
        print(f"Error: {CSV_INPUT} not found!")
    else:
        init_db()
        run_orchestrator()
