#!/bin/bash
# Find files by name pattern
# @param path:string:Search directory (default: .)
# @param pattern:string:Glob pattern to match (default: *)

path="${path:-.}"
pattern="${pattern:-*}"

find "$path" -name "$pattern" -type f 2>/dev/null | head -50
