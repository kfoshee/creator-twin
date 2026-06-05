#!/usr/bin/env bash
# Build the static frontend into dist/.
# If API_BASE_URL is set (GitHub repo variable), build LIVE mode against the real backend.
# Otherwise build the keyless demo. Secrets are never included either way.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf dist && mkdir -p dist
cp web/index.html web/hosting.js dist/
[ -d web/demo ] && cp -r web/demo dist/demo || mkdir -p dist/demo

if [ -n "${API_BASE_URL:-}" ]; then
  cat > dist/config.js <<EOF
// Production build — live backend, no demo data.
window.CT_CONFIG = { DEMO_MODE: false, USE_MOCK_DATA: false, API_BASE_URL: "${API_BASE_URL}", PUBLIC_BASE_PATH: "" };
EOF
  echo "Built LIVE frontend against ${API_BASE_URL}"
else
  cat > dist/config.js <<'EOF'
// Static demo build — no backend, no secrets.
window.CT_CONFIG = { DEMO_MODE: true, USE_MOCK_DATA: true, API_BASE_URL: "", PUBLIC_BASE_PATH: "" };
EOF
  echo "Built DEMO frontend (set the API_BASE_URL repo variable for live mode)"
fi

[ -f dist/demo/creator_demo.json ] || echo '{"creators":[],"detail":{},"canned":[{"match":"","reply":{"answer":"Demo data not exported yet.","products":[],"sources":[]}}]}' > dist/demo/creator_demo.json
