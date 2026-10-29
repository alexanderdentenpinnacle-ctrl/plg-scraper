#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu VPS with Docker installed. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || cp .env.example .env
set_var() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
get_var() { grep "^$1=" .env | cut -d= -f2- || true; }

if [ -z "$(get_var DOMAIN)" ]; then
  read -rp "Your domain (e.g. pinnaclelifegroup.com): " d
  set_var DOMAIN "$d"
fi
for k in PG_DATABASE_PASSWORD SYNAPSE_DB_PASSWORD SYNAPSE_REGISTRATION_SECRET SYNAPSE_MACAROON_SECRET SYNAPSE_FORM_SECRET HOOK_SECRET; do
  [ -n "$(get_var $k)" ] || set_var "$k" "$(openssl rand -hex 32)"
done
[ -n "$(get_var ENCRYPTION_KEY)" ] || set_var ENCRYPTION_KEY "$(openssl rand -base64 32)"

DOMAIN=$(get_var DOMAIN)
render() {
  sed -e "s|__DOMAIN__|$DOMAIN|g" \
      -e "s|__SYNAPSE_DB_PASSWORD__|$(get_var SYNAPSE_DB_PASSWORD)|g" \
      -e "s|__SYNAPSE_REGISTRATION_SECRET__|$(get_var SYNAPSE_REGISTRATION_SECRET)|g" \
      -e "s|__SYNAPSE_MACAROON_SECRET__|$(get_var SYNAPSE_MACAROON_SECRET)|g" \
      -e "s|__SYNAPSE_FORM_SECRET__|$(get_var SYNAPSE_FORM_SECRET)|g" "$1"
}
render element/config.json.tmpl > element/config.json

# Let Synapse create its signing key + log config, then apply our config on top.
mkdir -p synapse/data
if [ ! -f "synapse/data/matrix.$DOMAIN.signing.key" ]; then
  docker compose run --rm -e SYNAPSE_SERVER_NAME="matrix.$DOMAIN" -e SYNAPSE_REPORT_STATS=no synapse generate
fi
render synapse/homeserver.yaml.tmpl > synapse/data/homeserver.yaml
chown -R 991:991 synapse/data

docker compose up -d
echo
echo "Up. Next: README step 3 (create your chat admin account)."
echo "Twenty webhook URL: https://hooks.$DOMAIN/twenty/$(get_var HOOK_SECRET)"
