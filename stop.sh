#!/bin/bash

if pgrep -x "gaia" > /dev/null; then
  pkill -15 "gaia"
fi
