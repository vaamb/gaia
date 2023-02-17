#!/bin/bash

echo "Updating Gaia"

# Go to Gaia dir
cd "$GAIA_DIR" || { echo "Cannot go to \`GAIA_DIR\` did you install Gaia using the \`install.sh\` script?"; exit; }

source python_venv/bin/activate

cd "/lib/gaia"

LOCAL_HASH=$"git rev-parse stable"
ORIGIN_HASH=$"git rev-parse origin/stable"

if [ $LOCAL_HASH != $ORIGIN_HASH ]; then
  git pull --recurse-submodules
  pip install -e .
fi

deactivate

echo "Ouranos updated. To run it, either use \`ouranos start\` or go to the ouranos directory, activate the virtual environment and run \`python main.py\` or \`python -m ouranos\`"

exit
