# KARGO-05: Kargo ↔ Argo CD proof of concept

**Status:** the offline half is done. The live half needs a cluster and is **not yet executed** (the
implementation environment had no container runtime). Close KARGO-05 when every row in §2 is ✅.

## 1. Already verified offline (repeatable: `scripts/` + CI)

| Property | How it was verified |
|---|---|
| Every stage renders with kustomize 5.x | `scripts/render_stages.sh` (6 render roots) |
| Built-in kinds are schema-valid, **strict** (no unknown fields) | `kubernetes-validate --strict`, k8s 1.31: 36/36 |
| Argo CD, ESO and CNPG resources are schema-valid; every field name exists upstream | `@kubernetes-models/*` validators + field-name check: 23/23 |
| Rendered dev == live legacy, except image pins and host | `check_gitops.py::check_legacy_equivalence` (CNPG `Cluster` spec identical) |
| Stage isolation, data protection, RBAC, cutover invariants, task wiring | `check_gitops.py`: 9 checks, each with a mutation test proving it fails when it should (23 tests) |
| The promotion mechanics (clone at commit → clear → set digests → build → commit → push) produce only rendered, digest-pinned YAML on the stage branch and never touch `main` | Replayed the `render-and-push` steps with git + kustomize against a local bare repo |
| Stage-branch bootstrap is idempotent | `bootstrap_stage_branches.sh` run twice against a bare repo |
| Rulesets are valid JSON with the intended rules | `github_rulesets.sh --dry-run` |
| **M4** smoke + API-contract scripts pass a healthy stage, fail a broken one, and reject an untrusted TLS cert | Ran the exact scripts from `analysis-templates.yaml` against local HTTPS servers (exit 0 / 22 / 1 / 60) |
| **M4/M6** Verification, soak, provenance-no-bypass, prod HA/backup rules | `check_gitops.py` rules + mutation tests |
| **M5** Every repo state of the cutover passes all checks, including after the legacy trees are deleted | `scripts/test_cutover.py` |
| **M6** The Argo CD health check for CNPG `Backup` maps every phase correctly; `walArchivingFailing` can't hang a promotion | `scripts/test_platform_lua.py` (runs the Lua via lupa) |
| Prod renders 3 PG instances, backups, a pre-promotion hook at wave -4, 2 replicas + PDBs; dev is unaffected | render + `check_prod_hardening` + `check_legacy_equivalence` |

**Not checkable offline:** Kargo CRD schemas were unreachable. CI validates `hub/kargo/` strictly against
the pinned chart's CRDs on its first run. Kargo treats each step's `config:` as free-form, so step configs
are only validated when a promotion runs. That's what §2 is for.

## 2. To verify on a live cluster

Setup (kind is enough; ~30 min):
```bash
kind create cluster --name hub && kind create cluster --name spoke
# hub: Argo CD, cert-manager, Argo Rollouts CRDs, Kargo (KARGO_VERSION from .github/workflows/validate.yaml)
# register spoke as cluster "dev", rename in-cluster to "prod" (docs/handoffs/pantry-infra.md §3)
# fork pantry-gitops; point repoURL at the fork (sed over hub/); GitHub App or PAT creds in ns `pantry`
# scripts/bootstrap_stage_branches.sh; kubectl apply -f hub/bootstrap/root-app.yaml
```

