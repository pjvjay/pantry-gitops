#!/usr/bin/env bash
# One-time: create the machine-owned stage/<stage> branches (ADR 0002).
#
# Why it's needed: prod promotions open a PR INTO stage/prod, and a PR
# needs an existing target branch that shares history with the proposed
# change. Kargo can create dev/staging branches on first push, but doing
# all of them here keeps the setup uniform and lets the branch rulesets
# (scripts/github_rulesets.sh) exist before Kargo ever pushes.
#
# Idempotent: branches that already exist are left untouched. Run by a repo
# admin BEFORE applying the rulesets (they restrict who may create stage/*).
#
#   scripts/bootstrap_stage_branches.sh [remote-url]   (default: origin's URL)
set -euo pipefail
cd "$(dirname "$0")/.."
url="${1:-$(git remote get-url origin)}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

for dir in stages/*/; do
  stage="$(basename "$dir")"
  branch="stage/$stage"
  if git ls-remote --exit-code --heads "$url" "$branch" >/dev/null 2>&1; then
    echo "exists:  $branch"
    continue
  fi
  repo="$work/$stage"
  git init -q "$repo"
  git -C "$repo" checkout -q --orphan "$branch"
  cat > "$repo/README.md" <<MD
# $branch: machine-owned

Rendered Kubernetes manifests for the \`$stage\` stage, written ONLY by Kargo
(project \`pantry\`). Argo CD syncs \`infra/\` and \`apps/\` from here.

Do not edit. Change \`main:base/\` or \`main:stages/$stage/\` and promote.
The first promotion replaces this file.
MD
  git -C "$repo" add README.md
  git -C "$repo" -c user.name="pantry bootstrap" -c user.email="bootstrap@invalid" \
    commit -q -m "bootstrap $branch (machine-owned, rendered by Kargo)"
  git -C "$repo" push -q "$url" "HEAD:refs/heads/$branch"
  echo "created: $branch"
done
