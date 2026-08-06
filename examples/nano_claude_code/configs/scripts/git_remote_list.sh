#!/bin/bash
# List configured remotes
# @param repo_path:string:Repository path (default: .)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git remote -v
