#!/bin/bash

# Script to remove lines containing IP addresses from a file
# Usage: ./remove_ip_urls.sh <filepath>

# Check if file path argument is provided
if [ $# -eq 0 ]; then
    echo "Error: No file path provided"
    echo "Usage: $0 <filepath>"
    exit 1
fi

INPUT="$1"

# Resolve to absolute path and verify it's within the project directory
FILEPATH="$(realpath -m "$INPUT" 2>/dev/null || echo "$INPUT")"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
case "$FILEPATH" in
    "$PROJECT_DIR"/*) ;;
    *)
        echo "Error: Path '$INPUT' resolves outside the project directory ('$PROJECT_DIR')"
        exit 1
        ;;
esac

# Check if file exists
if [ ! -f "$FILEPATH" ]; then
    echo "Error: File '$FILEPATH' not found"
    exit 1
fi

# Check if file is readable
if [ ! -r "$FILEPATH" ]; then
    echo "Error: File '$FILEPATH' is not readable"
    exit 1
fi

# Create temporary file
TEMP_FILE="${FILEPATH}.tmp"

# Remove lines containing IP addresses (pattern: http://[0-9]+.[0-9]+.[0-9]+.[0-9]+)
grep -vE 'http://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' "$FILEPATH" > "$TEMP_FILE"

# Check if grep was successful
if [ $? -eq 0 ]; then
    # Count lines before and after
    LINES_BEFORE=$(wc -l < "$FILEPATH")
    LINES_AFTER=$(wc -l < "$TEMP_FILE")
    LINES_REMOVED=$((LINES_BEFORE - LINES_AFTER))

    # Replace original file with filtered file
    mv "$TEMP_FILE" "$FILEPATH"

    echo "✓ Successfully processed '$FILEPATH'"
    echo "  Lines before: $LINES_BEFORE"
    echo "  Lines after:  $LINES_AFTER"
    echo "  Lines removed: $LINES_REMOVED"
else
    echo "Error: Failed to filter file"
    rm -f "$TEMP_FILE"
    exit 1
fi
