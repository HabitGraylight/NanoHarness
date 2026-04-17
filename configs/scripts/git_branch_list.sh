#!/bin/bash
# List local branches (or all branches including remotes)
# @param repo_path:string:Repository path (default: .)
# @param all_branches:boolean:Show remote branches too (default: false)

cd "${repo_path:-.}" 2>/dev/null || exit 1
if [ "${all_branches}" = "true" ]; then
    git branch -a
else
    git branch
fi
