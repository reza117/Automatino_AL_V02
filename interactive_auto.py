import csv
import concurrent.futures
import ipaddress
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = "interactive_auto_config.json"
ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"


def load_config(config_path: str = CONFIG_PATH) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def color_cred_state(state: str) -> str:
    if state == "Success":
        return f"{ANSI_GREEN}{state}{ANSI_RESET}"
    if state == "Failed":
        return f"{ANSI_RED}{state}{ANSI_RESET}"
    return state


def ensure_sudo() -> None:
    if os.geteuid() == 0:
        return
    print("[*] Sudo is required for nmap UDP discovery. Elevating...")
    os.execvp("sudo", ["sudo", "-E", sys.executable, *sys.argv])


def config_ssh_key(cfg: dict) -> str | None:
    configured = cfg.get("ssh", {}).get("identity_file", "")
    configured = configured.strip()
    if not configured:
        return None
    path = os.path.expanduser(configured)
    if os.path.exists(path):
        return path
    return None


def ssh_base_cmd(key_path: str | None) -> list[str]:
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
    if key_path:
        cmd += ["-i", key_path]
    return cmd


def countdown_sleep(seconds: int, label: str) -> None:
    if seconds <= 0:
        return
    for remain in range(seconds, 0, -1):
        print(f"\r[*] {label}... {remain:>2}s ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 48 + "\r", end="", flush=True)


def ssh_exec(host: str, user: str, key_path: str | None, remote_cmd: str, timeout: int = 30) -> tuple[bool, str]:
    cmd = ssh_base_cmd(key_path) + [f"{user}@{host}", remote_cmd]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout).decode()
        return True, out
    except Exception as e:
        return False, str(e)


def scp_upload(host: str, user: str, key_path: str | None, local_path: str, remote_path: str, timeout: int = 60) -> tuple[bool, str]:
    cmd = ["scp", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]
    if key_path:
        cmd += ["-i", key_path]
    cmd += [local_path, f"{user}@{host}:{remote_path}"]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout).decode()
        return True, out
    except Exception as e:
        return False, str(e)