| # | Assumption in this repo | Test | Expected | ✅ |
|---|---|---|---|---|
| 1 | Field names in `hub/kargo/*` match the pinned version (`ProjectConfig.promotionPolicies[].stageSelector`, `allowTags`, `task.outputs.*`, `ctx.targetFreight.name`, `git-wait-for-pr` output `commit`, `git-open-pr` output `pr.id`). *Found in review against 1.6.0: image subscriptions need an explicit `strictSemvers` (fixed, and now enforced by `check_gitops.py`)* | CI kubeconform + one promotion per stage | CI green, promotions succeed. **If a name differs, fix it here and in `check_gitops.py`** | ☐ |
| 2 | `kargo.akuity.io/authorized-stage` is enforced | Remove the annotation from one Application, then promote | Kargo refuses with an authorization error | ☐ |
| 3 | Auto-sync + selfHeal don't fight `argocd-update`; the ApplicationSet doesn't clobber Kargo's sync operation | Promote staging 3× in a row | Each promotion succeeds, and the Applications report the promoted revision | ☐ |
| 4 | A failed PreSync migration fails the promotion | Promote Freight whose migrate image exits 1 | Sync fails, Promotion `Errored`, Freight not verified in that stage | ☐ |
| 5 | Infra-then-apps ordering converges on a **cold** stage without relying on retry | Delete staging namespaces (test cluster only), then promote | infra Healthy → apps sync → migration succeeds first try | ☐ |
| 6 | The prod PR flow works against a bootstrapped `stage/prod` | Promote to prod | PR opened; promotion waits; merge → prod syncs to the merge commit. Close instead → promotion fails, prod unchanged | ☐ |
| 7 | `rbac.kargo.akuity.io/claim.roles` maps Entra app roles | Log in as a `Pantry.Promoter`-only user | Can promote dev/staging, **cannot** promote prod | ☐ |
| 8 | Kargo's `NewestBuild` works on GHCR without credentials | Watch Warehouse discovery | Freight created with 3 digests and 1 commit, no auth errors | ☐ |
| 9 | Spoke stopped → no effect on prod | `docker stop spoke-control-plane`, promote prod | prod unaffected; dev/staging promotions fail fast and can be re-run after start | ☐ |
| 10 | `Prune=false,Delete=false` really protects data | Delete `pantry-infra-staging` (cascade) on the test cluster | Namespaces + CNPG `Cluster` survive | ☐ |
| 11 | `requestedFreight[].sources.requiredSoakTime` is honoured | Verify Freight in staging, then try to promote to prod right away | Prod refuses until 1 h has passed | ☐ |
| 12 | Verification Jobs run on the hub (ns `pantry`) and reach the public stage hosts | Promote staging | `pantry-smoke` + `pantry-api-contract` AnalysisRuns Successful; a broken build → Freight unverified | ☐ |
| 13 | The API contract assumptions hold (`/openapi.json` lists `/health`, `/recipes`, `/plan…`; `/recipes` → 200 JSON) | Run against the real pantry-api | Pass. **If not, fix the template before anything depends on it, or it blocks prod** | ☐ |
| 14 | The prod Backup Sync hook gates: with the health Lua, infra sync waits for `completed` and fails on `failed` | Point `destinationPath` at a wrong container, then promote prod | Backup `failed` → infra sync fails → apps (migration) never start | ☐ |
| 15 | Expressions work in Stage verification args (needed only to enable `pantry-provenance`) | Enable provenance on a test stage | The AnalysisRun receives resolved `repo@sha256:…` values | ☐ |
| 16 | Who may *Reverify* | Try as `Pantry.Promoter` | Document the result in the operations runbook | ☐ |
| 17 | Argo CD Notifications subscriptions fire | Promote staging; break a sync | Teams messages for deployed / sync-failed | ☐ |
| 18 | The cutover runbook end-to-end (rehearsal) | Run `docs/runbooks/cutover-dev.md` on the kind pair with a seeded DB | Row counts identical, Postgres not restarted, dev verified | ☐ |
| 19 | On-call operations work with `Pantry.Oncall` (`patch` only), and the ⚑ CLI lines in `docs/runbooks/reference.md` are right for 1.6 | As a user with only Viewer + Oncall: refresh the Warehouse, re-verify staging, abort a Promotion, edit a Freight alias. Run each ⚑ command with `--help` | All four work; none of them can promote. Fix the Role or the handbook where not (replaces row 16) | ☐ |
