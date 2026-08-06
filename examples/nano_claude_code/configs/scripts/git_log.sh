#!/bin/bash
# Show commit history
# @param repo_path:string:Repository path (default: .)
# @param count:integer:Number of commits to show (default: 20)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git log --oneline -"${count:-20}"
