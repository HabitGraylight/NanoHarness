#!/bin/bash
# Commit staged changes with a message
# @param repo_path:string:Repository path (default: .)
# @param message:string:Commit message (required)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git commit -m "${message:?message is required}"
