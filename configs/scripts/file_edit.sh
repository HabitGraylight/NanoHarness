#!/bin/bash
# Edit a file by replacing a text fragment
# @param path:string:File path (required)
# @param old_text:string:Text to find (required)
# @param new_text:string:Replacement text (required)
# @param replace_all:boolean:Replace all occurrences, not just the first (default: false)

path="${path:?path is required}"
old_text="${old_text:?old_text is required}"
new_text="${new_text:?new_text is required}"

if [ ! -f "$path" ]; then
    echo "Error: File not found: $path" >&2
    exit 1
fi

# Count matches before
matches=$(grep -c -F "$old_text" "$path" || true)
if [ "$matches" -eq 0 ]; then
    echo "Error: old_text not found in $path" >&2
    exit 1
fi

if [ "${replace_all}" = "true" ]; then
    sed -i "s|$(printf '%s' "$old_text" | sed 's/[&/\]/\\&/g')|$(printf '%s' "$new_text" | sed 's/[&/\]/\\&/g')|g" "$path"
    echo "Replaced $matches occurrence(s) in $path"
else
    # Replace only the first occurrence
    escaped_old=$(printf '%s' "$old_text" | sed 's/[&/\]/\\&/g')
    escaped_new=$(printf '%s' "$new_text" | sed 's/[&/\]/\\&/g')
    sed -i "0,|${escaped_old}|s|${escaped_old}|${escaped_new}|" "$path" 2>/dev/null || \
    sed -i "0,/${escaped_old}/s//${escaped_new}/" "$path"
    echo "Replaced 1 occurrence in $path"
fi
