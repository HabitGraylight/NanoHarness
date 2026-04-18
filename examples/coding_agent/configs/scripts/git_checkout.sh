#!/bin/bash
# Switch to a branch, optionally creating it
# @param repo_path:string:Repository path (default: .)
# @param branch:string:Branch name (required)
# @param create:boolean:Create the branch if it doesn't exist (default: false)

cd "${repo_path:-.}" 2>/dev/null || exit 1
if [ "${create}" = "true" ]; then
    git checkout -b "${branch:?branch is required}"
else
    git checkout "${branch:?branch is required}"
fi
