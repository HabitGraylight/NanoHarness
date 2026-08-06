#!/bin/bash
# Show the working tree status
# @param repo_path:string:Repository path (default: .)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git status
