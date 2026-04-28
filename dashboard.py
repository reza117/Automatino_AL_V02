import sqlite3
from typing import Any

DB_PATH = "wdc_inventory.db"


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def get_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def print_kv_block(title: str, data: dict[str, Any]) -> None:
    print(f"\n--- {title} ---")
    if not data:
        print("(no data)")
        return
    for k, v in data.items():
        if v is None or v == "":
            v = "-"
        print(f"{k}: {v}")


def print_table_rows(title: str, rows: list[sqlite3.Row], key_columns: list[str]) -> None:
    print(f"\n--- {title} ---")
    if not rows:
        print("(no rows)")
        return
    for idx, row in enumerate(rows, start=1):
        parts = []
        for col in key_columns:
            if col in row.keys():
                val = row[col]
                parts.append(f"{col}={val if val not in (None, '') else '-'}")
        print(f"{idx:>3}. " + " | ".join(parts))


def show_summary(conn: sqlite3.Connection) -> None:
    print("\n=== DB Summary ===")
    for table in ("ipmi_inventory", "server_tracking", "servers"):
        if table_exists(conn, table):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {count}")
        else:
            print(f"{table}: (missing)")


def list_ipmi_combined(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "ipmi_inventory"):
        print("\n[!] ipmi_inventory table not found.")
        return

    rows = conn.execute(
        """
        SELECT
            i.ipmi_ip,
            i.ipmi_name,
            i.username,
            i.status AS cred_status,
            i.remark,
            t.mgmt_ip,
            t.wipe_status,
            t.updated_at,
            t.deploy_stage,
            t.deploy_result
        FROM ipmi_inventory i
        LEFT JOIN server_tracking t ON t.ipmi_ip = i.ipmi_ip
        ORDER BY i.ipmi_ip
        """
    ).fetchall()
    print_table_rows(
        "Combined IPMI List",
        rows,
        [
            "ipmi_ip",
            "ipmi_name",
            "cred_status",
            "mgmt_ip",
            "wipe_status",
            "deploy_stage",
            "deploy_result",
        ],
    )


def show_full_ipmi_details(conn: sqlite3.Connection) -> None:
    ip_set: set[str] = set()
    if table_exists(conn, "ipmi_inventory"):
        rows = conn.execute("SELECT ipmi_ip FROM ipmi_inventory WHERE COALESCE(ipmi_ip, '') <> '' ORDER BY ipmi_ip").fetchall()
        ip_set.update(r["ipmi_ip"] for r in rows)
    if table_exists(conn, "server_tracking"):
        rows = conn.execute("SELECT ipmi_ip FROM server_tracking WHERE COALESCE(ipmi_ip, '') <> '' ORDER BY ipmi_ip").fetchall()
        ip_set.update(r["ipmi_ip"] for r in rows)
    if table_exists(conn, "servers"):
        cols = get_table_columns(conn, "servers")
        if "IP" in cols:
            rows = conn.execute("SELECT IP FROM servers WHERE COALESCE(IP, '') <> '' ORDER BY IP").fetchall()
            ip_set.update(r["IP"] for r in rows)

    ips = sorted(ip_set)
    if not ips:
        print("[!] No IPs available.")
        return

    print("\nAvailable IPMI IPs:")
    for idx, val in enumerate(ips, start=1):
        print(f"{idx:>3}. {val}")

    raw = input("Select number (or 0 to cancel): ").strip()
    if raw == "0":
        return
    if not raw.isdigit():
        print("[!] Invalid selection.")
        return
    pos = int(raw)
    if pos < 1 or pos > len(ips):
        print("[!] Number out of range.")
        return
    ip = ips[pos - 1]

    if table_exists(conn, "ipmi_inventory"):
        row = conn.execute(
            "SELECT * FROM ipmi_inventory WHERE ipmi_ip=?",
            (ip,),
        ).fetchone()
        print_kv_block("ipmi_inventory", dict(row) if row else {})
    else:
        print("\n--- ipmi_inventory ---\n(missing table)")

    if table_exists(conn, "server_tracking"):
        row = conn.execute(
            "SELECT * FROM server_tracking WHERE ipmi_ip=?",
            (ip,),
        ).fetchone()
        print_kv_block("server_tracking", dict(row) if row else {})
    else:
        print("\n--- server_tracking ---\n(missing table)")

    if table_exists(conn, "servers"):
        cols = get_table_columns(conn, "servers")
        key_col = "IP" if "IP" in cols else None
        if key_col:
            row = conn.execute(
                f"SELECT * FROM servers WHERE {key_col}=?",
                (ip,),
            ).fetchone()
            print_kv_block("servers", dict(row) if row else {})


def list_failed_or_pending(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "ipmi_inventory"):
        print("\n[!] ipmi_inventory table not found.")
        return
    rows = conn.execute(
        """
        SELECT ipmi_ip, ipmi_name, status, remark, updated_at
        FROM ipmi_inventory
        WHERE LOWER(COALESCE(status, '')) <> 'success'
        ORDER BY ipmi_ip
        """
    ).fetchall()
    print_table_rows(
        "Non-Success Credential Status",
        rows,
        ["ipmi_ip", "ipmi_name", "status", "remark", "updated_at"],
    )


def list_latest_stage_status(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "server_tracking"):
        print("\n[!] server_tracking table not found.")
        return
    rows = conn.execute(
        """
        SELECT
            ipmi_ip, mgmt_ip, deploy_stage, deploy_result, deploy_detail, wipe_status, updated_at
        FROM server_tracking
        ORDER BY updated_at DESC, ipmi_ip
        """
    ).fetchall()
    print_table_rows(
        "Latest Deployment/Tracking State",
        rows,
        [
            "ipmi_ip",
            "mgmt_ip",
            "deploy_stage",
            "deploy_result",
            "wipe_status",
            "updated_at",
        ],
    )


def menu() -> str:
    print("\n" + "=" * 56)
    print("IPMI Dashboard (Read-Only)")
    print("=" * 56)
    print("1. DB summary")
    print("2. List all IPMI combined status")
    print("3. Show full details for one IPMI IP")
    print("4. List failed/pending credential checks")
    print("5. List latest deployment stage status")
    print("q. Quit")
    return input("\nSelect: ").strip().lower()


def run() -> None:
    try:
        conn = connect_db()
    except Exception as e:
        print(f"[!] Failed to open DB: {e}")
        return

    while True:
        choice = menu()
        if choice == "1":
            show_summary(conn)
        elif choice == "2":
            list_ipmi_combined(conn)
        elif choice == "3":
            show_full_ipmi_details(conn)
        elif choice == "4":
            list_failed_or_pending(conn)
        elif choice == "5":
            list_latest_stage_status(conn)
        elif choice == "q":
            break
        else:
            print("[!] Invalid option.")

    conn.close()


if __name__ == "__main__":
    run()
