#!/usr/bin/env bash
# Build + export image for Podman on infra3 (RHEL).
# Usage (from repo root):
#   ./web/deploy-infra3.sh [user@infra3]

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${1:-}"
IMAGE="ecoflow-ocean-web:latest"
ARCHIVE="/tmp/ecoflow-ocean-web.tar.gz"

cd "$ROOT"
podman build --format docker -f web/Containerfile -t "$IMAGE" .
podman save "$IMAGE" | gzip > "$ARCHIVE"
echo "Wrote $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

if [[ -n "$HOST" ]]; then
  scp "$ARCHIVE" "web/.env.example" "$HOST:/tmp/"
  ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
podman load -i /tmp/ecoflow-ocean-web.tar.gz
podman volume create ecoflow-ocean-data 2>/dev/null || true
mkdir -p "$HOME/ecoflow-ocean-web"
if [[ ! -f "$HOME/ecoflow-ocean-web/.env" ]]; then
  cp /tmp/.env.example "$HOME/ecoflow-ocean-web/.env"
  echo "Edit $HOME/ecoflow-ocean-web/.env then re-run the podman run command below."
fi
echo
echo "Ensure \$HOME/ecoflow-ocean-web/.env is UTF-8 without a BOM (a BOM makes Podman miss SITES=)."
echo "Run on this host:"
echo "  podman rm -f ecoflow-ocean-web 2>/dev/null || true"
echo "  podman run -d --name ecoflow-ocean-web --restart=always --no-healthcheck \\"
echo "    -p 8080:8080 -v ecoflow-ocean-data:/data:Z \\"
echo "    --env-file \$HOME/ecoflow-ocean-web/.env ecoflow-ocean-web:latest"
REMOTE
fi
