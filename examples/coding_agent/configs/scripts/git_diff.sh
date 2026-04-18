#!/bin/bash
# Show unstaged or staged changes
# @param repo_path:string:Repository path (default: .)
# @param cached:boolean:Show staged changes instead of unstaged (default: false)

cd "${repo_path:-.}" 2>/dev/null || exit 1
if [ "${cached}" = "true" ]; then
    git diff --cached
else
    git diff
fi
