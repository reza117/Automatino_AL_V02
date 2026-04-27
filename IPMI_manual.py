import pandas as pd
import sqlite3
import subprocess
import os

# --- CONFIGURATION ---
VERIFIED_CSV = 'list_verified.csv'
DB_NAME = 'wdc_inventory.db'

def run_command(cmd):
    try:
        result = subprocess.check_output(cmd, stderr=subprocess.STDOUT, shell=True, timeout=30).decode()
        return True, result
    except Exception as e:
        return False, str(e)

def update_db(column, value, ipmi_ip):
    """Updates the inventory database tracking columns."""
    conn = sqlite3.connect(DB_NAME)
    # Ensure the table exists with basic structure if not present
    conn.execute('CREATE TABLE IF NOT EXISTS server_tracking (ipmi_ip TEXT PRIMARY KEY, mgmt_ip TEXT, serial TEXT, ipmi_mac TEXT, disk_inventory TEXT, wipe_status TEXT)')
    
    # Use REPLACE/INSERT logic to keep tracking data safe
    conn.execute(f'''
        INSERT INTO server_tracking (ipmi_ip, {column}) 
        VALUES (?, ?) 
        ON CONFLICT(ipmi_ip) DO UPDATE SET {column} = excluded.{column}
    ''', (ipmi_ip, value))
    conn.commit()
    conn.close()

def get_server_creds(ipmi_ip):
    """Retrieves IPMI credentials from the Orchestrator's verified CSV."""
    if not os.path.exists(VERIFIED_CSV):
        print(f"[!] {VERIFIED_CSV} not found. Run Orchestrator first!")
        return None
    
    df = pd.read_csv(VERIFIED_CSV)
    match = df[(df['IP'] == ipmi_ip) & (df['Status'] == 'Success')]
    
    if not match.empty:
        return {
            "ip": match.iloc[0]['IP'],
            "user": match.iloc[0]['Username'],
            "pw": match.iloc[0]['Password']
        }
    return None

def manual_ipmi_menu(creds):
    ip, user, pw = creds['ip'], creds['user'], creds['pw']
    while True:
        print(f"\n--- [IPMI CONTROL: {ip}] ---")
        print("1. Set PXE Boot")
        print("2. Verify PXE Flag")
        print("3. Power Reset (Reboot)")
        print("b. Back")
        
        choice = input("\nSelect Action: ")
        if choice == '1':
            cmd = f"ipmitool -I lanplus -H {ip} -U {user} -P '{pw}' chassis bootdev pxe"
            success, msg = run_command(cmd)
            print("[OK] PXE Set" if success else f"[FAIL] {msg}")
        elif choice == '2':
            cmd = f"ipmitool -I lanplus -H {ip} -U {user} -P '{pw}' chassis bootparam get 5"
            success, msg = run_command(cmd)
            print("[VERIFIED] PXE Active" if (success and "pxe" in msg.lower()) else f"[FAIL] {msg}")
        elif choice == '3':
            if input(f"Confirm Reboot {ip}? (y/n): ").lower() == 'y':
                run_command(f"ipmitool -I lanplus -H {ip} -U {user} -P '{pw}' chassis power reset")
                print("[OK] Reset sent.")
        elif choice == 'b':
            break

def ssh_inventory_and_wipe(ipmi_ip):
    mgmt_ip = input("\n[?] Enter OS Management IP: ")
    ssh_user = "user" # Your default SSH user
    
    print(f"[*] Starting Inventory on {mgmt_ip}...")
    update_db("mgmt_ip", mgmt_ip, ipmi_ip)

    # 1. Gather Hardware Info
    commands = {
        "serial": "sudo dmidecode -s system-serial-number",
        "ipmi_mac": "sudo ipmitool lan print 1 | grep 'MAC Address' | awk '{print $4}'",
        "disk_inventory": "lsblk -dn -o NAME,SERIAL,SIZE,MODEL"
    }
    
    for col, cmd in commands.items():
        ssh_cmd = f"ssh -o StrictHostKeyChecking=no {ssh_user}@{mgmt_ip} \"{cmd}\""
        success, out = run_command(ssh_cmd)
        if success:
            update_db(col, out.strip(), ipmi_ip)
            print(f"    [+] Recorded {col}")

    # 2. Deploy WDC Bootstrap
    print(f"[*] Deploying wdc_bootstrap.sh to {mgmt_ip}...")
    run_command(f"scp wdc_bootstrap.sh {ssh_user}@{mgmt_ip}:~/wdc_bootstrap.sh")
    
    # 3. TMUX Launch
    if input(f"\n[!!!] Start WIPE on {mgmt_ip}? (y/n): ").lower() == 'y':
        # Launched in background; user must have NOPASSWD sudo set up
        remote_exec = "chmod +x ~/wdc_bootstrap.sh && tmux new-session -d -s wipe_session 'sudo ./wdc_bootstrap.sh'"
        success, _ = run_command(f"ssh {ssh_user}@{mgmt_ip} \"{remote_exec}\"")
        if success:
            update_db("wipe_status", "Running_in_TMUX", ipmi_ip)
            print("\n[SUCCESS] Wipe running in TMUX. Process takes ~25 hours.")

def main():
    while True:
        print("\n" + "="*45)
        print("   WDC FLEET MANAGEMENT TOOL")
        print("="*45)
        print("1. IPMI Control (via Verified CSV)")
        print("2. OS Inventory & Wipe (via SSH/TMUX)")
        print("q. Exit")
        
        choice = input("\nSelect: ")
        if choice == '1':
            target = input("Enter IPMI IP: ")
            creds = get_server_creds(target)
            if creds:
                manual_ipmi_menu(creds)
            else:
                print(f"[!] No success record for {target} in {VERIFIED_CSV}")
        elif choice == '2':
            target = input("Enter IPMI IP to associate: ")
            if get_server_creds(target):
                ssh_inventory_and_wipe(target)
            else:
                print(f"[!] IPMI IP not verified. Run Orchestrator first.")
        elif choice == 'q':
            break

if __name__ == "__main__":
    main()
