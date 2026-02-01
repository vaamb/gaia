#!/bin/bash

# Exit on error, unset variables, and pipeline errors
set -euo pipefail

# Version requirements
readonly MIN_PYTHON_VERSION="3.11"
readonly GAIA_VERSION="0.10.0"
readonly GAIA_REPO="https://github.com/vaamb/gaia.git"

# Default values
readonly GAIA_DIR="${PWD}/gaia"

# Load logging functions
readonly DATETIME=$(date +%Y%m%d_%H%M%S)
readonly LOG_FILE="/tmp/gaia_install_${DATETIME}.log"
readonly SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
. "${SCRIPT_DIR}/logging.sh"

check_root() {
    # Check if running as root
    if [ "${EUID}" -eq 0 ]; then
        log WARN "Running as root is not recommended. Please run as a regular user with sudo privileges."
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log INFO "Installation cancelled by user."
            exit 0
        fi
    fi

    # Check if user has sudo privileges
    if ! sudo -n true 2>/dev/null; then
        log WARN "You may be prompted for sudo password during installation."
    fi
}

check_requirements() {
    local missing_deps=()
    local cmd

    # Function to check if command exists
    command_exists() {
        command -v "$1" >/dev/null 2>&1
    }

    # Check for required commands
    for cmd in git python3 systemctl; do
        if ! command_exists "${cmd}"; then
            missing_deps+=("${cmd}")
        fi
    done

    if [ ${#missing_deps[@]} -gt 0 ]; then
        log WARN "Missing required dependencies: ${missing_deps[*]}"
        log INFO "Attempting to install missing dependencies..."
            sudo apt update && sudo apt install -y "${missing_deps[@]}" ||
                log ERROR "Failed to install required packages"
    fi

    # Check Python version
    python3 -c "import sys; exit(0) if sys.version_info >= (${MIN_PYTHON_VERSION//./,}) else exit(1)" ||
        log ERROR "Python ${MIN_PYTHON_VERSION} or higher is required"
}

is_pi () {
    ARCH=$(dpkg --print-architecture)
    if [ "$ARCH" = "armhf" ] || [ "$ARCH" = "arm64" ] ; then
        return 0
    else
        return 1
    fi
}

configure_hardware() {
    # Backup original config
    local config_file="/boot/config.txt"
    local config_backup="${config_file}.bak.$(date +%Y%m%d%H%M%S)"

    IS_RASPI=true
    if [ ! -f "${config_file}" ]; then
        log WARN "${config_file} not found. This might not be a Raspberry Pi."
        IS_RASPI=false
    fi

    # Create backup
    if [ "${IS_RASPI}" = "true" ] && [ ! -f "${config_backup}" ]; then
        sudo cp "${config_file}" "${config_backup}"
        log INFO "Created backup of ${config_file} as ${config_backup}"
    fi

    # Function to add configuration
    add_config() {
        local pattern=$1
        local line=$2

        if ! grep -q "^${pattern}" "${config_file}"; then
            echo "${line}" | sudo tee -a "${config_file}" > /dev/null
        fi
    }

    # Enable I2C
    if [ -f /etc/modules ]; then
        if ! grep -q "^i2c-dev" /etc/modules; then
            echo "i2c-dev" | sudo tee -a "/etc/modules" > /dev/null
        fi
    fi

    add_config "dtparam=i2c_arm=" "dtparam=i2c_arm=on"

    # Enable 1-Wire
    add_config "dtoverlay=w1-gpio" "dtoverlay=w1-gpio"

    # Enable camera
    add_config "gpu_mem=" "gpu_mem=128"
    add_config "start_x=" "start_x=1"

    log SUCCESS "Hardware interfaces configured. A reboot may be required for changes to take effect."
}

install_requirements() {
    # Install system dependencies
    sudo apt update
    if ! sudo apt install -y libffi-dev libssl-dev python3-venv python3-pip; then
        log ERROR "Failed to install system dependencies."
    fi
}

create_directories() {
    # Create Gaia directory
    mkdir -p "${GAIA_DIR}" || log ERROR "Failed to create directory: ${GAIA_DIR}"
    cd "${GAIA_DIR}" || log ERROR "Failed to change to directory: ${GAIA_DIR}"

    # Create required subdirectories
    for dir in logs scripts lib; do
        mkdir -p "${GAIA_DIR}/${dir}" ||
            log ERROR "Failed to create directory: ${GAIA_DIR}/${dir}"
    done
}

setup_python_venv() {
    # Setup Python virtual environment
    if [ ! -d "python_venv" ]; then
        python3 -m venv "${GAIA_DIR}/python_venv" ||
            log ERROR "Failed to create Python virtual environment"
    else
        log WARN "Virtual environment already exists at ${GAIA_DIR}/python_venv"
    fi
}

install_gaia() {
    # Activate virtual environment
    # shellcheck source=/dev/null
    source "${GAIA_DIR}/python_venv/bin/activate" ||
        log ERROR "Failed to activate Python virtual environment"

    # Get Gaia repository
    log INFO "Cloning Gaia repository..."
    if [ ! -d "${GAIA_DIR}/lib/gaia" ]; then
        if ! git clone --branch "${GAIA_VERSION}" "${GAIA_REPO}" \
                "${GAIA_DIR}/lib/gaia" > /dev/null 2>&1; then
            log ERROR "Failed to clone Gaia repository"
        fi

        cd "${GAIA_DIR}/lib/gaia" ||
            log ERROR "Failed to enter Gaia directory"
    else
        log ERROR "Gaia installation detected at ${GAIA_DIR}/lib/gaia. Please update using the update script."
    fi

    log INFO "Updating Python packaging tools..."
    pip install --upgrade pip setuptools wheel ||
        log ERROR "Failed to update Python packaging tools"

    # Install Gaia
    log INFO "Installing Gaia and its dependencies..."
    pip install -e . || log ERROR "Failed to install Gaia and its dependencies"
    deactivate ||
        log ERROR "Failed to deactivate virtual environment"
}

copy_scripts() {
    # Copy scripts
    cp -r "${GAIA_DIR}/lib/gaia/scripts/"* "${GAIA_DIR}/scripts/" ||
        log ERROR "Failed to copy scripts"
    chmod +x "${GAIA_DIR}/scripts/"*.sh
}

update_profile() {
    # Update .profile
    ${GAIA_DIR}/scripts/gen_profile.sh "${GAIA_DIR}" ||
        log ERROR "Failed to update shell profile"

    log INFO "Setting up systemd service..."
}

install_service() {
    local service_file="${GAIA_DIR}/scripts/gaia.service"

    ${GAIA_DIR}/scripts/gen_service.sh "${GAIA_DIR}" "${service_file}" ||
        log ERROR "Failed to generate systemd service"

    # Install service
    if ! sudo cp "${service_file}" "/etc/systemd/system/gaia.service"; then
        log WARN "Failed to copy service file. You may need to run with sudo."
    else
        sudo systemctl daemon-reload ||
            log WARN "Failed to reload systemd daemon"
    fi
}

# Cleanup function to run on exit
cleanup() {
    local exit_code=$?

    if [ "${exit_code}" -ne 0 ]; then
        log ERROR "Installation failed. Check the log file for details: ${LOG_FILE}"
        rm -r "${GAIA_DIR}"
    else
        log SUCCESS "Installation completed successfully!"
    fi

    # Reset terminal colors
    echo -e "${NC}"
    exit ${exit_code}
}

main() {
    # Set up trap for cleanup on exit
    trap cleanup EXIT

    log INFO "Starting Gaia installation (v${GAIA_VERSION})"

    # Check if already installed
    if [ -d "${GAIA_DIR}" ]; then
        log ERROR "Gaia appears to be already installed at ${GAIA_DIR}"
    fi

    # Check requirements and permissions
    log INFO "Checking system requirements..."
    check_root
    check_requirements
    log SUCCESS "System requirements met"

    if is_pi; then
        log INFO "This is a Raspberry Pi. Configuring hardware interfaces..."
        configure_hardware
    else
        log INFO "This is not a Raspberry Pi. Skipping hardware interface configuration."
    fi

    log INFO "Installing system dependencies..."
    install_requirements
    log SUCCESS "System dependencies installed successfully."

    log INFO "Creating directories..."
    create_directories
    log SUCCESS "Directories created successfully."

    log INFO "Creating Python virtual environment..."
    setup_python_venv
    log SUCCESS "Python virtual environment created successfully."

    log INFO "Installing Gaia ..."
    install_gaia
    log SUCCESS "Gaia installed successfully"

    log INFO "Making scripts more easily accessible..."
    copy_scripts

    log INFO "Updating shell profile..."
    update_profile
    log SUCCESS "Shell profile updated successfully"

    log INFO "Setting up systemd service..."
    install_service
    log SUCCESS "Systemd service set up successfully"

    # Display completion message
    echo -e "\n${GREEN}✔ Installation completed successfully!${NC}"
    echo -e "\n${YELLOW}Next steps:${NC}"
    echo -e "1. Source your profile: ${YELLOW}source ~/.profile${NC}"
    echo -e "2. Start Gaia: ${YELLOW}gaia start${NC}"
    echo -e "\n${YELLOW}Other useful commands:${NC}"
    echo -e "  gaia stop     # Stop the service"
    echo -e "  gaia restart  # Restart the service"
    echo -e "  gaia status   # Check service status"
    echo -e "  gaia logs     # View logs"
    echo -e "  gaia --help   # Show help"
    echo -e "\n${YELLOW}To run as a system service:${NC}"
    echo -e "  sudo systemctl start gaia.service"
    echo -e "  sudo systemctl enable gaia.service  # Start on boot"
    echo -e "\n${YELLOW}For troubleshooting, check the log file:${NC} ${LOG_FILE}"
}

main "$@"
