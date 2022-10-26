#!/bin/bash

echo "Starting Gaia"

DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cd $DIR

if pgrep -x "gaia" > /dev/null; then
  pkill -15 "gaia"
fi

source venv/bin/activate

python3 main.py &
