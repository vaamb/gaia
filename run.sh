#!/bin/bash

echo "Starting Gaia"

if [ $(ps | grep "gaia") -eq 0 ]; then
  pkill -15 gaia
fi

source venv/bin/activate

python3 main.py &
