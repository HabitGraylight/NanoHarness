#!/bin/bash
# Merge a branch into the current branch
# @param repo_path:string:Repository path (default: .)
# @param branch:string:Branch to merge (required)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git merge "${branch:?branch is required}"
