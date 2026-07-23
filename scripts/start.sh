#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

# Load logging functions
readonly DATETIME=$(date +%Y%m%d_%H%M%S)
rm -f /tmp/gaia_start_*.log
readonly LOG_FILE="/tmp/gaia_start_${DATETIME}.log"
readonly SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
. "${SCRIPT_DIR}/utils/logging.sh"

# Default values
FOREGROUND=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--foreground)
            FOREGROUND=true
            shift
            ;;
        *)
            die "Unknown parameter: $1"
            ;;
    esac
done

# Check if GAIA_DIR is set
if [[ -z "${GAIA_DIR:-}" ]]; then
    die "GAIA_DIR environment variable is not set. Please source your profile or run the install script first."
fi

# Check if the directory exists
if [[ ! -d "$GAIA_DIR" ]]; then
    die "Gaia directory not found at $GAIA_DIR. Please check your installation."
fi

# Create logs directory if it doesn't exist
mkdir -p "${GAIA_DIR}/logs" || die "Failed to create logs directory"

# Check that a PID is really a Gaia process, and not a recycled PID.
# Gaia renames itself to "gaia" (setproctitle), but only once its imports are
# done: until then it is still "<python> -m gaia". Matching the command line
# covers both, so an instance still starting up is not mistaken for a dead one.
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

# Check if already running — prefer PID file
if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
    PID=$(cat "${GAIA_DIR}/gaia.pid")
    if kill -0 "$PID" 2>/dev/null && is_gaia_proc "$PID"; then
        log WARN "Gaia is already running with PID $PID"
        log INFO "If you want to restart, please run: gaia restart"
        exit 1
    fi
    # Stale PID file — process is gone, clean up and continue
    rm -f "${GAIA_DIR}/gaia.pid"
fi
# Fallback to pgrep if no PID file — match both the renamed process and one
# still starting up ("<python> -m gaia"); -f on the latter is safe as it
# cannot match this script's own command line.
PID=$({ pgrep -x "gaia"; pgrep -f -- "-m gaia"; } 2>/dev/null | head -n1 || true)
if [[ -n "$PID" ]]; then
    log WARN "Gaia is already running with PID $PID"
    log INFO "If you want to restart, please run: gaia restart"
    exit 1
fi

# Change to Gaia directory
cd "$GAIA_DIR" || die "Failed to change to Gaia directory: $GAIA_DIR"

# Check if virtual environment exists
if [[ ! -d ".venv" ]]; then
    die "Python virtual environment not found. Please run the install script first."
fi

# Activate virtual environment
# shellcheck source=/dev/null
if ! source ".venv/bin/activate"; then
    die "Failed to activate Python virtual environment"
fi

# Start Gaia
log INFO "Starting Gaia..."

if [[ "$FOREGROUND" = true ]]; then
    log INFO "Running in foreground mode (logs will be shown in terminal)"
    # Run Gaia in the foreground
    EXIT_CODE=0
    python3 -m gaia || EXIT_CODE=$?
    
    # Clean up and exit with the same code as Gaia
    deactivate ||
        log WARN "Failed to deactivate virtual environment"
    log INFO "Gaia process exited with code $EXIT_CODE"
    exit $EXIT_CODE
else
    # Run Gaia in the background and log the PID
    nohup python3 -m gaia > "${GAIA_DIR}/logs/stdout" 2>&1 &
    GAIA_PID=$!
    echo "$GAIA_PID" > "${GAIA_DIR}/gaia.pid"
    log INFO "Gaia started in background mode"
    log INFO "Gaia stdout and stderr output redirected to ${GAIA_DIR}/logs/stdout"

    deactivate ||
        log WARN "Failed to deactivate virtual environment"

    # Verify that Gaia started successfully
    sleep 2

    # Check if process is still running
    if ! kill -0 "$GAIA_PID" 2>/dev/null; then
        # Process died, check error log
        # Clean up PID file
        [[ -f "${GAIA_DIR}/gaia.pid" ]] && rm -f "${GAIA_DIR}/gaia.pid"
        die "Process failed to start."
    fi

    log SUCCESS "Gaia started successfully with PID $GAIA_PID"

    exit 0
fi
