#!/bin/bash
# Read and return the content of a file
# @param path:string:File path (required)
# @param start_line:integer:Start line number, 1-based (default: 1)
# @param end_line:integer:End line number, inclusive (default: 0 = all)

path="${path:?path is required}"
if [ ! -f "$path" ]; then
    echo "Error: File not found: $path" >&2
    exit 1
fi

start="${start_line:-1}"
end="${end_line:-0}"

if [ "$end" -eq 0 ]; then
    sed -n "${start},\$p" "$path"
else
    sed -n "${start},${end}p" "$path"
fi
