import sqlite3
import subprocess
import concurrent.futures
import os

DB_NAME = 'wdc_inventory.db'

def run_ipmi(ip, user, pw, args):
    cmd = ["ipmitool", "-I", "lanplus", "-H", ip, "-U", user, "-P", pw] + args
    try:
        result = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10).decode()
        return True, result
    except Exception as e:
        return False, str(e)

def task_power_on(row):
    ip, user, pw = row
    # 'power on' only starts the server if it is currently off
    success, msg = run_ipmi(ip, user, pw, ["chassis", "power", "on"])
    status = "On_Sent" if success else "Failed"
    # We will reuse the reboot_sent column or you can add a new one if you prefer
    update_db("reboot_sent", status, ip)


def task_set_pxe(row):
    ip, user, pw = row
    success, msg = run_ipmi(ip, user, pw, ["chassis", "bootdev", "pxe"])
    status = "Success" if success else "Failed"
    update_db("pxe_set", status, ip)


def task_verify_pxe(row):
    ip, user, pw = row
    success, msg = run_ipmi(ip, user, pw, ["chassis", "bootparam", "get", "5"])
    # Case-insensitive check for 'pxe' in the response
    status = "Verified" if (success and "pxe" in msg.lower()) else "Failed"
    update_db("pxe_verify", status, ip)

    if status == "Failed":
        # This helps us see why it isn't verifying
        print(f"DEBUG: {ip} fail. Msg: {msg.strip()[:50]}")




def task_reboot(row):
    ip, user, pw = row
    success, msg = run_ipmi(ip, user, pw, ["chassis", "power", "reset"])
    status = "Sent" if success else "Failed"
    update_db("reboot_sent", status, ip)

def update_db(column, value, ip):
    conn = sqlite3.connect(DB_NAME)
    conn.execute(f"UPDATE servers SET {column} = ? WHERE IP = ?", (value, ip))
    conn.commit()
    conn.close()

def get_targets(filter_col=None, filter_val=None):
    conn = sqlite3.connect(DB_NAME)
    query = "SELECT IP, Username, Password FROM servers WHERE Status = 'Success'"
    if filter_col:
        query += f" AND {filter_col} = '{filter_val}'"
    data = conn.execute(query).fetchall()
    conn.close()
    return data

def show_stats():
    conn = sqlite3.connect(DB_NAME)
    total = conn.execute("SELECT count(*) FROM servers WHERE Status = 'Success'").fetchone()[0]
    pxe_ok = conn.execute("SELECT count(*) FROM servers WHERE pxe_set = 'Success'").fetchone()[0]
    ver_ok = conn.execute("SELECT count(*) FROM servers WHERE pxe_verify = 'Verified'").fetchone()[0]
    reb_ok = conn.execute("SELECT count(*) FROM servers WHERE reboot_sent = 'Sent'").fetchone()[0]
    conn.close()
    print(f"\n--- FLEET STATUS (Targets: {total}) ---")
    print(f"1. PXE Set:     [{pxe_ok}/{total}]")
    print(f"2. PXE Verified: [{ver_ok}/{total}]")
    print(f"3. Reboot Sent: [{reb_ok}/{total}]")
    print("-" * 30)

def main_menu():
    while True:
        show_stats()
        print("0. [Action] Bulk Power ON (Cold Boot)")
        print("1. [Action] Set all to PXE")
        print("2. [Action] Verify PXE Flags (Required before reboot)")
        print("3. [Action] Bulk Power Reset (Reboot)")
        print("q. Exit")
        choice = input("\nSelect Action: ")
        if choice == '0':
            targets = get_targets("pxe_verify", "Verified")
            print(f"[*] Sending Power ON to {len(targets)} verified servers...")
            # We still stagger these slightly (10 at a time) to avoid power spikes
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as e:
                e.map(task_power_on, targets)
        if choice == '1':
            targets = get_targets()
            with concurrent.futures.ThreadPoolExecutor(max_workers=200) as e: e.map(task_set_pxe, targets)
        elif choice == '2':
            targets = get_targets("pxe_set", "Success")
            with concurrent.futures.ThreadPoolExecutor(max_workers=200) as e: e.map(task_verify_pxe, targets)
        elif choice == '3':
            # Safety Gate: Only reboot if verification passed
            targets = get_targets("pxe_verify", "Verified")
            if not targets:
                print("No verified servers found. Run Option 2 first!")
                continue
            confirm = input(f"Confirm STAGGERED reboot of {len(targets)} servers? (y/n): ")
            if confirm.lower() == 'y':
                import time
                batch_size = 5   # Number of servers to boot at once
                wait_time = 120   # Seconds to wait for downloads to finish
                # Split targets into smaller groups
                for i in range(0, len(targets), batch_size):
                    batch = targets[i : i + batch_size]
                    current_batch_num = (i // batch_size) + 1
                    print(f"\n[*] Starting Batch {current_batch_num}...")
                    # Run the reboot for just this batch
                    with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as e:
                        e.map(task_reboot, batch)
                    # Don't wait after the very last batch
                    if i + batch_size < len(targets):
                        print(f"[!] Batch {current_batch_num} triggered. Waiting {wait_time}s for network load to drop before next batch...")
                        time.sleep(wait_time)
                print("\n[***] All batches triggered successfully.") 
        elif choice == 'q':
            break
if __name__ == "__main__":
    main_menu()
