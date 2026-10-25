#!/usr/bin/env bash
# Static deploy of output/map.html to Cloudflare Pages. Prepared, NOT run automatically.
#   CONFIRM=yes CLOUDFLARE_API_TOKEN=... CLOUDFLARE_ACCOUNT_ID=... ./scripts/deploy_map.sh
# The map's popups include owner names/mailing addresses unless config.yaml has
# map.include_owner_in_popup: false. A Pages URL is public, so the script refuses to ship
# owner data unless ALLOW_OWNER_DATA=1 (e.g. when the project sits behind Cloudflare Access).
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="${CF_PAGES_PROJECT:-pinnacle-storm-map}"
[[ -f output/map.html ]] || { echo "output/map.html not found - run python run.py first"; exit 1; }
if grep -q "<th style='text-align:left;padding-right:6px'>Owner</th>" output/map.html && [[ "${ALLOW_OWNER_DATA:-0}" != "1" ]]; then
  echo "map.html contains owner names. Set map.include_owner_in_popup: false and rerun, or ALLOW_OWNER_DATA=1."; exit 1
fi
rm -rf deploy/site && mkdir -p deploy/site
cp output/map.html deploy/site/index.html
printf '/*\n  X-Robots-Tag: noindex\n' > deploy/site/_headers
echo "Prepared deploy/site/ for Cloudflare Pages project '$PROJECT'."
if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "Dry run. Re-run with CONFIRM=yes to deploy."; exit 0
fi
npx --yes wrangler@3 pages deploy deploy/site --project-name "$PROJECT" --branch main --commit-dirty=true
