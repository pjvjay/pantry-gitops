#!/usr/bin/env python3
"""Single source of truth for the Kargo epic backlog.

Generates:
  03-epic-and-stories.md   human-readable epic + stories
  jira-import.csv          Jira Cloud CSV import (Epic + children, parent links)

    python3 docs/kargo-epic/epic_backlog.py

Edit the data below, never the generated files.
Stdlib only (matching scripts/bump_image_tag.py).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

EPIC = {
    "key": "KARGO-EPIC",
    "summary": "Progressive delivery for pantry-platform: Kargo-orchestrated promotion on Argo CD",
    "goal": (
        "Replace the single-environment 'CI commits a tag to main' deployment loop with "
        "Kargo-orchestrated promotion of verified, immutable release candidates (Freight) "
        "through dev -> staging -> prod, with Argo CD remaining the sync engine for every stage."
    ),
    "value": [
        "Changes (images AND manifest/config changes) reach prod only after passing dev and staging verification.",
        "Rollback becomes 'promote the previous Freight' instead of hand-reverting git commits on a shared branch.",
        "App-repo CI no longer holds write credentials to the deployment repo (removes 3 standing deploy credentials).",
        "Every stage's exact running state is auditable: Freight history in Kargo + rendered YAML in `stage/<stage>` branches.",
    ],
    "in_scope": [
        "Multi-stage layout of this repo (base + stage overlays, per-stage infra, ApplicationSet).",
        "Kargo + Argo Rollouts CRDs added to the platform contract (installed by pantry-infra).",
        "Kargo Project, Warehouse, PromotionTask, Stages, verification, promotion policies, RBAC/SSO.",
        "Zero-data-loss cutover of the existing environment; removal of CI bump steps in app repos.",
    ],
    "out_of_scope": [
        "Separate prod AKS cluster (design keeps it a config change; separate epic).",
        "Canary/blue-green rollouts of workloads via Argo Rollouts (only its AnalysisTemplate API is used).",
        "Observability stack (Prometheus/Grafana) - metric-based verification is a follow-up.",
    ],
    "success": [
        "A commit to pantry-api reaches dev with no human action and prod with exactly one approval, with no direct git writes by CI.",
        "Lead time dev -> prod measured from Kargo Promotion timestamps; baseline captured in Sprint 1.",
        "Rollback of any stage to the previous Freight completes in < 10 min (game day in KARGO-29).",
        "Zero data loss during cutover (CNPG row counts identical before/after; backup verified).",
        "No gitops-repo write credential remains in pantry-api / pantry-frontend / pantry-db.",
    ],
    "dod": [
        "Manifests pass repo CI (kustomize build for all stages, kubeconform with CRD schemas, server-side dry-run where applicable).",
        "Any new behaviour documented in README/runbooks; comments explain *why*, matching repo convention.",
        "No secret values in git; all new credentials flow Key Vault -> ESO.",
        "Versions of all new third-party components pinned (chart version + image digest).",
    ],
}

MILESTONES = [
    ("M0", "Decide & de-risk", "ADRs ratified, Kargo<->Argo CD behaviour proven on kind, Kargo version pinned."),
    ("M1", "Platform foundation", "Kargo + Argo Rollouts CRDs running, SSO/RBAC, credentials, AppProjects."),
    ("M2", "Multi-stage repository", "base/stages layout, per-stage infra, ApplicationSet, repo CI, branch protection."),
    ("M3", "Promotion pipeline", "Warehouse -> dev -> staging -> prod promotions rendering to stage branches."),
    ("M4", "Verification & gates", "Automated verification, soak/approval policies, notifications."),
    ("M5", "Cutover & decommission", "Existing env adopted without data loss, CI bump removed, docs rewritten."),
    ("M6", "Hardening (stretch)", "Prod HA/backups, provenance, break-glass game day."),
]

# type: Spike | Story | Task.  pts: Fibonacci.  deps: list of keys.
STORIES = [
    # ---------------------------------------------------------------- M0
    dict(key="KARGO-01", ms="M0", type="Spike", pts=2, prio="Highest", repo="pantry-gitops",
         labels=["adr", "architecture"],
         summary="ADR-001: Stage topology & environment isolation",
         why="Kargo needs >= 2 isolated stages; the baseline has exactly one environment with namespaces, "
             "DB host and ingress host hard-coded. Topology drives every later story.",
         ac=["ADR merged under docs/adr/ deciding: stage list, namespace naming, host-per-stage vs path-per-stage, "
             "single cluster vs per-stage clusters, and per-stage vs shared Key Vault entries.",
             "Product owner answer recorded for 'is existing pantry-db data disposable?' and the dev adoption strategy derived from it.",
             "Resource budget for 3 stages estimated against current AKS node pool (incl. Kargo + Rollouts overhead).",
             "Explicit note on the stop-when-idle cluster and what that means for a stage named 'prod'."],
         deps=[]),
    dict(key="KARGO-02", ms="M0", type="Spike", pts=3, prio="Highest", repo="pantry-gitops",
         labels=["adr", "architecture"],
         summary="ADR-002: Git promotion pattern (rendered stage branches vs overlays on main)",
         why="Determines whether manifest/config changes are gated by the pipeline (analysis R6), whether "
             "argocd-update desiredRevision is unambiguous (R5), and whether Kargo ever rewrites human-authored, "
             "commented files (R9).",
         ac=["ADR compares both patterns on: config-change gating, revision ambiguity, Warehouse feedback loops, audit, "
             "prod gate, branch protection, comment preservation.",
             "Decision validated by the KARGO-05 PoC (not by documentation alone).",
             "Branch naming (`stage/<stage>`) and ownership rules (Kargo-only) recorded."],
         deps=["KARGO-05"]),
    dict(key="KARGO-03", ms="M0", type="Spike", pts=2, prio="High", repo="pantry-gitops",
         labels=["adr", "supply-chain"],
         summary="ADR-003: Freight composition & artifact identity (tags, digests, Warehouse layout)",
         why="`dev-<sha7>` tags are not orderable (only NewestBuild works) and put an environment name on "
             "artifacts promoted to prod. Single vs multiple Warehouses changes how api+migrate coupling behaves.",
         ac=["Decision: one Warehouse `pantry` with 3 image + 1 git subscription (or documented alternative).",
             "Phase-1 selection strategy (NewestBuild + allowTags regex + discoveryLimit) and Phase-2 target tag scheme agreed with app-repo owners.",
             "Promotion writes image digests, not tags - confirmed.",
             "GHCR visibility confirmed (public/private) and registry rate-limit impact of polling interval assessed; webhook-triggered discovery evaluated."],
         deps=[]),
    dict(key="KARGO-04", ms="M0", type="Spike", pts=3, prio="High", repo="pantry-db",
         labels=["adr", "database", "cross-repo"],
         summary="ADR-004: DB migration compatibility contract under promotion & rollback",
         why="Rollback = promoting older Freight; migrations are forward-only, so the older API will run against a newer "
             "schema (analysis R4). Without a contract, rollback is unsafe.",
         ac=["Expand/contract rule documented in pantry-db CONTRIBUTING + PR template checklist.",
             "Behaviour of the PreSync migrate Job when older Freight is promoted is tested and documented (expected: no-op).",
             "Schema-restore break-glass path defined (links to KARGO-27 pre-promotion backup)."],
         deps=[]),
    dict(key="KARGO-05", ms="M0", type="Spike", pts=5, prio="Highest", repo="pantry-gitops",
         labels=["spike", "argocd", "kargo"],
         summary="PoC: Kargo <-> Argo CD integration on kind (pin Kargo version)",
         why="Several integration behaviours must be proven, not assumed: authorization annotation, argocd-update with "
             "desiredRevision under auto-sync/selfHeal, ApplicationSet ownership, PreSync hook failure propagation, "
             "and verification prerequisites. Output pins the Kargo version used everywhere else.",
         ac=["kind cluster with Argo CD, cert-manager, Argo Rollouts CRDs, Kargo (pinned chart) running a 2-stage copy of this repo.",
             "Proven: an Application WITHOUT `kargo.akuity.io/authorized-stage` is refused by argocd-update; WITH it, sync succeeds.",
             "Proven: auto-sync + selfHeal left ON does not conflict with argocd-update; root app/ApplicationSet do not revert Kargo-triggered syncs.",
             "Proven: failing PreSync migration Job -> failed sync -> failed Promotion -> Freight not verified.",
             "Proven: infra-then-apps argocd-update ordering on a cold stage converges without relying on retry.",
             "Reference manifests in docs/kargo-epic/02 corrected to the pinned version's schema; findings written up."],
         deps=[]),
    # ---------------------------------------------------------------- M1
    dict(key="KARGO-06", ms="M1", type="Story", pts=5, prio="High", repo="pantry-infra",
         labels=["platform", "kargo", "cross-repo", "supply-chain"],
         summary="Add Kargo + Argo Rollouts CRDs to the platform contract (pantry-infra)",
         why="Kargo and the AnalysisTemplate API it uses for verification are new platform prerequisites, installed "
             "alongside Argo CD, cert-manager, CNPG and ESO (ADR-006).",
         ac=["Kargo Helm chart installed by pantry-infra Terraform at the version pinned in KARGO-05; images pinned by digest.",
             "Argo Rollouts CRDs (and controller if required by the pinned Kargo version) installed.",
             "Kargo uses existing cert-manager for webhook/API TLS; Kargo UI exposed via ingress-nginx with a letsencrypt-prod certificate.",
             "Kargo controller configured for Argo CD namespace `argocd`; controller restarts cleanly after `az aks stop/start`.",
             "Admin account disabled or its credential stored only in Key Vault; README 'Bootstrap' prerequisites list updated."],
         deps=["KARGO-05"]),
    dict(key="KARGO-07", ms="M1", type="Story", pts=3, prio="Medium", repo="pantry-infra",
         labels=["platform", "security", "rbac"],
         summary="Kargo SSO (Entra ID OIDC) and project RBAC",
         why="Promotion to prod must be restricted to an approver group; viewing should be broad.",
         ac=["Kargo API server authenticates via Entra ID OIDC.",
             "Groups mapped to Kargo roles: viewer (all), promoter (dev/staging), prod-approver (prod).",
             "A user outside prod-approver cannot promote to prod (tested).",
             "Group object IDs sourced from Terraform, not hard-coded in this repo."],
         deps=["KARGO-06"]),
    dict(key="KARGO-08", ms="M1", type="Story", pts=3, prio="High", repo="pantry-infra",
         labels=["security", "secrets", "cross-repo"],
         summary="Kargo credentials: GitHub App (git write) + GHCR read, delivered via Key Vault -> ESO",
         why="Kargo becomes the only automated writer to the gitops repo. Its credential must follow the baseline "
             "secret model (no values in git) and be narrower than today's CI tokens.",
         ac=["GitHub App installed on pantry-gitops only, contents:write + pull_requests:write; private key stored in Key Vault by Terraform.",
             "ExternalSecret in Kargo project namespace produces a Secret labelled `kargo.akuity.io/cred-type: git`.",
             "If GHCR packages are private: read-only credential projected with `kargo.akuity.io/cred-type: image`.",
             "Rotation procedure documented (rotate in KV -> ESO refresh -> Kargo picks up)."],
         deps=["KARGO-06"]),
    dict(key="KARGO-09", ms="M1", type="Story", pts=2, prio="High", repo="pantry-gitops",
         labels=["argocd", "gitops"],
         summary="AppProject `pantry-delivery` + Argo CD Application for kargo/; extend `pantry` destinations",
         why="Kargo `Project` is cluster-scoped and the `pantry` AppProject only whitelists Namespace. Keep workload "
             "project least-privilege and manage pipeline definitions via GitOps in their own project (ADR-005 #5).",
         ac=["New AppProject `pantry-delivery`: source = this repo, destination = ns `pantry`, cluster whitelist = kargo.akuity.io/Project only.",
             "New `argocd/kargo-application.yaml` syncs `kargo/` with sync-waves (Project before namespaced Kargo CRs).",
             "`pantry` AppProject destinations extended with staging/prod namespaces; nothing else widened.",
             "Root app picks both up with no manual kubectl."],
         deps=["KARGO-06"]),
    # ---------------------------------------------------------------- M2
    dict(key="KARGO-10", ms="M2", type="Story", pts=5, prio="High", repo="pantry-gitops",
         labels=["kustomize", "gitops"],
         summary="Restructure apps/ into base/apps + stages/<stage>/apps overlays",
         why="Stage-specific values are hard-coded today: `namespace: pantry-app`, DB_HOST "
             "`pantry-rw.pantry-db.svc...` (api + migrate Job), ingress host `pantry.example.com` (x3).",
         ac=["`base/apps` holds today's manifests with no stage-specific values; explanatory comments preserved.",
             "Overlays for dev/staging/prod set namespace, DB_HOST (via replacement/patch), ingress host + TLS secret, sizing.",
             "Equivalence check: rendered `stages/dev/apps` == rendered current `apps/` except intended differences (diff attached to PR).",
             "`apps/` left in place until cutover (KARGO-23) so the live env is untouched by this change."],
         deps=["KARGO-01", "KARGO-02"]),
    dict(key="KARGO-11", ms="M2", type="Story", pts=5, prio="High", repo="pantry-gitops + pantry-infra",
         labels=["infra", "database", "secrets", "cross-repo"],
         summary="Per-stage infra: namespaces, CNPG Cluster, ExternalSecrets, Key Vault entries, DNS",
         why="Each stage needs its own DB and credentials. infra/ spans two namespaces, so it must be split "
             "(app-ns / db-ns) for Kustomize namespace transforms. Sync waves (-15/-10/-5) must survive.",
         ac=["`base/infra/{app-ns,db-ns}` + `stages/<stage>/infra` render the correct namespaces, KV keys and sync-wave annotations.",
             "pantry-infra Terraform creates per-stage KV entries (db password, mcp tokens; anthropic key per ADR-001) and DNS records.",
             "Missing-KV-entry failure mode (mcp-creds Degraded) documented per stage, with Terraform applied first.",
             "Staging stage infra comes up healthy from zero on the real cluster."],
         deps=["KARGO-01", "KARGO-10"]),
    dict(key="KARGO-12", ms="M2", type="Story", pts=3, prio="High", repo="pantry-gitops",
         labels=["argocd", "applicationset"],
         summary="ApplicationSet generating pantry-{infra,apps}-<stage> Applications",
         why="Kargo requires one annotated Application per stage it promotes. Infra and apps MUST stay separate "
             "Applications: a PreSync hook runs before all waves, so merging them deadlocks a cold stage (analysis R3).",
         ac=["ApplicationSet (matrix: stage x layer) creates Applications targeting `stage/<stage>`, path `infra`|`apps`.",
             "Each carries `kargo.akuity.io/authorized-stage: pantry:<stage>`.",
             "Finalizer policy decided per stage and documented (no cascading delete into a stage DB).",
             "Staging + prod Applications live now; dev element added only during cutover (KARGO-23).",
             "`CreateNamespace=true` vs infra-owned Namespace double ownership resolved."],
         deps=["KARGO-10", "KARGO-11"]),
    dict(key="KARGO-13", ms="M2", type="Story", pts=3, prio="High", repo="pantry-gitops",
         labels=["ci", "quality", "supply-chain"],
         summary="CI for pantry-gitops: render, schema-validate and test every stage",
         why="There is no CI in this repo today; the existing unit tests are never run. With multiple stages, a broken "
             "overlay must fail in the PR, not in a promotion.",
         ac=["GitHub Actions workflow runs on PRs to main: `kustomize build` for every stages/*/*, kubeconform with CRD schemas "
             "(CNPG, ESO, Argo CD, Kargo, Rollouts), yamllint, `python3 -m unittest discover -s scripts`.",
             "Tooling binaries verified by checksum/digest pinned in the repo (consistent with bump_image_tag.py rationale); actions pinned by SHA.",
             "Kargo manifests validated against pinned CRDs (server-side dry-run on kind, or schema validation).",
             "Required status check on main."],
         deps=[]),
    dict(key="KARGO-14", ms="M2", type="Task", pts=1, prio="Medium", repo="GitHub settings",
         labels=["security", "github"],
         summary="Branch protection: main (PR + CI) and stage/* (Kargo GitHub App only)",
         why="Stage branches are machine-owned; humans editing them bypass Kargo's audit and verification.",
         ac=["main: PR required, KARGO-13 checks required, no direct pushes (incl. CI bots).",
             "stage/dev, stage/staging: push restricted to the Kargo GitHub App.",
             "stage/prod: changes only via PR with required review from prod-approver team; Kargo App may open PRs.",
             "Break-glass admin bypass documented (KARGO-29)."],
         deps=["KARGO-08"]),
    dict(key="KARGO-30", ms="M2", type="Task", pts=2, prio="Low", repo="pantry-gitops",
         labels=["tech-debt", "docs"],
         summary="Hygiene: ESO API version, documentation drift, placeholder host",
         why="Found during analysis (R10): docs still describe `kustomize edit set image`; ExternalSecrets use "
             "`external-secrets.io/v1beta1`; ingress host is a placeholder.",
         ac=["ExternalSecrets moved to the API version served by the installed ESO (v1 if available).",
             "README mermaid + comments in argocd/apps-application.yaml and apps/kustomization.yaml match reality.",
             "Placeholder host replaced by per-stage hosts from KARGO-10."],
         deps=[]),
    # ---------------------------------------------------------------- M3
    dict(key="KARGO-15", ms="M3", type="Story", pts=3, prio="High", repo="pantry-gitops",
         labels=["kargo", "warehouse"],
         summary="Kargo Project, ProjectConfig and Warehouse `pantry`",
         why="The Warehouse turns new images and config commits into Freight, the unit of promotion.",
         ac=["kargo/ contains Project, ProjectConfig and Warehouse per ADR-003; synced by the KARGO-09 Application.",
             "Git subscription limited to base/ and stages/ - a change in kargo/, argocd/ or docs/ creates NO Freight (tested).",
             "Pushing a new pantry-api image produces new Freight within the discovery interval (or webhook).",
             "Freight shows api, frontend, migrate digests + gitops commit in the UI."],
         deps=["KARGO-03", "KARGO-08", "KARGO-09"]),
    dict(key="KARGO-16", ms="M3", type="Story", pts=5, prio="High", repo="pantry-gitops",
         labels=["kargo", "promotion"],
         summary="PromotionTask `render-and-promote` (render to stage/<stage>, argocd-update infra then apps)",
         why="One reusable task implements the promotion for every stage, so stages can't drift in behaviour.",
         ac=["Task clones main at the Freight's commit, sets image digests, renders infra + apps, fully replaces stage branch contents.",
             "Commit message includes Freight ID and stage; rendered files contain digests (no tags).",
             "argocd-update runs infra first, then apps, each with desiredRevision = the new commit; no Application spec is modified.",
             "Re-promoting the same Freight is idempotent (no empty commits / or handled gracefully).",
             "Failure at any step leaves the stage branch and cluster in the previous good state."],
         deps=["KARGO-15", "KARGO-10", "KARGO-11", "KARGO-12"]),
    dict(key="KARGO-17", ms="M3", type="Story", pts=3, prio="High", repo="pantry-gitops",
         labels=["kargo", "promotion"],
         summary="Stages dev -> staging -> prod with dev auto-promotion",
         why="Defines the pipeline shape: only Freight verified upstream becomes eligible downstream.",
         ac=["Stage dev requests Freight directly from Warehouse; auto-promotion enabled via ProjectConfig.",
             "Stage staging sources from dev; prod sources from staging.",
             "Freight not verified in dev cannot be promoted to staging without explicit manual approval (tested).",
             "End-to-end: new api image -> dev automatically -> staging on one click, in the real cluster."],
         deps=["KARGO-16"]),
    dict(key="KARGO-18", ms="M3", type="Story", pts=3, prio="High", repo="pantry-gitops",
         labels=["kargo", "promotion", "governance"],
         summary="Prod promotion via pull request on stage/prod",
         why="Two independent gates for prod: Kargo RBAC (who may promote) and GitHub review (what exactly changes, "
             "as a rendered YAML diff).",
         ac=["Prod promotion pushes to a generated branch, opens a PR to stage/prod, waits for merge (git-open-pr / git-wait-for-pr).",
             "argocd-update targets the merge commit.",
             "Closing the PR without merge fails the Promotion cleanly.",
             "PR description links the Freight and the source commits/images."],
         deps=["KARGO-17", "KARGO-14"]),
    # ---------------------------------------------------------------- M4
    dict(key="KARGO-19", ms="M4", type="Story", pts=3, prio="High", repo="pantry-gitops",
         labels=["kargo", "verification"],
         summary="Smoke-test AnalysisTemplate used by every stage",
         why="Today nothing runs after deploy apart from probes; 'verified' Freight must mean something.",
         ac=["AnalysisTemplate `pantry-smoke` (job, image pinned by digest) checks api /health, frontend /healthz, "
             "and ingress routing of /pantry and /pantry/api on the stage host.",
             "Configured as verification on dev, staging and prod with stage-specific args.",
             "A deliberately broken build fails verification in dev and is not eligible for staging (tested)."],
         deps=["KARGO-17"]),
    dict(key="KARGO-20", ms="M4", type="Story", pts=5, prio="Medium", repo="pantry-gitops + pantry-api",
         labels=["verification", "cross-repo", "testing"],
         summary="Staging functional verification using a test image published by pantry-api",
         why="Smoke tests prove liveness, not correctness. Staging should gate prod on API behaviour "
             "(recipes, MCP auth, /plan with LLM calls stubbed or budget-capped).",
         ac=["pantry-api CI publishes a versioned test-runner image alongside the app image (same commit).",
             "Staging AnalysisTemplate runs it against the staging host; results visible in the AnalysisRun.",
             "LLM-dependent tests are stubbed or budget-capped (Anthropic spend per run documented).",
             "Runtime < 10 min; flaky tests quarantined, not retried blindly."],
         deps=["KARGO-19"]),
    dict(key="KARGO-21", ms="M4", type="Story", pts=2, prio="Medium", repo="pantry-gitops",
         labels=["kargo", "governance"],
         summary="Promotion policies: soak time, approvals, sync windows",
         why="Prod eligibility should require time-in-staging, not just a green test run.",
         ac=["Freight must soak in staging for an agreed duration before prod eligibility (requiredSoakTime or equivalent for the pinned version).",
             "Auto-promotion disabled for staging and prod.",
             "If Argo CD sync windows are used for prod, their interaction with Kargo-triggered syncs is tested and documented."],
         deps=["KARGO-17", "KARGO-07"]),
    dict(key="KARGO-22", ms="M4", type="Story", pts=3, prio="Low", repo="pantry-gitops",
         labels=["observability", "notifications"],
         summary="Promotion & verification notifications",
         why="Humans need to know when Freight is ready for prod or a promotion failed.",
         ac=["Failed promotion/verification in any stage notifies the team channel.",
             "Freight verified in staging and eligible for prod notifies prod-approvers.",
             "Mechanism chosen (Kargo-native if available in pinned version, else Argo CD Notifications on stage Applications) and documented."],
         deps=["KARGO-17"]),
    # ---------------------------------------------------------------- M5
    dict(key="KARGO-23", ms="M5", type="Story", pts=8, prio="Highest", repo="pantry-gitops",
         labels=["cutover", "database", "runbook"],
         summary="Cutover: adopt the live environment as stage dev with zero data loss",
         why="pantry-apps/pantry-infra carry resources-finalizer and the root app prunes: removing their files from git "
             "would cascade-delete the CNPG Cluster (analysis R1). This is the highest-risk change in the epic.",
         ac=["Runbook written and rehearsed end-to-end on kind with a seeded DB, including rollback steps.",
             "CNPG backup taken and restore-tested before the change.",
             "Finalizers removed / non-cascading deletion of pantry-apps and pantry-infra BEFORE their files leave git.",
             "pantry-{infra,apps}-dev adopt live resources; no pod restarts of the CNPG instance; row counts identical before/after.",
             "First Kargo promotion to dev succeeds; `kubectl get applications -n argocd` shows the new set, all Synced/Healthy."],
         deps=["KARGO-12", "KARGO-16", "KARGO-17", "KARGO-19"]),
    dict(key="KARGO-24", ms="M5", type="Story", pts=5, prio="High", repo="pantry-api, pantry-frontend, pantry-db",
         labels=["ci", "security", "cross-repo", "supply-chain"],
         summary="App repos: remove gitops bump step, revoke write credentials, adopt neutral tags",
         why="After cutover the bump commits to main would be ignored by Kargo but still confusing - and the "
             "credential is a standing risk. Phase 2 of ADR-003 tags images neutrally and orderably.",
         ac=["Bump step removed from all 3 workflows; gitops write credential deleted from each repo/org secret.",
             "Images additionally tagged per ADR-003 Phase 2; Warehouse switched to SemVer/Lexical; `dev-` prefix no longer required.",
             "Verified: no identity other than the Kargo GitHub App (and PR-merging humans) can write to pantry-gitops."],
         deps=["KARGO-23", "KARGO-03"]),
    dict(key="KARGO-25", ms="M5", type="Task", pts=1, prio="Low", repo="pantry-gitops",
         labels=["tech-debt"],
         summary="Retire scripts/bump_image_tag.py and legacy apps/ + argocd child apps",
         why="Superseded by Kargo's in-process kustomize-set-image. Keep for an agreed period as break-glass only.",
         ac=["Removed after 2 weeks of stable Kargo operation (or retained, documented as break-glass, if KARGO-29 needs it).",
             "Legacy apps/, argocd/apps-application.yaml, argocd/infra-application.yaml removed (their Applications already gone per KARGO-23)."],
         deps=["KARGO-24"]),
    dict(key="KARGO-26", ms="M5", type="Story", pts=3, prio="High", repo="pantry-gitops",
         labels=["docs", "runbook"],
         summary="Rewrite README + operator runbooks for the Kargo model",
         why="README describes the old loop, rollback by `git revert HEAD`, and 'Deploy a new API version - you don't'.",
         ac=["README: new loop diagram, layout, sync ordering per stage, Kargo concepts (Warehouse/Freight/Stage) in one paragraph each.",
             "Runbooks: promote, roll back (promote previous Freight + schema caveat), add a service, add a stage, rotate Kargo credentials.",
             "'Who can deploy what, how' table reflecting the new security model."],
         deps=["KARGO-23"]),
    # ---------------------------------------------------------------- M6
    dict(key="KARGO-27", ms="M6", type="Story", pts=8, prio="Medium", repo="pantry-gitops + pantry-infra",
         labels=["prod-readiness", "database", "ha"],
         summary="Prod stage hardening: CNPG HA + backups, pre-promotion backup, replicas, PDBs",
         why="Baseline runs 1 Postgres instance with no backup config and 1 replica per Deployment (analysis R7); rollback "
             "of a schema requires a backup taken before the prod migration (ADR-004).",
         ac=["Prod overlay: CNPG instances=3, barman backups to Azure Blob (storage + identity via pantry-infra), restore tested.",
             "A CNPG Backup is taken before each prod promotion's migration (PreSync hook in prod overlay or promotion step) and its success gates the sync.",
             "Prod Deployments: replicas >= 2, PodDisruptionBudgets, anti-affinity."],
         deps=["KARGO-11"]),
    dict(key="KARGO-28", ms="M6", type="Story", pts=5, prio="Low", repo="all app repos + pantry-gitops",
         labels=["supply-chain", "security"],
         summary="Artifact provenance: sign images in CI and verify before promotion",
         why="Digests make artifacts immutable; signatures prove where they came from.",
         ac=["CI signs images (cosign keyless / GitHub OIDC) and attaches SBOM attestations.",
             "Verification step (AnalysisTemplate job or admission policy) rejects unsigned images before staging.",
             "Approach documented against Kargo's native capabilities in the pinned version."],
         deps=["KARGO-24"]),
    dict(key="KARGO-29", ms="M6", type="Story", pts=2, prio="Medium", repo="pantry-gitops",
         labels=["runbook", "dr"],
         summary="Break-glass & DR procedures + rollback game day",
         why="Kargo becomes a critical path; the team must be able to deploy/roll back when it is down.",
         ac=["Break-glass: revert on stage/<stage> by admin bypass, documented + audited; Kargo reconciles afterwards.",
             "Game day executed: roll prod back one Freight in < 10 min; Kargo-down scenario exercised.",
             "Recovery of Kargo state documented (pipeline in git, history in stage branches)."],
         deps=["KARGO-23"]),
]


# Short labels for the dependency diagram (Mermaid-safe: no braces/backticks).
SHORT = {
    "KARGO-01": "ADR stage topology", "KARGO-02": "ADR promotion pattern", "KARGO-03": "ADR freight and tags",
    "KARGO-04": "ADR migration contract", "KARGO-05": "PoC Kargo and Argo CD", "KARGO-06": "Install Kargo + Rollouts",
    "KARGO-07": "SSO and RBAC", "KARGO-08": "Kargo credentials", "KARGO-09": "AppProjects + kargo app",
    "KARGO-10": "base + stage overlays", "KARGO-11": "Per-stage infra", "KARGO-12": "ApplicationSet",
    "KARGO-13": "Repo CI", "KARGO-14": "Branch protection", "KARGO-30": "Hygiene fixes",
    "KARGO-15": "Project + Warehouse", "KARGO-16": "PromotionTask", "KARGO-17": "Stages pipeline",
    "KARGO-18": "Prod via PR", "KARGO-19": "Smoke verification", "KARGO-20": "Staging functional tests",
    "KARGO-21": "Soak and approvals", "KARGO-22": "Notifications", "KARGO-23": "Cutover zero data loss",
    "KARGO-24": "App repo CI changes", "KARGO-25": "Retire legacy", "KARGO-26": "Docs and runbooks",
    "KARGO-27": "Prod hardening", "KARGO-28": "Provenance", "KARGO-29": "Break-glass and DR",
}


# Sprint plan (2-week sprints, ~2 engineers). A story may depend on work in the
# same sprint (sequenced inside it) but never on work in a later sprint.
SPRINTS = [
    ("Sprint 1", "Decide & prove", ["KARGO-05", "KARGO-01", "KARGO-03", "KARGO-04", "KARGO-02", "KARGO-13"]),
    ("Sprint 2", "Platform + layout", ["KARGO-06", "KARGO-08", "KARGO-09", "KARGO-10", "KARGO-30"]),
    ("Sprint 3", "Stages exist", ["KARGO-07", "KARGO-11", "KARGO-12", "KARGO-14", "KARGO-15"]),
    ("Sprint 4", "First promotions", ["KARGO-16", "KARGO-17", "KARGO-19", "KARGO-18"]),
    ("Sprint 5", "Gates + cutover", ["KARGO-23", "KARGO-21", "KARGO-20", "KARGO-22"]),
    ("Sprint 6", "Decommission", ["KARGO-24", "KARGO-26", "KARGO-25", "KARGO-29"]),
    ("Sprint 7", "Stretch", ["KARGO-27", "KARGO-28"]),
]


# Implementation status. "repo" = what exists in pantry-gitops; the rest is
# owned elsewhere (see docs/handoffs/) or needs a live cluster.
STATUS = {
    "KARGO-01": ("Done", "docs/adr/0001: dev adopted in place, staging on dev cluster, prod on prod cluster, hub on prod"),
    "KARGO-02": ("Done", "docs/adr/0002; promotion mechanics replayed offline against a bare repo"),
    "KARGO-03": ("Done", "docs/adr/0003; GHCR inferred public (no imagePullSecrets)"),
    "KARGO-04": ("Done (repo)", "docs/adr/0004; contract text for pantry-db in docs/handoffs/pantry-db.md"),
    "KARGO-05": ("Partial", "offline half done; 18-row live-cluster checklist in docs/kargo-epic/poc-kargo-argocd.md"),
    "KARGO-06": ("Handoff", "docs/handoffs/pantry-infra.md §1-2"),
    "KARGO-07": ("Done (repo) + handoff", "hub/kargo/rbac.yaml (Entra app roles); Entra + Helm values in handoff §5"),
    "KARGO-08": ("Done (repo) + handoff", "hub/kargo/credentials.yaml; GitHub App + Key Vault in handoff §4, §6"),
    "KARGO-09": ("Done", "hub/argocd/projects.yaml (per-stage + pantry-delivery), kargo-application.yaml"),
    "KARGO-10": ("Done", "base/apps + stages/*/apps + base/components/stage-params; dev == legacy proven in CI"),
    "KARGO-11": ("Done (repo) + handoff", "base/infra/{app-ns,db-ns} + stages/*/infra; Key Vault/DNS in handoff §4, §7"),
    "KARGO-12": ("Done", "hub/argocd/stages-appset.yaml; dev element enabled at cutover by design"),
    "KARGO-13": ("Done", ".github/workflows/validate.yaml + scripts/check_gitops.py (23 mutation tests)"),
    "KARGO-14": ("Scripted", "scripts/github_rulesets.sh + bootstrap_stage_branches.sh; run by repo admin"),
    "KARGO-30": ("Partial", "doc drift fixed, hosts centralised per stage; ESO v1 waits on cluster ESO versions"),
    "KARGO-15": ("Done (repo)", "hub/kargo/project.yaml, warehouse.yaml"),
    "KARGO-16": ("Done (repo)", "hub/kargo/promotion-tasks.yaml: render-and-push / render-and-pr"),
    "KARGO-17": ("Done (repo)", "hub/kargo/stages.yaml; dev auto-promotion flips on at cutover"),
    "KARGO-18": ("Done (repo)", "render-and-pr task + stage/prod rulesets"),
    "KARGO-19": ("Done (repo)", "pantry-smoke on every stage via public hosts; script tested against local TLS servers"),
    "KARGO-20": ("Done (repo) + handoff", "black-box pantry-api-contract gates staging; deeper suite optional (handoffs/app-repos.md §4)"),
    "KARGO-21": ("Done (repo)", "prod requiredSoakTime 1h; auto-promotion only for dev; no sync windows (ADR 0008)"),
    "KARGO-22": ("Done (repo) + handoff", "Argo CD Notifications on every stage app; Kargo-event forwarding is a documented gap"),
    "KARGO-23": ("Ready to execute", "docs/runbooks/cutover-dev.md + scripts/cutover.py (every state tested); needs the live hub"),
    "KARGO-24": ("Handoff", "docs/handoffs/app-repos.md §1-2; Warehouse SemVer switch specified"),
    "KARGO-25": ("Ready (cutover + 2 weeks)", "commands in cutover runbook step 9; post-retirement state tested"),
    "KARGO-26": ("Done", "README rewritten; delivery handbook in docs/runbooks/ (onboarding, on-call, promotion, rollback, troubleshooting, routine tasks, reference)"),
    "KARGO-27": ("Done (repo) + handoff", "prod HA/backups/PDBs + pre-promotion backup hook; storage, identity, health Lua in handoff §11"),
    "KARGO-28": ("Done (repo), not enabled", "pantry-provenance + no-bypass rule; enable after app repos sign (handoffs/app-repos.md §3)"),
    "KARGO-29": ("Done (docs)", "docs/runbooks/break-glass-and-dr.md incl. game day; first game day pending"),
}


# ---------------------------------------------------------------- renderers
def _by_key():
    return {s["key"]: s for s in STORIES}


def _validate():
    keys = _by_key()
    assert len(keys) == len(STORIES), "duplicate keys"
    ms_ids = {m[0] for m in MILESTONES}
    for s in STORIES:
        assert s["ms"] in ms_ids, s["key"]
        assert s["key"] in SHORT, f"missing SHORT label for {s['key']}"
        assert s["pts"] in (1, 2, 3, 5, 8, 13), s["key"]
        for d in s["deps"]:
            assert d in keys, f"{s['key']} depends on unknown {d}"
    assert set(STATUS) <= set(keys), "STATUS has unknown keys"
    # sprint plan: every story exactly once, no dependency scheduled later
    planned = [k for _, _, ks in SPRINTS for k in ks]
    assert sorted(planned) == sorted(keys), "sprint plan must contain every story exactly once"
    sprint_of = {k: i for i, (_, _, ks) in enumerate(SPRINTS) for k in ks}
    for s in STORIES:
        for d in s["deps"]:
            assert sprint_of[d] <= sprint_of[s["key"]], f"{s['key']} scheduled before its dependency {d}"
    # cycle check (DFS)
    state: dict[str, int] = {}

    def visit(k, path):
        if state.get(k) == 1:
            raise AssertionError("dependency cycle: " + " -> ".join(path + [k]))
        if state.get(k) == 2:
            return
        state[k] = 1
        for d in keys[k]["deps"]:
            visit(d, path + [k])
        state[k] = 2

    for k in keys:
        visit(k, [])


def _critical_path(exclude_ms=("M6",)):
    keys = {k: s for k, s in _by_key().items() if s["ms"] not in exclude_ms}
    memo: dict[str, tuple[int, list[str]]] = {}

    def longest(k):
        if k not in memo:
            best = (0, [])
            for d in keys[k]["deps"]:
                if d in keys:
                    best = max(best, longest(d), key=lambda t: t[0])
            memo[k] = (best[0] + keys[k]["pts"], best[1] + [k])
        return memo[k]

    return max((longest(k) for k in keys), key=lambda t: t[0])


def render_markdown() -> str:
    keys = _by_key()
    dependents: dict[str, list[str]] = {k: [] for k in keys}
    for s in STORIES:
        for d in s["deps"]:
            dependents[d].append(s["key"])
    total = sum(s["pts"] for s in STORIES)
    mvp = sum(s["pts"] for s in STORIES if s["ms"] != "M6")
    cp_pts, cp = _critical_path()

    out: list[str] = []
    w = out.append
    w("<!-- GENERATED by epic_backlog.py - edit the data there, not this file. -->")
    w(f"# 03 - Jira Epic: {EPIC['summary']}\n")
    w("## Epic\n")
    w(f"**Goal.** {EPIC['goal']}\n")
    w("**Business value**")
    w("\n".join(f"- {v}" for v in EPIC["value"]) + "\n")
    w("**In scope**")
    w("\n".join(f"- {v}" for v in EPIC["in_scope"]) + "\n")
    w("**Out of scope (follow-up epics)**")
    w("\n".join(f"- {v}" for v in EPIC["out_of_scope"]) + "\n")
    w("**Success metrics / epic acceptance**")
    w("\n".join(f"- {v}" for v in EPIC["success"]) + "\n")
    w("**Definition of Done (every story)**")
    w("\n".join(f"- {v}" for v in EPIC["dod"]) + "\n")

    w("## Summary\n")
    w(f"- **{len(STORIES)} issues**, **{total} story points** total; "
      f"**MVP (M0-M5) = {mvp} pts**, stretch (M6) = {total - mvp} pts.")
    w(f"- **MVP critical path ({cp_pts} pts of sequential work):** " + " → ".join(cp))
    w("- Suggested cadence: 2-week sprints, ~2 engineers (~18-20 pts/sprint) → MVP in ~5-6 sprints.\n")
    w("| Milestone | Theme | Exit criteria | Issues | Pts | Status |")
    w("|---|---|---|---|---|---|")
    for mid, name, exit_ in MILESTONES:
        items = [s for s in STORIES if s["ms"] == mid]
        sts = sorted({STATUS.get(s["key"], ("To do",))[0] for s in items})
        w(f"| {mid} | {name} | {exit_} | {', '.join(s['key'] for s in items)} | {sum(s['pts'] for s in items)} | "
          f"{' / '.join(sts)} |")
    w("")

    w("## Sprint plan\n")
    w("| Sprint | Focus | Issues (in order) | Pts |")
    w("|---|---|---|---|")
    for name, focus, ks in SPRINTS:
        w(f"| {name} | {focus} | {', '.join(ks)} | {sum(keys[k]['pts'] for k in ks)} |")
    w("\nSprint 1 front-loads the PoC (KARGO-05) because it pins the Kargo version and validates the reference "
      "manifests everything else builds on. KARGO-13 (repo CI) has no dependencies and should land first so every "
      "later restructuring PR is validated. KARGO-25 needs two stable weeks after KARGO-24, so it may slip past Sprint 6.\n")

    w("## Dependency graph\n")
    w("```mermaid\nflowchart LR")
    for mid, name, _ in MILESTONES:
        w(f'    subgraph {mid}["{mid} {name}"]')
        for s in STORIES:
            if s["ms"] == mid:
                w(f'        {s["key"].replace("-", "")}["{s["key"]}<br/>{SHORT[s["key"]]}"]')
        w("    end")
    for s in STORIES:
        for d in s["deps"]:
            w(f'    {d.replace("-", "")} --> {s["key"].replace("-", "")}')
    w("```\n")

    w("## Stories\n")
    for mid, name, _ in MILESTONES:
        w(f"### {mid} - {name}\n")
        for s in STORIES:
            if s["ms"] != mid:
                continue
            w(f"#### {s['key']} · {s['summary']}")
            st = STATUS.get(s["key"], ("To do", ""))
            w(f"**Status: {st[0]}**" + (f": {st[1]}" if st[1] else "") + "\n")
            w(f"`{s['type']}` · **{s['pts']} pts** · priority {s['prio']} · repo: {s['repo']} · "
              f"labels: {', '.join(s['labels'])}\n")
            w(f"**Why:** {s['why']}\n")
            w("**Acceptance criteria**")
            w("\n".join(f"- [ ] {a}" for a in s["ac"]))
            deps = ", ".join(s["deps"]) or "-"
            blocks = ", ".join(dependents[s["key"]]) or "-"
            w(f"\n*Depends on:* {deps} · *Blocks:* {blocks}\n")

    w("## Risk register (epic level)\n")
    w("| # | Risk | L | I | Mitigation | Owner story |")
    w("|---|---|---|---|---|---|")
    for row in RISKS:
        w("| " + " | ".join(row) + " |")
    w("")
    return "\n".join(out)


RISKS = [
    ("1", "Cascade deletion of CNPG cluster during restructure (finalizer + prune)", "M", "Critical",
     "Runbook, backup, finalizer removal before git change, kind rehearsal", "KARGO-23"),
    ("2", "Kargo v1alpha1 API churn between minor versions", "H", "M",
     "Pin version in PoC; CI validates against pinned CRDs; upgrade as explicit story", "KARGO-05/13"),
    ("3", "Rollback runs old API on new schema", "M", "H", "Expand/contract contract, pre-prod backup", "KARGO-04/27"),
    ("4", "Kargo/Argo CD fight over Application state", "L", "H",
     "Kargo never edits Application spec; proven in PoC", "KARGO-05/12"),
    ("5", "Cross-repo changes (pantry-infra, app repos) stall the epic", "M", "M",
     "Cross-repo stories labelled; owners named at kickoff; Phase-1 needs no app-repo change", "KARGO-06/08/11/24"),
    ("6", "Cluster stop/start interrupts promotions & AnalysisRuns", "M", "L",
     "Documented; promotions are re-runnable; prod hosting decision in ADR-001", "KARGO-01"),
    ("7", "3x workloads exceed the small AKS node pool", "M", "M", "Budget in ADR-001; small staging sizing", "KARGO-01"),
    ("8", "Registry polling rate limits with NewestBuild", "M", "L",
     "discoveryLimit, interval, webhook receiver; Phase-2 orderable tags", "KARGO-03/24"),
]


def render_csv(path: Path) -> None:
    """Jira Cloud CSV importer format.

    Map on import: Issue Id -> Issue Id, Parent -> Parent (by Issue Id),
    Labels (repeated column) -> Labels, Story Points -> Story Points,
    Depends On (repeated) -> Link 'Blocks' (inward) or leave unmapped.
    """
    max_labels = max(len(s["labels"]) for s in STORIES) + 2  # + milestone + kargo-epic
    max_deps = max(len(s["deps"]) for s in STORIES)
    header = (["Issue Id", "Issue Type", "Summary", "Description", "Priority", "Story Points", "Parent"]
              + ["Labels"] * max_labels + ["Depends On"] * max_deps)
    ids = {s["key"]: str(i + 2) for i, s in enumerate(STORIES)}  # epic = 1

    def jira_desc_epic():
        parts = [f"h3. Goal\n{EPIC['goal']}", "h3. Business value\n" + "\n".join(f"* {v}" for v in EPIC["value"]),
                 "h3. In scope\n" + "\n".join(f"* {v}" for v in EPIC["in_scope"]),
                 "h3. Out of scope\n" + "\n".join(f"* {v}" for v in EPIC["out_of_scope"]),
                 "h3. Success metrics\n" + "\n".join(f"* {v}" for v in EPIC["success"]),
                 "h3. Definition of Done\n" + "\n".join(f"* {v}" for v in EPIC["dod"]),
                 "Design docs: docs/kargo-epic/01-current-state-analysis.md, 02-target-architecture.md"]
        return "\n\n".join(parts)

    def jira_desc(s):
        deps = ", ".join(s["deps"]) or "none"
        return (f"h3. Why\n{s['why']}\n\nh3. Acceptance criteria\n"
                + "\n".join(f"# {a}" for a in s["ac"])
                + f"\n\nh3. Context\n* Milestone: {s['ms']}\n* Repo: {s['repo']}\n* Depends on: {deps}\n"
                  f"* Backlog key: {s['key']}")

    def wiki(text):
        # Markdown `code` -> Jira wiki {{code}}
        return re.sub(r"`([^`]+)`", r"{{\1}}", text)

    def pad(lst, n):
        return lst + [""] * (n - len(lst))

    type_map = {"Spike": "Task", "Task": "Task", "Story": "Story"}  # Jira has no native Spike type
    with path.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        wr.writerow(header)
        wr.writerow(["1", "Epic", EPIC["summary"], wiki(jira_desc_epic()), "High", "", ""]
                    + pad(["kargo-epic", "progressive-delivery"], max_labels) + pad([], max_deps))
        for s in STORIES:
            labels = ["kargo-epic", s["ms"].lower()] + s["labels"] + (["spike"] if s["type"] == "Spike" and "spike" not in s["labels"] else [])
            labels = list(dict.fromkeys(labels))[:max_labels]
            summary = f"[{s['key']}] {s['summary']}"
            wr.writerow([ids[s["key"]], type_map[s["type"]], summary, wiki(jira_desc(s)), s["prio"], s["pts"], "1"]
                        + pad(labels, max_labels) + pad([ids[d] for d in s["deps"]], max_deps))


if __name__ == "__main__":
    _validate()
    (HERE / "03-epic-and-stories.md").write_text(render_markdown(), encoding="utf-8")
    render_csv(HERE / "jira-import.csv")
    print(f"{len(STORIES)} stories, {sum(s['pts'] for s in STORIES)} pts -> 03-epic-and-stories.md, jira-import.csv")
