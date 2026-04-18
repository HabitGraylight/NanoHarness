#!/bin/bash
# List files and directories
# @param path:string:Directory path (default: .)
# @param all:boolean:Show hidden files (default: false)
# @param long:boolean:Use long listing format (default: false)

path="${path:-.}"

if [ ! -d "$path" ]; then
    echo "Error: Directory not found: $path" >&2
    exit 1
fi

args=("-1" "$path")
if [ "${all}" = "true" ]; then
    args=("-a" "${args[@]}")
fi
if [ "${long}" = "true" ]; then
    args=("-l" "$path")
    [ "${all}" = "true" ] && args=("-la" "$path")
fi

ls "${args[@]}"