def ensure_data_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def get_conn(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ipmi_inventory (
            ipmi_ip TEXT PRIMARY KEY,
            ipmi_name TEXT,
            username TEXT,
            password TEXT,
            status TEXT DEFAULT 'Pending',
            remark TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.commit()


def init_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS server_tracking (
            ipmi_ip TEXT PRIMARY KEY,
            mgmt_ip TEXT,
            serial TEXT,
            ipmi_mac TEXT,
            disk_inventory TEXT,
            wipe_status TEXT,
            updated_at TEXT
        )
        """
    )
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(server_tracking)").fetchall()
    }
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE server_tracking ADD COLUMN updated_at TEXT")
    if "deploy_stage" not in cols:
        conn.execute("ALTER TABLE server_tracking ADD COLUMN deploy_stage TEXT")
    if "deploy_result" not in cols:
        conn.execute("ALTER TABLE server_tracking ADD COLUMN deploy_result TEXT")
    if "deploy_detail" not in cols:
        conn.execute("ALTER TABLE server_tracking ADD COLUMN deploy_detail TEXT")
    now = now_utc_iso()
    conn.execute(
        """
        UPDATE server_tracking
        SET updated_at = ?
        WHERE updated_at IS NULL AND COALESCE(wipe_status, '') <> ''
        """,
        (now,),
    )
    conn.commit()


def seed_from_csv_if_missing(conn: sqlite3.Connection, csv_path: str) -> int:
    if not os.path.exists(csv_path):
        return 0

    inserted = 0
    now = now_utc_iso()
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ip = (row.get("IP") or "").strip()
            name = (row.get("NAME") or "").strip()
            if not ip:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO ipmi_inventory
                (ipmi_ip, ipmi_name, remark, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ip, name, "Source:CSV", now, now),
            )
            if conn.total_changes > inserted:
                inserted += 1
    conn.commit()
    return inserted


def classify_ipmi_range(ip: str, ipmi_ranges: dict) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""

    for label, cidr in ipmi_ranges.items():
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return label
        except ValueError:
            continue
    return ""


def apply_ipmi_range_labels(conn: sqlite3.Connection, ipmi_ranges: dict) -> int:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ipmi_ip, ipmi_name, remark FROM ipmi_inventory ORDER BY ipmi_ip"
    ).fetchall()

    updated = 0
    now = now_utc_iso()
    for row in rows:
        label = classify_ipmi_range(row["ipmi_ip"], ipmi_ranges)
        if not label:
            continue

        current_name = (row["ipmi_name"] or "").strip()
        new_name = current_name or label
        remark = (row["remark"] or "").strip()
        marker = f"Range:{label}"
        if marker not in remark:
            new_remark = marker if not remark else f"{remark} | {marker}"
        else:
            new_remark = remark

        if new_name != current_name or new_remark != remark:
            conn.execute(
                """
                UPDATE ipmi_inventory
                SET ipmi_name = ?, remark = ?, updated_at = ?
                WHERE ipmi_ip = ?
                """,
                (new_name, new_remark, now, row["ipmi_ip"]),
            )
            updated += 1

    conn.commit()
    return updated


def _parse_nmap_grepable_output(output: str, port: int | None = None, ping_mode: bool = False) -> list[str]:
    discovered: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Host:"):
            continue
        if ping_mode:
            if "Status: Up" not in line:
                continue
        else:
            marker = f"{port}/open"
            if marker not in line:
                continue
        parts = line.split()
        if len(parts) >= 2:
            ip = parts[1].strip()
            discovered.append(ip)
    return sorted(set(discovered))


def scan_ipmi_ranges_with_nmap(
    conn: sqlite3.Connection,
    ipmi_ranges: dict,
    scan_mode: str,
    scan_port: int | None,
) -> dict:
    if not ipmi_ranges:
        print("[!] No ipmi_ranges found in config.")
        return {"seen_ips": set(), "new_ips": set()}

    now = now_utc_iso()
    seen_ips: set[str] = set()
    new_ips: set[str] = set()
    pre_existing = {
        row[0]
        for row in conn.execute("SELECT ipmi_ip FROM ipmi_inventory").fetchall()
    }
    if scan_mode == "ping":
        print("[*] Starting nmap ping scan on configured IPMI ranges...")
    else:
        print(f"[*] Starting nmap UDP scan on configured IPMI ranges (port {scan_port})...")

    for label, cidr in ipmi_ranges.items():
        if scan_mode == "ping":
            cmd = ["nmap", "-n", "-sn", "-oG", "-", cidr]
        else:
            cmd = [
                "nmap",
                "-n",
                "-sU",
                "-p",
                str(scan_port),
                "--open",
                "-oG",
                "-",
                cidr,
            ]
        if os.geteuid() != 0:
            cmd = ["sudo", "-n"] + cmd
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=120).decode()
        except FileNotFoundError:
            print("[!] nmap not found. Please install nmap first.")
            return {"seen_ips": seen_ips, "new_ips": new_ips}
        except PermissionError:
            print("[!] Permission denied for nmap UDP scan. Run script with sudo.")
            return {"seen_ips": seen_ips, "new_ips": new_ips}
        except subprocess.TimeoutExpired:
            print(f"[!] nmap timeout on range {label} ({cidr})")
            continue
        except subprocess.CalledProcessError as e:
            err = e.output.decode(errors="ignore").strip()
            if "requires root privileges" in err.lower() or "a password is required" in err.lower():
                print("[!] UDP nmap scan needs root privileges.")
                print("[!] Re-run as: sudo python interactive_auto.py")
                return {"seen_ips": seen_ips, "new_ips": new_ips}
            print(f"[!] nmap failed on range {label} ({cidr}): {err}")
            continue

        ips = _parse_nmap_grepable_output(
            output,
            port=scan_port if scan_mode != "ping" else None,
            ping_mode=(scan_mode == "ping"),
        )
        if scan_mode == "ping":
            print(f"    - {label} ({cidr}): {len(ips)} host(s) up")
        else:
            print(f"    - {label} ({cidr}): {len(ips)} host(s) with UDP {scan_port} open")
        seen_ips.update(ips)

        for ip in ips:
            if scan_mode == "ping":
                remark = f"DiscoveredBy:nmap-ping | Range:{label}"
            else:
                remark = f"DiscoveredBy:nmap-udp:{scan_port} | Range:{label}"
            conn.execute(
                """
                INSERT INTO ipmi_inventory (ipmi_ip, ipmi_name, remark, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(ipmi_ip) DO UPDATE SET
                    ipmi_name = COALESCE(NULLIF(ipmi_inventory.ipmi_name, ''), excluded.ipmi_name),
                    remark = CASE
                        WHEN ipmi_inventory.remark IS NULL OR ipmi_inventory.remark = '' THEN excluded.remark
                        WHEN instr(ipmi_inventory.remark, excluded.remark) > 0 THEN ipmi_inventory.remark
                        ELSE ipmi_inventory.remark || ' | ' || excluded.remark
                    END,
                    updated_at = excluded.updated_at
                """,
                (ip, label, remark, "Pending", now, now),
            )
            if ip not in pre_existing:
                new_ips.add(ip)
                pre_existing.add(ip)

    conn.commit()
    print(f"[*] nmap discovery complete. Seen: {len(seen_ips)}, New inserted: {len(new_ips)}")
    return {"seen_ips": seen_ips, "new_ips": new_ips}


def select_discovery_scan_mode(default_udp_port: int) -> tuple[str, int | None]:
    print("\n--- Discovery Scan Mode ---")
    print(f"1. Default UDP {default_udp_port}")
    print("2. Custom UDP port")
    print("3. Basic ping scan")
    choice = input("Select scan mode [1/2/3]: ").strip()

    if choice == "2":
        val = input("Enter custom UDP port: ").strip()
        try:
            port = int(val)
            if 1 <= port <= 65535:
                return "udp", port
        except ValueError:
            pass
        print(f"[!] Invalid port. Falling back to UDP {default_udp_port}.")
        return "udp", default_udp_port
    if choice == "3":
        return "ping", None
    return "udp", default_udp_port


def write_discovery_report(
    data_dir: str,
    scan_mode: str,
    scan_port: int | None,
    seen_ips: set[str],
    new_ips: set[str],
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = Path(data_dir) / f"ipmi_ip_list_{ts}.csv"
    mode_label = "ping" if scan_mode == "ping" else f"udp_{scan_port}"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["IPMI_IP", "ScanMode", "Status", "TimestampUTC"])
        for ip in sorted(seen_ips):
            status = "NEW" if ip in new_ips else "EXISTING"
            w.writerow([ip, mode_label, status, now_utc_iso()])
    return str(path)


def print_discovery_split(new_ips: set[str], existing_ips: set[str]) -> None:
    print("\n--- Discovery Result ---")
    print(f"New IPs: {len(new_ips)}")
    for ip in sorted(new_ips):
        print(f"  + {ip}")
    print(f"Existing IPs: {len(existing_ips)}")
    for ip in sorted(existing_ips):
        print(f"  = {ip}")


def post_discovery_action_menu() -> str:
    print("\nNext action:")
    print("1. Add new IPs to DB")
    print("2. Scan again")
    print("3. Check IPMI login now")
    print("4. Back to main menu")
    return input("Select [1/2/3/4]: ").strip().lower()


def has_data(row: sqlite3.Row) -> bool:
    keys = ("username", "password", "status", "remark")
    for key in keys:
        value = row[key]
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if key == "status" and text.lower() == "pending":
            continue
        return True
    return False


def list_ipmi_with_status(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    inv_rows = conn.execute(
        """
        SELECT ipmi_ip, ipmi_name, username, password, status, remark
        FROM ipmi_inventory
        ORDER BY ipmi_ip
        """
    ).fetchall()

    tracking_rows = conn.execute(
        """
        SELECT ipmi_ip, mgmt_ip, serial, ipmi_mac, disk_inventory, wipe_status
        FROM server_tracking
        ORDER BY ipmi_ip
        """
    ).fetchall()

    inv_map = {row["ipmi_ip"]: row for row in inv_rows}
    tracking_map = {row["ipmi_ip"]: row for row in tracking_rows}
    all_ips = sorted(set(inv_map.keys()) | set(tracking_map.keys()))

    if not all_ips:
        print("\n[!] No IPMI records found.")
        return

    print("\n--- IPMI Inventory (Combined View) ---")
    for idx, ip in enumerate(all_ips, start=1):
        inv = inv_map.get(ip)
        trk = tracking_map.get(ip)

        if inv:
            data_state = "Has Data" if has_data(inv) else "No Data"
            name = inv["ipmi_name"] if inv["ipmi_name"] else "-"
            remark = inv["remark"] if inv["remark"] else "-"
            raw_status = (inv["status"] or "").strip().lower()
            if raw_status == "success":
                cred_state = "Success"
            elif raw_status == "failed":
                cred_state = "Failed"
            else:
                cred_state = "NotTested"
        else:
            data_state = "Has Data"
            name = "-"
            remark = "-"
            cred_state = "NotTested"

        source = "Both" if (inv and trk) else ("ipmi_inventory" if inv else "server_tracking")
        wipe = trk["wipe_status"] if trk and trk["wipe_status"] else "-"
        cred_state_colored = color_cred_state(cred_state)
        print(
            f"{idx:>3}. {ip:<16}  Name:{name:<10}  [{data_state}]  "
            f"Cred:{cred_state_colored:<18}  Source:{source:<14}  Wipe:{wipe:<18}  Remark:{remark}"
        )
        if trk:
            mgmt_ip = trk["mgmt_ip"] if trk["mgmt_ip"] else "-"
            serial = trk["serial"] if trk["serial"] else "-"
            ipmi_mac = trk["ipmi_mac"] if trk["ipmi_mac"] else "-"
            disk_inventory = trk["disk_inventory"] if trk["disk_inventory"] else "-"
            print(f"      MGMT IP: {mgmt_ip}")
            print(f"      SERIAL : {serial}")
            print(f"      IPMI MAC: {ipmi_mac}")
            print("      DISK INVENTORY:")
            for line in str(disk_inventory).splitlines():
                print(f"        {line}")


def verify_ipmi_credentials(conn: sqlite3.Connection, credentials: list[dict]) -> None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ipmi_ip FROM ipmi_inventory ORDER BY ipmi_ip"
    ).fetchall()

    if not rows:
        print("[!] No IPMI targets in ipmi_inventory. Run option 2 first.")
        return
    if not credentials:
        print("[!] No credentials found in config key: possible_ipmi_credentials")
        return

    print(f"[*] Verifying credentials for {len(rows)} IPMI targets...")
    now = now_utc_iso()
    success_count = 0
    failed_ips: list[str] = []

    for row in rows:
        ip = row["ipmi_ip"]
        matched = False
        for cred in credentials:
            user = str(cred.get("username", "")).strip()
            pw = str(cred.get("password", "")).strip()
            if not user:
                continue
            cmd = [
                "ipmitool",
                "-I",
                "lanplus",
                "-H",
                ip,
                "-U",
                user,
                "-P",
                pw,
                "chassis",
                "status",
            ]
            try:
                subprocess.check_call(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                conn.execute(
                    """
                    UPDATE ipmi_inventory
                    SET username = ?, password = ?, status = ?, remark = ?, updated_at = ?
                    WHERE ipmi_ip = ?
                    """,
                    (user, pw, "Success", "", now, ip),
                )
                success_count += 1
                matched = True
                print(f"[+] {ip}: Success ({user})")
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue

        if not matched:
            conn.execute(
                """
                UPDATE ipmi_inventory
                SET username = NULL, password = NULL, status = ?, remark = ?, updated_at = ?
                WHERE ipmi_ip = ?
                """,
                ("Failed", "Credential check failed", now, ip),
            )
            failed_ips.append(ip)
            print(f"[-] {ip}: Failed")

    conn.commit()
    print(f"\n[*] Verification done. Success: {success_count}, Failed: {len(failed_ips)}")
    if failed_ips:
        print("[!] Failed IP list:")
        for ip in failed_ips:
            print(f"    - {ip}")


def run_ipmi_command(ip: str, user: str, pw: str, args: list[str]) -> tuple[bool, str]:
    cmd = ["ipmitool", "-I", "lanplus", "-H", ip, "-U", user, "-P", pw] + args
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10).decode()
        return True, out
    except Exception as e:
        return False, str(e)


def get_verified_targets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT
            i.ipmi_ip,
            i.ipmi_name,
            i.username,
            i.password,
            t.wipe_status,
            t.updated_at
        FROM ipmi_inventory i
        LEFT JOIN server_tracking t ON t.ipmi_ip = i.ipmi_ip
        WHERE LOWER(COALESCE(status, '')) = 'success'
        ORDER BY i.ipmi_ip
        """
    ).fetchall()


def select_targets_interactive(targets: list[sqlite3.Row]) -> list[sqlite3.Row] | None:
    if not targets:
        return []
    print("\n--- Verified IPMI Targets ---")
    for i, t in enumerate(targets, start=1):
        name = t["ipmi_name"] or "-"
        wipe_status = (t["wipe_status"] or "").strip()
        ts = (t["updated_at"] or "").strip()
        if wipe_status:
            wipe_text = f"{ANSI_GREEN}{wipe_status}{ANSI_RESET}"
            ts_text = ts if ts else "-"
            print(f"{i:>3}. {t['ipmi_ip']}  Name:{name}  User:{t['username']}  Wipe:{wipe_text}  TS:{ts_text}")
        else:
            print(f"{i:>3}. {t['ipmi_ip']}  Name:{name}  User:{t['username']}  Wipe:-  TS:-")
    raw = input("\nSelect refs (e.g. 1,3,5), 'a' for all, or 'b/0' to go back: ").strip().lower()
    if raw in {"b", "0"}:
        return None
    if raw == "a":
        return targets
    refs: set[int] = set()
    for chunk in raw.split(","):
        s = chunk.strip()
        if not s:
            continue
        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= len(targets):
                refs.add(idx - 1)
    return [targets[i] for i in sorted(refs)]


def read_dhcp_mgmt_ips(cfg: dict) -> set[str]:
    netboot = cfg.get("netboot", {})
    host = netboot.get("host", "192.168.122.114")
    user = netboot.get("user", "netboot")
    lease_path = netboot.get("DHCP_LEASE_FILE", "/var/lib/misc/dnsmasq.leases")
    key_path = config_ssh_key(cfg)
    mgmt_ranges = cfg.get("mgmt_ranges", {})
    networks = []
    for _, cidr in mgmt_ranges.items():
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            pass

    ok, out = ssh_exec(host, user, key_path, f"cat {lease_path}", timeout=10)
    if not ok:
        return set()

    ips: set[str] = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        ip = parts[2].strip()
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not networks:
            ips.add(ip)
            continue
        if any(addr in n for n in networks):
            ips.add(ip)
    return filter_online_ips(ips, cfg)


def _is_ip_online(ip: str, timeout_sec: int) -> bool:
    cmd = [
        "ping",
        "-c",
        "1",
        "-W",
        str(timeout_sec),
        ip,
    ]
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout_sec + 1)
        return True
    except Exception:
        return False


def filter_online_ips(ips: set[str], cfg: dict) -> set[str]:
    if not ips:
        return set()
    defaults = cfg.get("defaults", {})
    ping_timeout = int(defaults.get("dhcp_ping_timeout_seconds", 1))
    ping_workers = int(defaults.get("dhcp_ping_workers", 32))

    online: set[str] = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=ping_workers) as pool:
        futures = {pool.submit(_is_ip_online, ip, ping_timeout): ip for ip in ips}
        for fut in concurrent.futures.as_completed(futures):
            ip = futures[fut]
            try:
                if fut.result():
                    online.add(ip)
            except Exception:
                pass
    return online


