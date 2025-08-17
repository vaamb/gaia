#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

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
            log ERROR "Unknown parameter: $1"
            exit 1
            ;;
    esac
done

# Load logging functions
readonly DATETIME=$(date +%Y%m%d_%H%M%S)
readonly LOG_FILE="/tmp/gaia_start_${DATETIME}.log"
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

# Create logs directory if it doesn't exist
mkdir -p "${GAIA_DIR}/logs" || log ERROR "Failed to create logs directory"

# Check if already running
if pgrep -x "gaia" > /dev/null; then
    PID=$(pgrep -x "gaia" | head -n 1)
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
log INFO "Starting Gaia..."

if [ "$FOREGROUND" = true ]; then
    log INFO "Running in foreground mode (logs will be shown in terminal)"
    # Run Gaia in the foreground
    python3 -m gaia
    EXIT_CODE=$?
    
    # Clean up and exit with the same code as Gaia
    deactivate || log WARN "Failed to deactivate virtual environment"
    log INFO "Gaia process exited with code $EXIT_CODE"
    exit $EXIT_CODE
else
    # Run Gaia in the background and log the PID
    nohup python3 -m gaia > "${GAIA_DIR}/logs/stdout" 2>&1 &
    log INFO "Gaia started in background mode"
    log INFO "Gaia stdout and stderr output redirected to ${GAIA_DIR}/logs/stdout"
    
    deactivate ||
        log ERROR "Failed to deactivate virtual environment"
    
    GAIA_PID=$!
    echo "$GAIA_PID" > "${GAIA_DIR}/gaia.pid"
fi

# Verify that Gaia started successfully
sleep 2

# Check if process is still running
if ! kill -0 "$GAIA_PID" 2>/dev/null; then
    # Process died, check error log
    log ERROR "Process failed to start."
    # Clean up PID file
    [[ -f "${GAIA_DIR}/gaia.pid" ]] && rm -f "${GAIA_DIR}/gaia.pid"
    exit 1
fi

log SUCCESS "Gaia started successfully with PID $GAIA_PID"

exit 0
