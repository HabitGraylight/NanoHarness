#!/bin/bash
# Stash current changes
# @param repo_path:string:Repository path (default: .)
# @param message:string:Stash message (default: empty)

cd "${repo_path:-.}" 2>/dev/null || exit 1
if [ -n "${message}" ]; then
    git stash push -m "$message"
else
    git stash push
fi
