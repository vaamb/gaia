#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

INSTALL_DIR="${1:-}"

# Validate argument
if [[ -z "${INSTALL_DIR}" ]]; then
  echo "Usage: $0 <ouranos_install_dir>" >&2
  exit 1
fi

# Remove existing Gaia section if it exists
if grep -q "#>>>Gaia variables>>>" "${HOME}/.profile"; then
    sed -i "/#>>>Gaia variables>>>/,/#<<<Gaia variables<<</d" "${HOME}/.profile"
fi

cat >> "${HOME}/.profile" << EOF
#>>>Gaia variables>>>
# Gaia root directory
export GAIA_DIR="${INSTALL_DIR}"

# Gaia utility function to manage the application
gaia() {
  case \$1 in
    start) "\${GAIA_DIR}/scripts/start.sh" ;;
    stop) "\${GAIA_DIR}/scripts/stop.sh" ;;
    restart) "\${GAIA_DIR}/scripts/stop.sh" && "\${GAIA_DIR}/scripts/start.sh" ;;
    status) systemctl --user status gaia.service ;;
    logs) tail -f "\${GAIA_DIR}/logs/gaia.log" ;;
    stdout) tail -f "\${GAIA_DIR}/logs/stdout" ;;
    update) "\${GAIA_DIR}/scripts/update.sh" ;;
    *) echo "Usage: gaia {start|stop|restart|status|logs|update}" ;;
  esac
}
complete -W "start stop restart status logs stdout update" gaia
#<<<Gaia variables<<<
EOF

# shellcheck source=/dev/null
source "${HOME}/.profile"
