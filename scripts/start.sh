#!/bin/bash

if pgrep -x "gaia" > /dev/null; then
  echo "An instance of Gaia is already running, this might lead to conflicts"
fi

echo "Starting Gaia";
cd "$GAIA_DIR" || echo "\$GAIA_DIR is not set, exiting" exit
source python_venv/bin/activate
python3 main.py
