# ADR 0001: Stage topology, cluster mapping and control-plane placement

**Status:** Accepted · **Epic story:** KARGO-01

## Context
- The baseline has one environment on a dev AKS cluster that is **stopped when idle**.
- The product owner has decided: **existing `pantry-db` data must persist**, and **prod runs on a separate prod cluster**.
- Kargo can only promote through Argo CD Applications it can see. One Kargo controller watches one Argo CD
  control plane. Splitting Argo CD per cluster would need Kargo sharding (agents per cluster), which is more
  moving parts than this platform needs.

## Decision

| Stage | Cluster (Argo CD name) | App namespace | DB namespace | Key Vault keys (db / mcp) | Host (CHANGE ME) |
|---|---|---|---|---|---|
| dev | `dev` | `pantry-app` *(adopted in place)* | `pantry-db` *(adopted)* | `pantry-db-password` / `pantry-mcp-tokens` | `dev.pantry.example.com` |
| staging | `dev` | `pantry-staging-app` | `pantry-staging-db` | `pantry-staging-db-password` / `pantry-staging-mcp-tokens` | `staging.pantry.example.com` |
| prod | `prod` | `pantry-app` | `pantry-db` | `pantry-db-password` / `pantry-mcp-tokens` *(prod Key Vault)* | `pantry.example.com` |

- **Dev is adopted in place.** It keeps the exact namespaces, Key Vault keys and CNPG `Cluster` spec it has today,
  so adoption changes no data. `scripts/check_gitops.py` enforces this. Its *legacy equivalence* check fails CI
  if the rendered dev stage differs from the live `apps/` + `infra/` in anything but the allow-listed fields.
- **Staging shares the dev cluster.** It needs distinct namespaces and Key Vault keys because both stages use
  the dev cluster's `ClusterSecretStore`. `check_gitops.py` fails if two stages on one cluster collide.
- **Prod reuses the canonical names.** It is a separate cluster with a separate Key Vault.
- **One Anthropic key** (`anthropic-api-key`) for all stages, per the product owner. pantry-infra writes the
  same value into both Key Vaults.
- **Hosts per stage, not path prefixes per stage.** The SPA's `/pantry` base path and the API's `--root-path` must
  stay the same in every stage, or we could no longer build once and promote the same bytes.

### Control plane: hub-and-spoke on the prod cluster
Argo CD and Kargo (the "hub") run on the **prod cluster** and deploy to the dev cluster as a remote cluster.

| Option | Verdict |
|---|---|
| Hub on the dev cluster | ❌ Prod self-heal and prod promotions would stop every time dev is stopped. A lower environment would hold admin credentials to prod |
| **Hub on the prod cluster** | ✅ Always on. Credentials only point from prod to dev (a higher environment controlling a lower one). No extra cost |
| Dedicated management cluster | ⭐ Best long-term (prod workloads don't share a node pool with the control plane), but extra cost. The repo is **location-independent** (named destinations `dev`/`prod`, and delivery resources target "the hub itself"), so moving there later is a bootstrap change, not a redesign |

While dev is stopped, its Applications show `Unknown`, and promotions to dev/staging fail fast and can be re-run.
Prod is unaffected.

## Consequences
- A new bootstrap root, `hub/bootstrap/root-app.yaml`, is applied once to the hub's Argo CD.
  The legacy `argocd/` tree keeps running the current environment on the dev cluster's own Argo CD **until the
  cutover (KARGO-23)**. The two trees never manage the same namespace: the `dev` ApplicationSet element stays
  disabled until the cutover.
- pantry-infra must: register the dev cluster in the hub's Argo CD as `dev`, name the hub's in-cluster `prod`,
  create the per-stage Key Vault entries, and point DNS at the right ingress IPs (see `docs/handoffs/pantry-infra.md`).
