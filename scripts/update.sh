#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
DRY_RUN=false
FORCE_UPDATE=false

# Function to display help
show_help() {
    echo "Usage: $0 [options]"
    echo "Options:"
    echo "  -d, --dry-run    Show what would be updated without making changes"
    echo "  -f, --force      Force update even if already at the latest version"
    echo "  -h, --help       Show this help message and exit"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -f|--force)
            FORCE_UPDATE=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            log "ERROR" "Unknown option: $1"
            ;;
    esac
done

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

# Check if GAIA_DIR is set
if [[ -z "${GAIA_DIR:-}" ]]; then
    log "ERROR" "GAIA_DIR environment variable is not set. Please source your profile or run the install script first."
fi

# Check if the directory exists
if [[ ! -d "$GAIA_DIR" ]]; then
    log "ERROR" "Gaia directory not found at $GAIA_DIR. Please check your installation."
fi

cd "$GAIA_DIR" || log "ERROR" "Failed to change to Gaia directory: $GAIA_DIR"

# Check if virtual environment exists
if [[ ! -d "python_venv" ]]; then
    log "ERROR" "Python virtual environment not found. Please run the install script first."
fi

# Activate virtual environment
# shellcheck source=/dev/null
if ! source "python_venv/bin/activate"; then
    log "ERROR" "Failed to activate Python virtual environment"
fi

# Function to update a single repository
update_repo() {
    local repo_dir="$1"
    local repo_name=$(basename "$repo_dir")

    log "INFO" "\nChecking $repo_name..."

    if [[ ! -d "$repo_dir/.git" ]]; then
        log "WARN" "$repo_dir is not a git repository. Skipping."
        return 1
    fi

    cd "$repo_dir" || return 1

    # Get current branch and status
    local current_branch
    current_branch=$(git rev-parse --abbrev-ref HEAD)
    local has_changes
    has_changes=$(git status --porcelain)

    if [[ -n "$has_changes" ]]; then
        log "WARN" "$repo_name has uncommitted changes. Stashing them..."
        if [[ "$DRY_RUN" == false ]]; then
            git stash save "Stashed by Gaia update script"
        fi
    fi

    # Fetch all updates
    log "INFO" "Fetching updates for $repo_name..."
    if [[ "$DRY_RUN" == false ]]; then
        git fetch --all --tags --prune
    fi

    # Get current and latest tags
    local current_tag
    current_tag=$(git describe --tags 2>/dev/null || echo "No tags found")
    local latest_tag
    latest_tag=$(git describe --tags "$(git rev-list --tags --max-count=1 2>/dev/null)" 2>/dev/null || echo "No tags found")

    log "Current version: $current_tag"
    log "Latest version:  $latest_tag"

    if [[ "$current_tag" == "$latest_tag" && "$FORCE_UPDATE" == false ]]; then
        log "WARN" "$repo_name is already at the latest version. Use -f to force update."
        return 0
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log "INFO" "[DRY RUN] Would update $repo_name from $current_tag to $latest_tag"
        return 0
    fi

    # Checkout the latest tag
    log "INFO" "Updating $repo_name to $latest_tag..."
    git checkout "$latest_tag"

    # Install the package in development mode
    if [[ -f "pyproject.toml" ]]; then
        log "INFO" "Installing $repo_name..."
        pip install -e .
    fi

    # Return to the original branch if not on a detached HEAD
    if [[ "$current_branch" != "HEAD" ]]; then
        log "INFO" "Returning to branch $current_branch..."
        git checkout "$current_branch"

        # Apply stashed changes if any
        if [[ -n "$has_changes" ]]; then
            log "INFO" "Restoring stashed changes..."
            git stash pop
        fi
    fi

    log "SUCCESS" "$repo_name updated to $latest_tag"
    return 0
}

# Main update process
log "INFO" "Starting Gaia update..."

# Update gaia
update_repo "${GAIA_DIR}/lib/gaia"

# Deactivate virtual environment
deactivate 2>/dev/null || true

log "SUCCESS" "\nUpdate complete!"

# Show final instructions
if [[ "$DRY_RUN" == false ]]; then
    echo -e "\nTo apply the updates, please restart the Gaia service with one of these commands:"
    echo -e "  ${YELLOW}gaia restart${NC}    # If using the gaia command"
    echo -e "  ${YELLOW}sudo systemctl restart gaia.service${NC}  # If using systemd"
else
    echo -e "\nThis was a dry run. No changes were made. Use ${YELLOW}$0${NC} without --dry-run to perform the updates."
fi

exit 0
