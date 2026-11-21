# ADR 0008: Verification, gates and prod hardening

**Status:** Accepted · **Epic stories:** KARGO-19/20/21/22 (M4), KARGO-27/28 (M6)

## Decision
**What each stage must prove before its Freight moves on:**

| Stage | Verification | Extra gate to enter it |
|---|---|---|
| dev | `pantry-smoke` | none (auto-promote after cutover) |
| staging | `pantry-smoke` + `pantry-api-contract` | Freight verified in dev; `Pantry.Promoter` |
| prod | `pantry-smoke` (post-deploy) | verified in staging **+ soaked ≥ 1 h** (`requiredSoakTime`); `Pantry.ProdApprover` + merged PR (ADR 0007); pre-promotion backup succeeded |

- **Verification runs on the hub, against public hosts.** The Jobs run where Kargo runs (the prod cluster),
  but dev and staging live on the dev cluster, so in-cluster Service DNS isn't reachable. Going through
  the public host also proves DNS, ingress routing and TLS; curl verifies the certificate.
- **Black-box over in-repo tests.** The API contract check needs nothing from pantry-api beyond its public
  OpenAPI. It never calls `/plan` (LLM cost). A deeper suite is an optional pantry-api follow-up.
- **Provenance is built but not enforced yet.** `pantry-provenance` (keyless cosign) would freeze the
  pipeline until all three repos sign. Once enabled anywhere, `check_gitops.py` requires it on **every**
  stage that takes Freight directly from the Warehouse, so there's no side door.
- **Prod pre-promotion backup = an infra-layer Sync hook (wave -4, after the Cluster).** A PreSync hook in
  the apps layer would sit in the app namespace: Kustomize's namespace transformer runs after
  replacements, which we proved while building it. The infra layer is also where it belongs. Kargo syncs
  infra before apps, so a failed backup stops the promotion before the migration. It depends on the Argo CD
  health check in `hub/platform/argocd-health-cnpg-backup.lua`; without it the hook gates nothing.
- **Prod HA:** CNPG 3 instances with preferred anti-affinity, WAL archiving + daily base backups to Azure
  Blob via Workload Identity, 2 replicas per stateless Deployment, spread across nodes, with PDBs.
  Dev and staging keep the baseline sizing (dev must stay byte-identical to the live legacy).
- **Notifications via Argo CD Notifications** on every stage Application. Kargo-level failures that never
  reach a sync (e.g. a closed PR) need an event forwarder, which is a documented gap (pantry-infra handoff §10).

## Consequences
- A stage's `host` now lives in two places: params, and the Stage verification args. CI fails if they differ.
- While the dev cluster is stopped, dev/staging verification fails. Re-run it after start.
- Backups use CNPG's in-tree `barmanObjectStore`. Newer CNPG releases move to the Barman Cloud **plugin**;
  migrate when the platform's CNPG version deprecates the in-tree path.
