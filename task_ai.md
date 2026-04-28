# AI Development Task List

## 1) Foundation and Configuration
- [ ] Create a master config file for credentials, IP ranges, timers, paths, and defaults.
- [ ] Add configurable countdown values (menu timeout, SSH wait, DHCP poll interval).
- [ ] Add configurable management network ranges (e.g., `MGMT_1`, `MGMT_2`).
- [ ] Define default scan mode options (UDP 623, custom port, ping-only).
- [ ] Add validation and startup checks for required config keys.

## 2) IPMI Discovery Workflow
- [ ] Build menu: "Scan the Network for IPMI IPs".
- [ ] Prompt user for scan range and scan mode.
- [ ] Implement nmap-based scan for default UDP 623.
- [ ] Implement nmap-based scan for custom user-provided port.
- [ ] Implement basic ping-only scan mode.
- [ ] Normalize and deduplicate discovered IP list.
- [ ] Keep nmap discovery as its own dedicated menu mode (not mixed with CSV import mode).

## 3) Discovery Output and Reconciliation
- [ ] Generate timestamped `ipmi_ip_list.csv` in `data/`.
- [ ] Compare scanned IPs against existing DB records.
- [ ] Add CSV remarks for existing/new status and linked DB metadata.
- [ ] Print two terminal lists: new IPs and existing IPs.
- [ ] Present next-step menu:
  - [ ] Add new IPs to DB
  - [ ] Scan again
  - [ ] Run IPMI login verification
  - [ ] Quit
- [ ] Keep `list.csv` import as a separate manual menu option with clear source tagging (`Source:CSV`).

## 4) IPMI Credential Verification
- [ ] Test predefined credential list against selected/available IPMI IPs.
- [ ] Store successful credentials in DB.
- [ ] Store failed attempts with remark/status in DB.
- [ ] Print final failed-IP report for operator review.

## 5) Deployment Automation Menu
- [ ] Build deployment menu with default action + 5-second configurable countdown.
- [ ] Show selectable list of IPMI targets with successful credentials.
- [ ] Support single, multi-select, and select-all modes.
- [ ] Process deployment one IP at a time in deterministic order.

## 6) Per-Server Deployment Sequence
- [ ] Step 3.1: Send `chassis bootdev pxe`.
- [ ] Step 3.1.1: Verify boot flag using `chassis bootparam get 5` and require PXE in output.
- [ ] Step 3.1.2: Check power with `chassis power status`.
- [ ] Step 3.1.3: If power is off, send `chassis power on`; otherwise send `chassis power cycle`.
- [ ] Step 3.2: Capture baseline DHCP lease state via netboot SSH.
- [ ] Step 3.4: Poll DHCP every configurable interval during reboot.
- [ ] Step 3.5: Detect new MGMT IP and map it to configured MGMT range label.
- [ ] Step 3.5 (SSH watcher): Test SSH readiness with retry/connect timeout template.
- [ ] Step 3.6: On SSH success, collect inventory + launch wipe in TMUX.

## 7) DHCP Watcher and Netboot Integration
- [ ] Verify SSH access to netboot host before DHCP operations.
- [ ] Implement dnsmasq lease query parser for current MGMT IP set.
- [ ] Store baseline snapshot keyed by `ipmi_ip + timestamp`.
- [ ] Compare live snapshots to baseline to detect new lease assignments.
- [ ] Log DHCP detection events to DB and terminal.

## 8) Inventory, Wipe, and Tracking
- [ ] Ensure/maintain `server_tracking` schema in SQLite.
- [ ] Record MGMT IP, serial, IPMI MAC, and full disk inventory.
- [ ] Transfer and run `wdc_bootstrap.sh` in detached TMUX session.
- [ ] Confirm process start status and persist result in DB.
- [ ] Close SSH session without affecting TMUX process.

## 9) Iteration and Operator UX
- [ ] After each server, show configurable countdown for user action.
- [ ] If no user input, continue to next valid IPMI target automatically.
- [ ] Show concise progress and status output per server stage.
- [ ] Add clear error prompts and safe retry points.

## 10) Reporting and Persistence
- [ ] Persist all key events/status transitions in DB.
- [ ] Produce terminal summary for run completion (success/fail/pending).
- [ ] Keep CSV and DB outputs aligned with timestamps.
- [ ] Add structured logs for troubleshooting (scan, DHCP, SSH, deploy).

## 11) Main Menu DB Query View
- [ ] Add a main-menu option to query DB and list all IPMI IPs.
- [ ] Show each IPMI IP with a numeric reference for user selection.
- [ ] Show per-IP data status (`Has Data` / `No Data`).
- [ ] Define `Has Data` based on key fields populated in DB.
- [ ] Allow quick follow-up actions using the numeric reference list.
