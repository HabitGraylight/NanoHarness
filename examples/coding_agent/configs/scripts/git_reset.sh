#!/bin/bash
# Reset current HEAD to a specific revision. DANGEROUS.
# @param repo_path:string:Repository path (default: .)
# @param revision:string:Target revision (required)
# @param hard:boolean:Discard working directory changes (default: false)

cd "${repo_path:-.}" 2>/dev/null || exit 1
if [ "${hard}" = "true" ]; then
    git reset --hard "${revision:?revision is required}"
else
    git reset "${revision:?revision is required}"
fi
