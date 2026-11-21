#!/usr/bin/env bash
# Branch rulesets for pantry-gitops (KARGO-14, ADR 0002 + 0007).
#
#   main               PR + 1 approval + `validate` CI; no force-push/delete
#   stage/dev|staging  only the Kargo GitHub App may create/update; no
#                      force-push/delete (machine-owned rendered output)
#   stage/prod         changes only via PR with 1 approval (review rule,
#                      no bypass) AND only the prod-approvers team may
#                      merge (update rule, bypass = that team via PR only)
#
# Repo admins can bypass every ruleset: the documented break-glass path
# (KARGO-29). Teams need an organisation repo; on a personal account drop
# the prod-merge ruleset and rely on the Kargo prod-approver role (ADR 0007).
#
# Requires gh (authenticated as a repo admin) unless --dry-run.
#   KARGO_APP_ID=<github app id>  PROD_APPROVERS_TEAM_ID=<team id> \
#     scripts/github_rulesets.sh [--dry-run] [owner/repo]
# Run AFTER scripts/bootstrap_stage_branches.sh.
set -euo pipefail
dry=false
if [[ "${1:-}" == "--dry-run" ]]; then dry=true; shift; fi
repo="${1:-pjvjay/pantry-gitops}"
: "${KARGO_APP_ID:?set KARGO_APP_ID (GitHub App id of kargo-pantry)}"
: "${PROD_APPROVERS_TEAM_ID:?set PROD_APPROVERS_TEAM_ID (GitHub team id of pantry-prod-approvers)}"
[[ "$KARGO_APP_ID" =~ ^[0-9]+$ && "$PROD_APPROVERS_TEAM_ID" =~ ^[0-9]+$ ]] || { echo "ids must be numeric" >&2; exit 2; }

ADMIN='{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}'   # 5 = repo admin role
PROTECT='{"type": "deletion"}, {"type": "non_fast_forward"}'

rulesets=(
"$(cat <<JSON
{
  "name": "main: reviewed and validated",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [$ADMIN],
  "rules": [
    $PROTECT,
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews_on_push": true,
      "require_last_push_approval": true,
      "require_code_owner_review": false,
      "required_review_thread_resolution": true}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": true,
      "required_status_checks": [{"context": "validate"}]}}
  ]
}
JSON
)"
"$(cat <<JSON
{
  "name": "stage/dev+staging: Kargo only",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/stage/dev", "refs/heads/stage/staging"], "exclude": []}},
  "bypass_actors": [$ADMIN, {"actor_id": $KARGO_APP_ID, "actor_type": "Integration", "bypass_mode": "always"}],
  "rules": [$PROTECT, {"type": "creation"}, {"type": "update"}]
}
JSON
)"
"$(cat <<JSON
{
  "name": "stage/prod: reviewed PR",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/stage/prod"], "exclude": []}},
  "bypass_actors": [$ADMIN],
  "rules": [
    $PROTECT,
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 1,
      "dismiss_stale_reviews_on_push": true,
      "require_last_push_approval": true,
      "require_code_owner_review": false,
      "required_review_thread_resolution": false}}
  ]
}
JSON
)"
"$(cat <<JSON
{
  "name": "stage/prod: only prod-approvers merge",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/stage/prod"], "exclude": []}},
  "bypass_actors": [$ADMIN, {"actor_id": $PROD_APPROVERS_TEAM_ID, "actor_type": "Team", "bypass_mode": "pull_request"}],
  "rules": [{"type": "update"}]
}
JSON
)"
)

for rs in "${rulesets[@]}"; do
  name="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])' <<<"$rs")"
  if $dry; then
    echo "$rs"
  else
    gh api --method POST "repos/$repo/rulesets" --input - <<<"$rs" >/dev/null
    echo "applied: $name"
  fi
done