def mgmt_label_for_ip(ip: str, mgmt_ranges: dict) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "-"
    for label, cidr in mgmt_ranges.items():
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return label
        except ValueError:
            continue
    return "-"


def wait_for_ssh_ready(ip: str, ssh_user: str, max_attempts: int, cfg: dict) -> bool:
    key_path = config_ssh_key(cfg)
    defaults = cfg.get("defaults", {})
    connect_timeout = int(defaults.get("ssh_connect_timeout_seconds", 4))
    connection_attempts = int(defaults.get("ssh_connection_attempts", 1))
    retry_delay = int(defaults.get("ssh_retry_delay_seconds", 1))
    probe_timeout = max(connect_timeout + 2, 6)
    for _ in range(max_attempts):
        cmd = ssh_base_cmd(key_path) + [
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-o",
            f"ConnectionAttempts={connection_attempts}",
            f"{ssh_user}@{ip}",
            "exit",
        ]
        try:
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=probe_timeout)
            return True
        except Exception:
            countdown_sleep(retry_delay, "Waiting before next SSH check")
    return False


def upsert_tracking_status(conn: sqlite3.Connection, ipmi_ip: str, mgmt_ip: str, status: str) -> None:
    now = now_utc_iso()
    conn.execute(
        """
        INSERT INTO server_tracking (ipmi_ip, mgmt_ip, wipe_status, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ipmi_ip) DO UPDATE SET
            mgmt_ip = excluded.mgmt_ip,
            wipe_status = excluded.wipe_status,
            updated_at = excluded.updated_at
        """,
        (ipmi_ip, mgmt_ip, status, now),
    )
    conn.commit()


