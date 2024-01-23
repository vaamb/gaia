#!/bin/bash

if [ -z ${GAIA_DIR+x} ];
  then echo "\$GAIA_DIR is not set, exiting." exit;
fi

exec </dev/null >"$GAIA_DIR/logs/stdout" 2>&1

trap '' HUP

if pgrep -x "gaia" > /dev/null;
  then
    echo "An instance of Gaia is already running, this might lead to conflicts";
fi

echo "Starting Gaia";
if [ -z ${GAIA_VENV_PATH+x} ];
  then
    cd "$GAIA_DIR" || echo "$GAIA_DIR does not exist, exiting" exit;
    source $GAIA_DIR/python_venv/bin/activate;
  else
    source $GAIA_VENV_PATH;
fi
python3 -m gaia
