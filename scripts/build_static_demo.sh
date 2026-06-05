#!/usr/bin/env bash
# Build the static GitHub Pages demo into dist/ (no backend, no secrets).
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf dist && mkdir -p dist
cp web/index.html web/hosting.js dist/
[ -d web/demo ] && cp -r web/demo dist/demo || mkdir -p dist/demo

cat > dist/config.js <<'EOF'
// GitHub Pages static demo build — no backend, no secrets.
window.CT_CONFIG = { DEMO_MODE: true, USE_MOCK_DATA: true, API_BASE_URL: "", PUBLIC_BASE_PATH: "" };
EOF

# placeholder demo data if none was exported
[ -f dist/demo/creator_demo.json ] || echo '{"creators":[],"detail":{},"canned":[{"match":"","reply":{"answer":"Demo data not exported yet. Run: python scripts/export_demo_data.py","products":[],"sources":[]}}]}' > dist/demo/creator_demo.json

echo "Static demo built in dist/ — open dist/index.html or deploy via GitHub Pages."
