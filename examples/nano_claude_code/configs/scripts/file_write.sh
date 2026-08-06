#!/bin/bash
# Write content to a file (creates or overwrites)
# @param path:string:File path (required)
# @param content:string:Content to write (required)

path="${path:?path is required}"
content="${content:?content is required}"

# Create parent directories if needed
mkdir -p "$(dirname "$path")" 2>/dev/null

# Write content (handle multiline via printf)
printf '%s\n' "$content" > "$path"
echo "Written to $path ($(wc -c < "$path") bytes)"
