#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

# Load logging functions
readonly DATETIME=$(date +%Y%m%d_%H%M%S)
readonly LOG_FILE="/tmp/gaia_start_${DATETIME}.log"
. "./logging.sh"

# Check if GAIA_DIR is set
if [[ -z "${GAIA_DIR:-}" ]]; then
    log ERROR "GAIA_DIR environment variable is not set. Please source your profile or run the install script first."
fi

# Check if the directory exists
if [[ ! -d "$GAIA_DIR" ]]; then
    log ERROR "Gaia directory not found at $GAIA_DIR. Please check your installation."
fi

# Create logs directory if it doesn't exist
mkdir -p "${GAIA_DIR}/logs" || log ERROR "Failed to create logs directory"

# Redirect all output to log file
exec > >(tee -a "${GAIA_DIR}/logs/gaia.log") 2>&1

trap '' HUP

# Function to check if Gaia is running
is_running() {
    if pgrep -f "python3 -m gaia" > /dev/null; then
        return 0
    else
        return 1
    fi
}

# Check if already running
if is_running; then
    PID=$(pgrep -f "python3 -m gaia")
    log WARN "Gaia is already running with PID $PID"
    log INFO "If you want to restart, please run: gaia restart"
    exit 0
fi

# Change to Gaia directory
cd "$GAIA_DIR" || log ERROR "Failed to change to Gaia directory: $GAIA_DIR"

# Check if virtual environment exists
if [[ ! -d "python_venv" ]]; then
    log ERROR "Python virtual environment not found. Please run the install script first."
fi

# Activate virtual environment
# shellcheck source=/dev/null
if ! source "python_venv/bin/activate"; then
    log ERROR "Failed to activate Python virtual environment"
fi

# Start Gaia
log INFO "$(date) - Starting Gaia..."
log INFO "Logging to: ${GAIA_DIR}/logs/gaia.log"

# Run Gaia in the background and log the PID
python3 -m gaia
GAIA_PID=$!

echo "$GAIA_PID" > "${GAIA_DIR}/gaia.pid"

# Verify that Gaia started successfully
sleep 5
if ! kill -0 "$GAIA_PID" 2>/dev/null; then
    log ERROR "Failed to start Gaia. Check the logs at ${GAIA_DIR}/logs/gaia.log for details.
$(tail -n 20 "${GAIA_DIR}/logs/gaia.log")"
fi

log INFO "Gaia started successfully with PID $GAIA_PID"
log INFO "To view logs: tail -f ${GAIA_DIR}/logs/gaia.log"

exit 0
