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

# Check if GAIA_DIR is set
if [[ -z "${GAIA_DIR:-}" ]]; then
    error_exit "GAIA_DIR environment variable is not set. Please source your profile or run the install script first."
fi

# Check if the directory exists
if [[ ! -d "$GAIA_DIR" ]]; then
    error_exit "Gaia directory not found at $GAIA_DIR. Please check your installation."
fi

# Create logs directory if it doesn't exist
mkdir -p "${GAIA_DIR}/logs" || error_exit "Failed to create logs directory"

# Redirect all output to log file
exec > >(tee -a "${GAIA_DIR}/logs/gaia.log") 2>&1

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
    warn "Gaia is already running with PID $PID"
    info "If you want to restart, please run: gaia restart"
    exit 0
fi

# Change to Gaia directory
cd "$GAIA_DIR" || error_exit "Failed to change to Gaia directory: $GAIA_DIR"

# Check if virtual environment exists
if [[ ! -d "python_venv" ]]; then
    error_exit "Python virtual environment not found. Please run the install script first."
fi

# Activate virtual environment
# shellcheck source=/dev/null
if ! source "python_venv/bin/activate"; then
    error_exit "Failed to activate Python virtual environment"
fi

# Start Gaia
info "$(date) - Starting Gaia..."
info "Logging to: ${GAIA_DIR}/logs/gaia.log"

# Run Gaia in the background and log the PID
nohup python3 -m gaia > "${GAIA_DIR}/logs/gaia.log" 2>&1 &
GAIA_PID=$!

echo "$GAIA_PID" > "${GAIA_DIR}/gaia.pid"

# Verify that Gaia started successfully
sleep 2
if ! kill -0 "$GAIA_PID" 2>/dev/null; then
    error_exit "Failed to start Gaia. Check the logs at ${GAIA_DIR}/logs/gaia.log for details.
$(tail -n 20 "${GAIA_DIR}/logs/gaia.log")"
fi

info "Gaia started successfully with PID $GAIA_PID"
info "To view logs: tail -f ${GAIA_DIR}/logs/gaia.log"

exit 0
