#!/bin/bash
# Create a new branch
# @param repo_path:string:Repository path (default: .)
# @param name:string:Branch name (required)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git branch "${name:?name is required}"
