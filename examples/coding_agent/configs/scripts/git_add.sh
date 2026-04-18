#!/bin/bash
# Stage files for commit
# @param repo_path:string:Repository path (default: .)
# @param files:string:Files to stage, use '.' for all (required)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git add "${files:?files is required}"
