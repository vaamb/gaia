#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
info() {
    echo -e "${GREEN}[INFO ]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN ]${NC} $1"
}

error_exit() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
    exit 1
}

# Function to check if Gaia is running
is_running() {
    if pgrep -f "python3 -m gaia" > /dev/null; then
        return 0
    else
        return 1
    fi
}

# Check if GAIA_DIR is set
if [[ -z "${GAIA_DIR:-}" ]]; then
    error_exit "GAIA_DIR environment variable is not set. Please source your profile or run the install script first."
fi

# Check if the directory exists
if [[ ! -d "$GAIA_DIR" ]]; then
    error_exit "Gaia directory not found at $GAIA_DIR. Please check your installation."
fi

# Ensure logs directory exists
mkdir -p "${GAIA_DIR}/logs" || error_exit "Failed to create logs directory"

# Log stop attempt
info "$(date) - Attempting to stop Gaia..."

# Check if Gaia is running
if ! is_running; then
    info "No running instance of Gaia found."

    # Clean up PID file if it exists
    if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
        warn "Stale PID file found. Cleaning up..."
        rm -f "${GAIA_DIR}/gaia.pid"
    fi

    exit 0
fi

# Get the PID of the running process
GAIA_PID=$(pgrep -f "python3 -m gaia")

if [[ -z "$GAIA_PID" ]]; then
    error_exit "Could not determine Gaia process ID"
fi

info "Stopping Gaia (PID: $GAIA_PID)..."

# Send SIGTERM (15) - graceful shutdown
if kill -15 "$GAIA_PID" 2>/dev/null; then
    # Wait for the process to terminate
    TIMEOUT=10  # seconds
    while (( TIMEOUT-- > 0 )) && kill -0 "$GAIA_PID" 2>/dev/null; do
        sleep 1
        echo -n "."
    done
    echo

    # Check if process is still running
    if kill -0 "$GAIA_PID" 2>/dev/null; then
        warn "Graceful shutdown failed. Force killing the process..."
        kill -9 "$GAIA_PID" 2>/dev/null || true
    fi

    # Clean up PID file
    if [[ -f "${GAIA_DIR}/gaia.pid" ]]; then
        rm -f "${GAIA_DIR}/gaia.pid"
    fi

    # Verify the process was actually stopped
    if is_running; then
        error_exit "Failed to stop Gaia. Process still running with PID: $(pgrep -f "python3 -m gaia")"
    fi

    info "Gaia stopped successfully."
    exit 0
else
    error_exit "Failed to send stop signal to Gaia (PID: $GAIA_PID). You may need to run with sudo."
fi
