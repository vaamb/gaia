#!/bin/bash

# Exit on error, unset variables, and pipeline errors
set -euo pipefail

# Constants
readonly MIN_PYTHON_VERSION="3.11"
readonly GAIA_VERSION="0.9.0"
readonly GAIA_REPO="https://github.com/vaamb/gaia.git"

# Colors for output
readonly RED='\033[0;31m'
readonly YELLOW='\033[1;33m'
readonly GREEN='\033[0;32m'
readonly LIGHT_YELLOW='\033[93m'
readonly NC='\033[0m' # No Color

# Function to log messages
log() {
    case "$1" in
        "INFO")
            echo -e "${LIGHT_YELLOW}$2${NC}"
            ;;
        "WARN")
            echo -e "${YELLOW}Warning: $2${NC}"
            ;;
        "ERROR")
            echo -e "${RED}Error: $2${NC}"
            exit 1
            ;;
        "SUCCESS")
            echo -e "${GREEN}$2${NC} "
            ;;
        *)
            echo -e "$1"
            ;;
    esac
}

log "INFO" "Installing Gaia"

# Check if running as root
if [ "${EUID}" -eq 0 ]; then
    log "WARN" "Running as root is not recommended. Please run as a regular user with sudo privileges."
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "INFO" "Installation cancelled by user."
        exit 1
    fi
fi

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}


# Check system requirements
log "INFO" "Checking system requirements..."

# Check for required commands
for cmd in git python3 systemctl; do
    if ! command_exists "${cmd}"; then
        log "ERROR" "$cmd is required but not installed."
    fi
done

# Check Python version
python3 -c "import sys; exit(0) if sys.version_info >= (${MIN_PYTHON_VERSION//./,}) else exit(1)" ||
    log "ERROR" "Python ${MIN_PYTHON_VERSION} or higher is required"

log "SUCCESS" "System requirements met"


# Enable hardware interfaces
log "INFO" "Configuring hardware interfaces..."

# Backup original config
local config_file="/boot/config.txt"
local config_backup="${config_file}.bak.$(date +%Y%m%d%H%M%S)"

if [ ! -f "${config_file}" ]; then
    log "WARN" "${config_file} not found. This might not be a Raspberry Pi."
fi

# Create backup
if [ ! -f "${config_backup}" ]; then
    sudo cp "${config_file}" "${config_backup}"
    log "INFO" "Created backup of ${config_file} as ${config_backup}"
fi

# Function to add configuration
add_config() {
    local pattern=$1
    local line=$2

    if ! grep -q "^${pattern}" "${config_file}"; then
        echo "${line}" >> "${config_file}";
    fi
}

# Enable I2C
# Enable I2C kernel module
if [ -f /etc/modules ]; then
    if ! grep -q "^i2c-dev" /etc/modules; then
        echo "i2c-dev" >> /etc/modules;
    fi
fi

add_config "dtparam=i2c_arm=" "dtparam=i2c_arm=on"

# Enable 1-Wire
add_config "dtoverlay=w1-gpio" "dtoverlay=w1-gpio"

# Enable camera
add_config "gpu_mem=" "gpu_mem=128"
add_config "start_x=" "start_x=1"

log "SUCCESS" "Hardware interfaces configured. A reboot may be required for changes to take effect."


# Install system dependencies
log "INFO" "Installing system dependencies..."

local deps=(
    "libffi-dev"
    "libssl-dev"
    "python3-venv"
    "python3-pip"
)

sudo apt update
if ! sudo apt install -y "${deps[@]}"; then
    log "ERROR" "Failed to install system dependencies."
fi

log "INFO" "System dependencies installed successfully."

# Create Gaia directory
GAIA_DIR="${PWD}/gaia"
mkdir -p "${GAIA_DIR}" || log "ERROR" "Failed to create directory: ${GAIA_DIR}"
cd "${GAIA_DIR}" || log "ERROR" "Failed to change to directory: ${GAIA_DIR}"

# Create required subdirectories
for dir in logs scripts lib; do
    mkdir -p "${GAIA_DIR}/${dir}" ||
        log "ERROR" "Failed to create directory: ${GAIA_DIR}/${dir}"
done

# Setup Python virtual environment
log "INFO" "Creating Python virtual environment..."
if [ ! -d "python_venv" ]; then
    python3 -m venv "${GAIA_DIR}/python_venv" ||
        log "ERROR" "Failed to create Python virtual environment"
else
    log "WARN" "Virtual environment already exists at ${GAIA_DIR}/python_venv"
fi
log "SUCCESS" "Python virtual environment created successfully."


# Activate virtual environment
# shellcheck source=/dev/null
source "${GAIA_DIR}/python_venv/bin/activate" ||
    log "ERROR" "Failed to activate Python virtual environment"

# Get Gaia repository
log "INFO" "Cloning Gaia repository..."
if [ ! -d "${GAIA_DIR}/lib/gaia" ]; then
    if ! git clone --branch "${GAIA_VERSION}" "${GAIA_REPO}" \
            "${GAIA_DIR}/lib/gaia" > /dev/null 2>&1; then
        log "ERROR" "Failed to clone Gaia repository"
    fi

    cd "${GAIA_DIR}/lib/gaia" ||
        log "ERROR" "Failed to enter Gaia directory"
else
    log "ERROR" "Gaia installation detected at ${GAIA_DIR}/lib/gaia. Please update using the update script."
fi

log "INFO" "Updating Python packaging tools..."
pip install --upgrade pip setuptools wheel ||
    log "ERROR" "Failed to update Python packaging tools"

# Install Gaia
log "INFO" "Installing Gaia and its dependencies..."
pip install -e . || log "ERROR" "Failed to install Gaia and its dependencies"

log "SUCCESS" "Gaia installed successfully"


# Copy scripts
cp -r "${GAIA_DIR}/lib/gaia/scripts/"* "${GAIA_DIR}/scripts/" ||
    log "ERROR" "Failed to copy scripts"
chmod +x "${GAIA_DIR}/scripts/"*.sh

# Update .profile
log "INFO" "Updating shell profile..."

${GAIA_DIR}/scripts/gen_profile.sh "${GAIA_DIR}" ||
    log "ERROR" "Failed to update shell profile"

info "Setting up systemd service..."
SERVICE_FILE="${GAIA_DIR}/scripts/gaia.service"

${GAIA_DIR}/scripts/gen_service.sh "${GAIA_DIR}" "${SERVICE_FILE}" ||
    log "ERROR" "Failed to generate systemd service"

# Install service
if ! sudo cp "${SERVICE_FILE}" "/etc/systemd/system/gaia.service"; then
    log "WARN" "Failed to copy service file. You may need to run with sudo."
else
    sudo systemctl daemon-reload ||
        log "WARN" "Failed to reload systemd daemon"
fi

log "SUCCESS" "Systemd service set up successfully"

# Installation complete
log "SUCCESS"    "\nInstallation completed successfully!"
echo -e "\nTo get started:"
echo -e "1. Source your profile: ${YELLOW}source ~/.profile${NC}"
echo -e "2. Start Gaia: ${YELLOW}gaia start${NC}"
echo -e "\nOther useful commands:"
echo -e "  gaia stop     # Stop the service"
echo -e "  gaia restart  # Restart the service"
echo -e "  gaia status   # Check service status"
echo -e "  gaia logs     # View logs"
echo -e "\nTo run as a system service:"
echo -e "  sudo systemctl start gaia.service"
echo -e "  sudo systemctl enable gaia.service  # Start on boot"