def record_stage(
    conn: sqlite3.Connection,
    ipmi_ip: str,
    stage: str,
    result: str,
    detail: str = "",
    mgmt_ip: str = "",
) -> None:
    now = now_utc_iso()
    conn.execute(
        """
        INSERT INTO server_tracking (ipmi_ip, mgmt_ip, wipe_status, updated_at, deploy_stage, deploy_result, deploy_detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ipmi_ip) DO UPDATE SET
            mgmt_ip = CASE WHEN excluded.mgmt_ip <> '' THEN excluded.mgmt_ip ELSE server_tracking.mgmt_ip END,
            wipe_status = excluded.wipe_status,
            updated_at = excluded.updated_at,
            deploy_stage = excluded.deploy_stage,
            deploy_result = excluded.deploy_result,
            deploy_detail = excluded.deploy_detail
        """,
        (ipmi_ip, mgmt_ip, f"{stage}:{result}", now, stage, result, detail[:500]),
    )
    conn.commit()


def upsert_tracking_full(
    conn: sqlite3.Connection,
    ipmi_ip: str,
    mgmt_ip: str,
    serial: str,
    ipmi_mac: str,
    disk_inventory: str,
    status: str,
) -> None:
    now = now_utc_iso()
    conn.execute(
        """
        INSERT INTO server_tracking (ipmi_ip, mgmt_ip, serial, ipmi_mac, disk_inventory, wipe_status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ipmi_ip) DO UPDATE SET
            mgmt_ip = excluded.mgmt_ip,
            serial = excluded.serial,
            ipmi_mac = excluded.ipmi_mac,
            disk_inventory = excluded.disk_inventory,
            wipe_status = excluded.wipe_status,
            updated_at = excluded.updated_at
        """,
        (ipmi_ip, mgmt_ip, serial, ipmi_mac, disk_inventory, status, now),
    )
    conn.commit()


