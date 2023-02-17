#!/bin/bash

if pgrep -x "gaia" > /dev/null; then
  pkill -15 "gaia"
else
  echo "No instance of Gaia currently running"
fi
