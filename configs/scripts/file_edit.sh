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

if command -v python3 >/dev/null 2>&1; then
    python_command=python3
elif command -v python >/dev/null 2>&1; then
    python_command=python
else
    echo "Error: file_edit requires Python" >&2
    exit 1
fi

# Work on bytes so matching is literal and unrelated line endings are preserved.
# Arguments, rather than generated source code or regexes, carry user text.
"$python_command" - "$path" "$old_text" "$new_text" "${replace_all:-false}" <<'PY'
import os
import sys
from pathlib import Path


path = Path(sys.argv[1])
old_text = os.fsencode(sys.argv[2])
new_text = os.fsencode(sys.argv[3])
replace_all = sys.argv[4] == "true"

data = path.read_bytes()
matches = data.count(old_text)
if matches == 0:
    print(f"Error: old_text not found in {path}", file=sys.stderr)
    raise SystemExit(1)

updated = data.replace(old_text, new_text) if replace_all else data.replace(
    old_text,
    new_text,
    1,
)
with path.open("r+b") as handle:
    handle.write(updated)
    handle.truncate()
    handle.flush()
    os.fsync(handle.fileno())

replaced = matches if replace_all else 1
suffix = "occurrence" if replaced == 1 else "occurrences"
print(f"Replaced {replaced} {suffix} in {path}")
PY