def run_deployment_automation(conn: sqlite3.Connection, cfg: dict) -> None:
    targets = get_verified_targets(conn)
    if not targets:
        print("[!] No verified IPMI targets found. Run option 4 first.")
        return
    selected = select_targets_interactive(targets)
    if selected is None:
        print("[*] Back to main menu.")
        return
    if not selected:
        print("[!] No valid target selected.")
        return

    defaults = cfg.get("defaults", {})
    poll_seconds = int(defaults.get("dhcp_poll_seconds", 10))
    ssh_user = defaults.get("ssh_user", "user")
    ssh_attempts = int(defaults.get("ssh_watch_attempts", 60))
    ssh_start_delay = int(defaults.get("ssh_start_delay_after_dhcp_seconds", 5))
    power_wait_after_on = int(defaults.get("power_wait_after_on_seconds", 10))
    power_wait_after_cycle = int(defaults.get("power_wait_after_cycle_seconds", 10))
    dhcp_watch_loops = int(defaults.get("dhcp_watch_loops", 60))
    mgmt_ranges = cfg.get("mgmt_ranges", {})
    key_path = config_ssh_key(cfg)
    if not key_path:
        print("[!] Configured SSH key not found.")
        print("[!] Set a valid path in interactive_auto_config.json -> ssh.identity_file")
        return
    script_path = cfg.get("files", {}).get("wipe_script", "wdc_bootstrap.sh")

    print(f"[*] Starting deployment automation for {len(selected)} target(s)...")
    for t in selected:
        ip = t["ipmi_ip"]
        user = t["username"] or ""
        pw = t["password"] or ""
        print(f"\n=== Target {ip} ===")
        record_stage(conn, ip, "START", "OK", "Deployment started")

        ok, msg = run_ipmi_command(ip, user, pw, ["chassis", "bootdev", "pxe"])
        print(f"    [ipmi] chassis bootdev pxe -> {msg.strip() if msg else '-'}")
        if not ok:
            print(f"[!] PXE set failed: {msg}")
            record_stage(conn, ip, "PXE_SET", "FAILED", msg)
            upsert_tracking_status(conn, ip, "", "PXE_Set_Failed")
            continue
        print("[+] PXE set sent.")
        record_stage(conn, ip, "PXE_SET", "OK", "PXE bootdev set")

        ok, msg = run_ipmi_command(ip, user, pw, ["chassis", "bootparam", "get", "5"])
        print(f"    [ipmi] chassis bootparam get 5 -> {msg.strip() if msg else '-'}")
        if not ok or "pxe" not in msg.lower():
            print(f"[!] PXE verify failed: {msg}")
            record_stage(conn, ip, "PXE_VERIFY", "FAILED", msg)
            upsert_tracking_status(conn, ip, "", "PXE_Verify_Failed")
            continue
        print("[+] PXE verify passed.")
        record_stage(conn, ip, "PXE_VERIFY", "OK", "Boot flag confirms PXE")

        ok, msg = run_ipmi_command(ip, user, pw, ["chassis", "power", "status"])
        print(f"    [ipmi] chassis power status -> {msg.strip() if msg else '-'}")
        if not ok:
            print(f"[!] Power status check failed: {msg}")
            record_stage(conn, ip, "POWER_STATUS", "FAILED", msg)
            upsert_tracking_status(conn, ip, "", "Power_Status_Failed")
            continue
        is_off = "off" in msg.lower()
        record_stage(conn, ip, "POWER_STATUS", "OK", msg.strip())

        if is_off:
            print("[*] Power status is OFF -> sending power on (skip power cycle).")
            ok, msg = run_ipmi_command(ip, user, pw, ["chassis", "power", "on"])
            action = "POWER_ON"
            fail_status = "Power_On_Failed"
            ok_status_text = "Power on sent"
        else:
            print("[*] Power status is ON -> sending power cycle.")
            ok, msg = run_ipmi_command(ip, user, pw, ["chassis", "power", "cycle"])
            action = "POWER_CYCLE"
            fail_status = "Power_Cycle_Failed"
            ok_status_text = "Power cycle sent"
        print(f"    [ipmi] {action.lower().replace('_', ' ')} -> {msg.strip() if msg else '-'}")

        if not ok:
            print(f"[!] {action} failed: {msg}")
            record_stage(conn, ip, action, "FAILED", msg)
            upsert_tracking_status(conn, ip, "", fail_status)
            continue
        print(f"[+] {ok_status_text}.")
        record_stage(conn, ip, action, "OK", ok_status_text)

        if action == "POWER_ON":
            countdown_sleep(power_wait_after_on, "Waiting after power on")
            ok2, msg2 = run_ipmi_command(ip, user, pw, ["chassis", "power", "status"])
            print(f"    [ipmi] post-on chassis power status -> {msg2.strip() if msg2 else '-'}")
            if not ok2 or "on" not in msg2.lower():
                detail = msg2 if ok2 else msg2
                print(f"[!] Post power-on verification failed: {detail}")
                record_stage(conn, ip, "POWER_ON_VERIFY", "FAILED", detail)
                upsert_tracking_status(conn, ip, "", "Power_On_Verify_Failed")
                continue
            print("[+] Post power-on verification passed (power is ON).")
            record_stage(conn, ip, "POWER_ON_VERIFY", "OK", "Power is ON after wait")
        else:
            countdown_sleep(power_wait_after_cycle, "Waiting after power cycle")

        time.sleep(5)
        baseline = read_dhcp_mgmt_ips(cfg)
        print(f"[*] DHCP baseline captured (online only): {len(baseline)} IP(s)")
        record_stage(conn, ip, "DHCP_BASELINE", "OK", f"Captured online baseline: {len(baseline)} IPs")

        detected_ip = ""
        for _ in range(dhcp_watch_loops):
            current = read_dhcp_mgmt_ips(cfg)
            new_ips = sorted(current - baseline)
            if new_ips:
                detected_ip = new_ips[0]
                break
            countdown_sleep(poll_seconds, "Waiting for DHCP change")

        if not detected_ip:
            print("[!] No new MGMT IP detected in DHCP watch window.")
            record_stage(conn, ip, "DHCP_WATCH", "FAILED", "No new MGMT IP detected")
            upsert_tracking_status(conn, ip, "", "Mgmt_IP_Not_Detected")
            continue

        mgmt_label = mgmt_label_for_ip(detected_ip, mgmt_ranges)
        print(f"[+] New MGMT IP detected: {detected_ip} ({mgmt_label})")
        record_stage(conn, ip, "DHCP_WATCH", "OK", f"Detected {detected_ip} ({mgmt_label})", mgmt_ip=detected_ip)
        upsert_tracking_status(conn, ip, detected_ip, f"Mgmt_IP_Detected:{mgmt_label}")

        countdown_sleep(ssh_start_delay, "Waiting before SSH readiness checks")
        ssh_ok = wait_for_ssh_ready(detected_ip, ssh_user, ssh_attempts, cfg)
        if ssh_ok:
            print(f"[+] SSH ready on {detected_ip}")
            record_stage(conn, ip, "SSH_WATCH", "OK", "SSH reachable", mgmt_ip=detected_ip)
            upsert_tracking_status(conn, ip, detected_ip, f"SSH_Ready:{mgmt_label}")
        else:
            print(f"[!] SSH not ready on {detected_ip}")
            record_stage(conn, ip, "SSH_WATCH", "FAILED", "SSH not reachable", mgmt_ip=detected_ip)
            upsert_tracking_status(conn, ip, detected_ip, f"SSH_Not_Ready:{mgmt_label}")
            continue

        ok, serial = ssh_exec(detected_ip, ssh_user, key_path, "sudo dmidecode -s system-serial-number", timeout=25)
        if not ok:
            serial = ""
        ok, ipmi_mac = ssh_exec(detected_ip, ssh_user, key_path, "sudo ipmitool lan print 1 | awk -F': ' '/MAC Address/ {print $2}'", timeout=25)
        if not ok:
            ipmi_mac = ""
        ok, disk_inventory = ssh_exec(detected_ip, ssh_user, key_path, "lsblk -dn -o NAME,SERIAL,SIZE,MODEL", timeout=30)
        if not ok:
            disk_inventory = ""

        if not os.path.exists(script_path):
            print(f"[!] Wipe script not found: {script_path}")
            record_stage(conn, ip, "WIPE_PREP", "FAILED", f"Script missing: {script_path}", mgmt_ip=detected_ip)
            upsert_tracking_full(conn, ip, detected_ip, serial.strip(), ipmi_mac.strip(), disk_inventory.strip(), f"Wipe_Script_Missing:{mgmt_label}")
            continue

        ok, msg = scp_upload(detected_ip, ssh_user, key_path, script_path, "~/wdc_bootstrap.sh", timeout=60)
        if not ok:
            print(f"[!] Copy wipe script failed: {msg}")
            record_stage(conn, ip, "WIPE_COPY", "FAILED", msg, mgmt_ip=detected_ip)
            upsert_tracking_full(conn, ip, detected_ip, serial.strip(), ipmi_mac.strip(), disk_inventory.strip(), f"Wipe_Copy_Failed:{mgmt_label}")
            continue
        record_stage(conn, ip, "WIPE_COPY", "OK", "Script copied", mgmt_ip=detected_ip)

        launch_cmd = "chmod +x ~/wdc_bootstrap.sh && tmux new-session -d -s wipe_session 'sudo ./wdc_bootstrap.sh'"
        ok, msg = ssh_exec(detected_ip, ssh_user, key_path, launch_cmd, timeout=20)
        if not ok:
            print(f"[!] TMUX wipe start failed: {msg}")
            record_stage(conn, ip, "WIPE_START", "FAILED", msg, mgmt_ip=detected_ip)
            upsert_tracking_full(conn, ip, detected_ip, serial.strip(), ipmi_mac.strip(), disk_inventory.strip(), f"Wipe_Start_Failed:{mgmt_label}")
            continue

        ok, _ = ssh_exec(detected_ip, ssh_user, key_path, "tmux has-session -t wipe_session", timeout=10)
        if ok:
            print(f"[+] Wipe started in TMUX on {detected_ip}")
            record_stage(conn, ip, "WIPE_VERIFY", "OK", "TMUX session verified", mgmt_ip=detected_ip)
            upsert_tracking_full(conn, ip, detected_ip, serial.strip(), ipmi_mac.strip(), disk_inventory.strip(), f"Running_in_TMUX:{mgmt_label}")
        else:
            print(f"[!] TMUX session verification failed on {detected_ip}")
            record_stage(conn, ip, "WIPE_VERIFY", "FAILED", "TMUX session missing", mgmt_ip=detected_ip)
            upsert_tracking_full(conn, ip, detected_ip, serial.strip(), ipmi_mac.strip(), disk_inventory.strip(), f"Wipe_Verify_Failed:{mgmt_label}")


