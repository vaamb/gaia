#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

# Load logging functions
readonly DATETIME=$(date +%Y%m%d_%H%M%S)
rm -f /tmp/gaia_stop_*.log
readonly LOG_FILE="/tmp/gaia_stop_${DATETIME}.log"
readonly SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
. "${SCRIPT_DIR}/utils/logging.sh"

# Check if GAIA_DIR is set
if [[ -z "${GAIA_DIR:-}" ]]; then
    die "GAIA_DIR environment variable is not set. Please source your profile or run the install script first."
fi

# Check if the directory exists
if [[ ! -d "$GAIA_DIR" ]]; then
    die "Gaia directory not found at $GAIA_DIR. Please check your installation."
fi

# Ensure logs directory exists
mkdir -p "${GAIA_DIR}/logs" || die "Failed to create logs directory"

# Log stop attempt
log INFO "Attempting to stop Gaia..."

# Check that a PID is really a Gaia process, and not a recycled PID.
# Gaia renames itself to "gaia" (setproctitle), but only once its imports are
# done: until then it is still "<python> -m gaia". Matching the command line
# covers both, so an instance can be stopped while it is still starting up.
is_gaia_proc() {
    local pid=$1
    local cmdline
    # tr flattens the NUL-separated argv (2>/dev/null before the redirect so a
    # dead PID's failed open is silenced); a missing /proc entry returns 1.
    cmdline=$(tr '\0' ' ' 2>/dev/null < "/proc/${pid}/cmdline") || return 1
    # read strips the trailing NUL-padding setproctitle leaves behind.
    read -r cmdline <<< "$cmdline"
    [[ "$cmdline" == "gaia" || "$cmdline" == *"-m gaia" ]]
}

# Function to check if Gaia is running
get_gaia_pid() {
    # Prefer PID file when available
    if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
        local pid
        pid=$(cat "${GAIA_DIR}/gaia.pid" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && is_gaia_proc "$pid"; then
            echo "$pid"
            return 0
        fi
    fi
    # Fallback: the PID file may be missing or stale while an instance started
    # by other means is still running. Match both the renamed process and one
    # still starting up ("<python> -m gaia"); -f on the latter is safe as it
    # cannot match this script's own command line.
    { pgrep -x "gaia"; pgrep -f -- "-m gaia"; } 2>/dev/null | head -n1 || true
}

# Check if Gaia is running
GAIA_PID=$(get_gaia_pid) || true

if [[ -z "$GAIA_PID" ]]; then
    log INFO "No running instance of Gaia found."

    # Clean up PID file if it exists
    if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
        log WARN "Stale PID file found. Cleaning up..."
        rm -f "${GAIA_DIR}/gaia.pid"
    fi

    exit 0
fi

log INFO "Stopping Gaia (PID: $GAIA_PID)..."

# Send SIGTERM (15) - graceful shutdown
if kill -15 "$GAIA_PID" 2>/dev/null; then
    # Wait for the process to terminate
    TIMEOUT=10  # seconds
    sleep .5
    while (( TIMEOUT-- > 0 )) && kill -0 "$GAIA_PID" 2>/dev/null; do
        echo -n "."
        sleep 1
    done
    echo ""

    # Check if process is still running
    if kill -0 "$GAIA_PID" 2>/dev/null; then
        log WARN "Graceful shutdown failed. Force killing the process..."
        kill -9 "$GAIA_PID" 2>/dev/null || true
        sleep .5
    fi

    # Clean up PID file
    if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
        rm -f "${GAIA_DIR}/gaia.pid"
    fi

    # Verify the process was actually stopped
    if kill -0 "$GAIA_PID" 2>/dev/null; then
        die "Failed to stop Gaia. Process still running with PID: ${GAIA_PID}."
    fi

    log SUCCESS "Gaia stopped successfully."
    exit 0
else
    die "Failed to send stop signal to Gaia (PID: ${GAIA_PID}). You may need to run with sudo."
fi
