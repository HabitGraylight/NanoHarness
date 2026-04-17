#!/bin/bash
# Revert a specific commit
# @param repo_path:string:Repository path (default: .)
# @param revision:string:Commit to revert (required)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git revert --no-edit "${revision:?revision is required}"