def show_main_menu() -> str:
    print("\n" + "=" * 50)
    print("Interactive Auto - Main Menu")
    print("=" * 50)
    print("1. List IPMI IPs from DB (numbered + data status)")
    print("2. Discover IPMI IPs via nmap (config ipmi_ranges)")
    print("3. Import IPMI IPs from list.csv (separate manual mode)")
    print("4. Verify IPMI login using possible credentials")
    print("5. Deployment automation (PXE + cycle + DHCP + SSH watcher)")
    print("q. Quit")
    return input("\nSelect option: ").strip().lower()


def run() -> None:
    ensure_sudo()
    cfg = load_config()
    db_path = cfg["database"]["path"]
    seed_csv = cfg["files"]["seed_ipmi_csv"]
    data_dir = cfg["files"]["data_dir"]
    ipmi_ranges = cfg.get("ipmi_ranges", {})
    possible_ipmi_credentials = cfg.get("possible_ipmi_credentials", [])
    ipmi_default_udp_port = int(cfg.get("defaults", {}).get("ipmi_default_udp_port", 623))

    ensure_data_dir(data_dir)
    conn = get_conn(db_path)
    init_db(conn)
    init_tracking_table(conn)
    apply_ipmi_range_labels(conn, ipmi_ranges)

    while True:
        choice = show_main_menu()
        if choice == "1":
            list_ipmi_with_status(conn)
        elif choice == "2":
            while True:
                scan_mode, scan_port = select_discovery_scan_mode(ipmi_default_udp_port)
                result = scan_ipmi_ranges_with_nmap(conn, ipmi_ranges, scan_mode, scan_port)
                seen_ips = result["seen_ips"]
                new_ips = result["new_ips"]
                existing_ips = seen_ips - new_ips
                relabeled = apply_ipmi_range_labels(conn, ipmi_ranges)
                if relabeled:
                    print(f"[+] Updated {relabeled} rows with IPMI range labels")
                if seen_ips:
                    print_discovery_split(new_ips, existing_ips)
                    report_path = write_discovery_report(data_dir, scan_mode, scan_port, seen_ips, new_ips)
                    print(f"[*] Discovery report saved: {report_path}")
                action = post_discovery_action_menu()
                if action == "1":
                    print("[*] New IPs are already added to DB during discovery.")
                elif action == "2":
                    continue
                elif action == "3":
                    verify_ipmi_credentials(conn, possible_ipmi_credentials)
                break
        elif choice == "3":
            inserted = seed_from_csv_if_missing(conn, seed_csv)
            relabeled = apply_ipmi_range_labels(conn, ipmi_ranges)
            if inserted == 0:
                print(f"[*] No new rows inserted. Checked: {seed_csv}")
            else:
                print(f"[+] Inserted {inserted} new IPMI rows from {seed_csv}")
            if relabeled:
                print(f"[+] Updated {relabeled} rows with IPMI range labels")
        elif choice == "4":
            verify_ipmi_credentials(conn, possible_ipmi_credentials)
        elif choice == "5":
            run_deployment_automation(conn, cfg)
        elif choice == "q":
            break
        else:
            print("[!] Invalid option. Try again.")

    conn.close()


if __name__ == "__main__":
    run()
