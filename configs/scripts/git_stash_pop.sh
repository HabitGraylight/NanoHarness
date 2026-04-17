#!/bin/bash
# Apply and remove a stash entry
# @param repo_path:string:Repository path (default: .)
# @param index:integer:Stash index (default: 0)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git stash pop "stash@{${index:-0}}"
