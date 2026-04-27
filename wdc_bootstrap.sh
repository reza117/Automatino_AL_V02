#!/usr/bin/env bash
set -euo pipefail

# --- CONFIGURATION ---
SERVER_IP="10.0.0.250"
NFS_EXPORT="/srv/reports"
REPORT_MOUNT="/mnt"
REPORT_DIR="/tmp/netboot-reports"

# --- HELPER FUNCTIONS ---
get_mac() {
    ip route get "$SERVER_IP" | grep -Po 'dev \K\S+' | xargs ip link show | awk '/link\/ether/ {print $2}' | tr -d ':' || echo "unknown_mac"
}

# --- PRE-WIPE SETUP ---
mkdir -p "${REPORT_DIR}"
echo "[*] Mounting NFS for report logging..."
mount -t nfs "${SERVER_IP}:${NFS_EXPORT}" "${REPORT_MOUNT}" -o nolock,soft || echo "NFS Mount failed, continuing locally..."

# Collect all physical disks, exclude loop and the OS drive if needed
# For a full wipe, we take all 'disk' types
mapfile -t DISKS < <(lsblk -dn -o NAME,TYPE | awk '$2=="disk" && $1 !~ /loop/ {print "/dev/"$1}')

if [[ "${#DISKS[@]}" -eq 0 ]]; then
    echo "No disks found!" | tee "${REPORT_DIR}/error.log"
    exit 1
fi

# --- EXECUTION ---
MAC=$(get_mac)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="${REPORT_DIR}/wipe_${MAC}_${TIMESTAMP}.log"

echo "============================================"
echo " STARTING FULL DISK WIPE ON: ${DISKS[*]}"
echo " START TIME: $(date)"
echo "============================================"

# --autonuke is critical for non-interactive mode
# --method=prng is your requested single-pass random wipe
/usr/sbin/nwipe --autonuke --method=prng --verify=off --logfile="${LOG_FILE}" "${DISKS[@]}"

echo "============================================"
echo " WIPE COMPLETE: $(date)"
echo "============================================"

# Upload final logs to NFS
cp "${LOG_FILE}" "${REPORT_MOUNT}/" 2>/dev/null || true
sync
