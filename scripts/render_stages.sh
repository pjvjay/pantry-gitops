#!/usr/bin/env bash
# Render every stage exactly the way Kargo's kustomize-build step will
# (minus image digests, which only exist at promotion time), plus the
# legacy tree while it still exists. Used by CI for schema validation;
# handy locally for reviewing what a base/ or stages/ change does.
#
#   scripts/render_stages.sh [out-dir]        (default: ./rendered)
set -euo pipefail
cd "$(dirname "$0")/.."
out="${1:-rendered}"
kustomize="${KUSTOMIZE:-kustomize}"
mkdir -p "$out"
for dir in stages/*/; do
  stage="$(basename "$dir")"
  for layer in infra apps; do
    "$kustomize" build "stages/$stage/$layer" > "$out/$stage-$layer.yaml"
    echo "rendered $out/$stage-$layer.yaml"
  done
done
# Legacy (removed at KARGO-25): still live on the dev cluster until cutover.
if [[ -f apps/kustomization.yaml ]]; then
  "$kustomize" build apps > "$out/legacy-apps.yaml"
  echo "rendered $out/legacy-apps.yaml"
fi
