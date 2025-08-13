#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

# Load logging functions
readonly DATETIME=$(date +%Y%m%d_%H%M%S)
readonly LOG_FILE="/tmp/gaia_stop_${DATETIME}.log"
readonly SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
. "${SCRIPT_DIR}/logging.sh"

# Check if GAIA_DIR is set
if [[ -z "${GAIA_DIR:-}" ]]; then
    log ERROR "GAIA_DIR environment variable is not set. Please source your profile or run the install script first."
fi

# Check if the directory exists
if [[ ! -d "$GAIA_DIR" ]]; then
    log ERROR "Gaia directory not found at $GAIA_DIR. Please check your installation."
fi

# Ensure logs directory exists
mkdir -p "${GAIA_DIR}/logs" || log ERROR "Failed to create logs directory"

# Log stop attempt
log INFO "Attempting to stop Gaia..."

# Check if Gaia is running
is_running() {
    if pgrep -f "gaia" > /dev/null; then
        return 0
    else
        return 1
    fi
}

if ! is_running; then
    log INFO "No running instance of Gaia found."

    # Clean up PID file if it exists
    if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
        log WARN "Stale PID file found. Cleaning up..."
        rm -f "${GAIA_DIR}/gaia.pid"
    fi

    exit 0
fi

# Get the PID of the running process
GAIA_PID=$(pgrep -f "gaia")

if [[ -z "$GAIA_PID" ]]; then
    log ERROR "Could not determine Gaia process ID"
fi

log INFO "Stopping Gaia (PID: $GAIA_PID)..."

# Send SIGTERM (15) - graceful shutdown
if kill -15 "$GAIA_PID" 2>/dev/null; then
    # Wait for the process to terminate
    TIMEOUT=10  # seconds
    while (( TIMEOUT-- > 0 )) && kill -0 "$GAIA_PID" 2>/dev/null; do
        sleep 1
        echo -n "."
    done

    # Check if process is still running
    if kill -0 "$GAIA_PID" 2>/dev/null; then
        log WARN "Graceful shutdown failed. Force killing the process..."
        kill -9 "$GAIA_PID" 2>/dev/null || true
    fi

    # Clean up PID file
    if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
        rm -f "${GAIA_DIR}/gaia.pid"
    fi

    # Verify the process was actually stopped
    if is_running; then
        log ERROR "Failed to stop Gaia. Process still running with PID: ${GAIA_PID}."
    fi

    log SUCCESS "Gaia stopped successfully."
    exit 0
else
    log ERROR "Failed to send stop signal to Gaia (PID: ${GAIA_PID}). You may need to run with sudo."
fi
