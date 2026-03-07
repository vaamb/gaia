#!/bin/bash

# Exit on error, unset variable, and pipefail
set -euo pipefail

INSTALL_DIR="${1:-}"

# Validate argument
if [[ -z "${INSTALL_DIR}" ]]; then
  echo "Usage: $0 <gaia_install_dir>" >&2
  exit 1
fi

cat > "${INSTALL_DIR}/pyproject.toml" << EOF
[project]
name = "gaia"
version = "0.10.0"
description = "An app to manage greenhouses, terrariums and aquariums"
requires-python = ">=3.11"
dependencies = ["gaia"]

[tool.uv.sources]
gaia = { workspace = true }

[tool.uv.workspace]
members = ["lib/*"]
EOF
