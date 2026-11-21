# Kargo promotion epic: design package

An epic to move `pantry-gitops` from *"CI commits a tag to `main`, one environment"*
to *"Kargo promotes verified Freight through dev → staging → prod, with Argo CD syncing each stage"*.

| File | What it is |
|---|---|
| [`01-current-state-analysis.md`](01-current-state-analysis.md) | Review of all 25 files: what to keep, gaps against Kargo's needs, risks R1–R10 with file/line evidence, cross-repo impact, open questions |
| [`02-target-architecture.md`](02-target-architecture.md) | Target flow, ADR-001…006, repo layout, illustrative Kargo/Argo CD manifests, security model, rollback |
| [`03-epic-and-stories.md`](03-epic-and-stories.md) | **Generated.** The epic, 30 stories with acceptance criteria, sprint plan, dependency graph, risk register |
| [`jira-import.csv`](jira-import.csv) | **Generated.** Jira Cloud CSV import: 1 Epic + 30 children |
| [`epic_backlog.py`](epic_backlog.py) | Single source for the backlog. Edit here, then run `python3 docs/kargo-epic/epic_backlog.py` |

## Executive summary

**The headline finding:** Kargo itself is the easy part. Most of the effort (and the
biggest risk) is making this single-environment repo support multiple stages *without
losing the live database on the way*.

1. **Multi-stage restructuring is a prerequisite.** Namespaces, `DB_HOST`, ingress host and
   image tags are all hard-coded for one environment.
2. **Cutover can cascade-delete Postgres.** The child Applications carry
   `resources-finalizer` and the root app prunes, so moving their files in git deletes the
   CNPG cluster. Cutover is its own 8-pt story with a rehearsed runbook (KARGO-23).
3. **Keep infra and apps as separate Applications per stage.** A PreSync migration hook runs
   before all sync waves, so merging them would deadlock a cold stage. Kargo's
   `argocd-update` (infra first, then apps) also gives an ordering that today comes only from retry.
4. **Render to `stage/<stage>` branches, and include the gitops commit in Freight.**
   Otherwise manifest/config changes skip the pipeline and reach every stage at once.
5. **Kargo writes git and never edits Application specs.** The root app and ApplicationSet
   self-heal and would otherwise fight Kargo.
6. **Security improves.** Three CI jobs holding write access to the deployment repo are
   replaced by one narrowly-scoped GitHub App, plus a PR review gate on `stage/prod`.

**Size:** 30 issues, 106 pts (MVP 91 pts), about 6 sprints for 2 engineers. Critical path:
PoC → layout → per-stage infra → ApplicationSet → PromotionTask → Stages → verification → cutover → app-repo CI.

## Implementation status (M0–M6)

Everything that can live in this repo is implemented. Per-story status is in
[`03-epic-and-stories.md`](03-epic-and-stories.md).
- **Decisions:** [`docs/adr/`](../adr/) 0001–0008
- **Manifests:** `base/`, `stages/{dev,staging,prod}/` (prod: HA + backups), `hub/argocd/`, `hub/kargo/`
  (pipeline + verification), `hub/platform/` (Argo CD health checks)
- **Safety net:** `scripts/check_gitops.py` (11 checks, each with mutation tests) and
  `.github/workflows/validate.yaml` (strict kubeconform, including Kargo CRDs from the pinned chart)
- **Runbooks:** [`cutover-dev`](../runbooks/cutover-dev.md) (+ `scripts/cutover.py`),
  the [delivery handbook](../runbooks/README.md) (onboarding, on-call, promotion, rollback,
  troubleshooting, routine tasks, reference), [`break-glass-and-dr`](../runbooks/break-glass-and-dr.md)
- **Other repos:** [`pantry-infra`](../handoffs/pantry-infra.md), [`app repos`](../handoffs/app-repos.md),
  [`pantry-db`](../handoffs/pantry-db.md)
- **Still open, and needs people or clusters:**
  - the live PoC ([18 rows](poc-kargo-argocd.md))
  - the pantry-infra platform work
  - executing the dev cutover, then KARGO-24/25
  - enabling provenance once images are signed
  - the first game day

## Before kickoff (answered; see 01 §5)

Decisions needed from the product owner (details in 01 §5):
- Is the existing `pantry-db` data disposable?
- Where does prod run?
- Which IdP and approver group?
- Per-stage Anthropic keys?

Owners are also needed for the cross-repo stories in `pantry-infra` and the three app repos.

## Importing into Jira

1. *Settings → System → External system import → CSV*, and upload `jira-import.csv`.
2. Map the fields:
   - `Issue Id` → Issue Id
   - `Parent` → Parent (the epic is row `1`)
   - each `Labels` column → Labels
   - `Story Points` → Story Points
   - `Depends On` → *Link: "is blocked by"* (or leave unmapped; dependencies are also listed in each description)
3. Spikes import as **Task** with label `spike`, because Jira has no native Spike type.
   Milestones come in as labels `m0`…`m6`. Turn them into Fix Versions or Sprints if you prefer.
4. Summaries are prefixed `[KARGO-NN]` so the design docs' cross-references still resolve after Jira assigns real keys.

> The reference manifests in 02 are **illustrative**. Kargo's API is `v1alpha1` and changes between
> minor versions. KARGO-05 pins the version and corrects them, and KARGO-13 validates them in CI.
