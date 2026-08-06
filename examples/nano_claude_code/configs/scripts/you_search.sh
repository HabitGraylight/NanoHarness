#!/bin/bash
# Search the web with you.com Search API and return compact JSON results
# @param query:string:Search query text
# @param count:integer:Number of web results to return (default: 5)
# @param country:string:Country code like US, GB, DE (default: US)
# @param language:string:Language code like EN, ES, FR (default: EN)

set -euo pipefail

query="${query:-}"
count="${count:-5}"
country="${country:-US}"
language="${language:-EN}"

if [ -z "$query" ]; then
  echo "query is required" >&2
  exit 1
fi

tmp_body="$(mktemp)"
trap 'rm -f "$tmp_body"' EXIT

curl_args=(
  -sS
  -G
  -o "$tmp_body"
  -w "%{http_code}"
  --data-urlencode "query=$query"
  --data-urlencode "count=$count"
  --data-urlencode "country=$country"
  --data-urlencode "language=$language"
)

if [ -n "${YDC_API_KEY:-}" ]; then
  curl_args+=( -H "X-API-Key: ${YDC_API_KEY}" )
fi

http_code="$(curl "${curl_args[@]}" "https://api.you.com/v1/agents/search")"

if [ "$http_code" -lt 200 ] || [ "$http_code" -ge 300 ]; then
  body_preview="$(head -c 1200 "$tmp_body" || true)"
  echo "you.com search failed (${http_code}): ${body_preview}" >&2
  exit 1
fi

python3 - "$tmp_body" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

web = (data.get("results") or {}).get("web") or []
items = []
for r in web:
    snippets = r.get("snippets") or []
    snippet = snippets[0] if snippets else (r.get("description") or "")
    items.append(
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": snippet,
        }
    )

out = {
    "query": ((data.get("metadata") or {}).get("query") or ""),
    "search_uuid": ((data.get("metadata") or {}).get("search_uuid") or ""),
    "results": items,
}
print(json.dumps(out, ensure_ascii=False, indent=2))
PY

