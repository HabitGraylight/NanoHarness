#!/bin/bash
# Push commits to a remote repository
# @param repo_path:string:Repository path (default: .)
# @param remote:string:Remote name (default: origin)
# @param branch:string:Branch name (default: current)

cd "${repo_path:-.}" 2>/dev/null || exit 1
if [ -n "${branch}" ]; then
    git push "${remote:-origin}" "$branch"
else
    git push "${remote:-origin}"
fi
