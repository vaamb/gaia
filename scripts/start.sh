#!/bin/bash

exec </dev/null >"$GAIA_DIR/logs/stdout" 2>&1

trap '' HUP

if pgrep -x "gaia" > /dev/null; then
  echo "An instance of Gaia is already running, this might lead to conflicts"
fi

echo "Starting Gaia";
cd "$GAIA_DIR" || echo "\$GAIA_DIR is not set, exiting" exit
source $GAIA_DIR/python_venv/bin/activate
python3 $GAIA_DIR/main.py
python3 -m gaia
