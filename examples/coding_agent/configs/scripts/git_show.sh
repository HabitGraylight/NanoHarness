#!/bin/bash
# Show details of a specific commit
# @param repo_path:string:Repository path (default: .)
# @param revision:string:Commit hash or reference (required)

cd "${repo_path:-.}" 2>/dev/null || exit 1
git show "${revision:?revision is required}"
