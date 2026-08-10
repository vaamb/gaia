#!/bin/bash

# Read stdin and keep only its last N lines in FILE.
#
# Used by start.sh to bound "${GAIA_DIR}/logs/stdout", so stdout only has to
# keep enough context to see why Gaia died.
#
# Usage: ring_log.sh <file> [lines]

# Ignore SIGHUP so this outlives the terminal start.sh was launched from:
# were it to die first, Gaia would take a SIGPIPE on its next write.
trap '' HUP

FILE="${1:?usage: ring_log.sh <file> [lines]}"
LINES="${2:-20}"

if ! [[ "$LINES" =~ ^[1-9][0-9]*$ ]]; then
    echo "Number of lines must be a positive integer, got '${LINES}'." >&2
    exit 1
fi

mkdir -p "$(dirname "$FILE")" || exit 1

# Rem: a `while read` loop is used rather than awk as mawk block-buffers its
# input, which would leave the file empty until 4 kB have been logged.
declare -a buffer
count=0
while IFS= read -r line || [[ -n "$line" ]]; do
    buffer[count++ % LINES]="$line"
    # Truncate in place and rewrite the whole window: the file is at most
    # ${LINES} lines long, and rewriting it keeps the inode stable.
    : > "$FILE"
    for (( i = (count > LINES ? count - LINES : 0); i < count; i++ )); do
        printf '%s\n' "${buffer[i % LINES]}" >> "$FILE"
    done
done
