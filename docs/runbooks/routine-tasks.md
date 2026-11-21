# Routine DevOps tasks

Every task ends the same way: **a PR to `main`, CI green, reviewed, merged.** What differs is what you
edit, and whether the change travels as Freight (`base/`, `stages/`) or applies straight away (`hub/`).

| Path changed | Becomes Freight? | Takes effect |
|---|---|---|
| `base/**`, `stages/**` | **yes** | stage by stage, as it's promoted (dev → staging → prod) |
| `hub/kargo/**` | no | on merge, via Argo CD app `pantry-kargo` (pipeline definition) |
| `hub/argocd/**` | no | on merge, via `pantry-hub-root` (AppProjects, ApplicationSet) |
| `docs/**`, `scripts/**`, `.github/**` | no | repo only |

Before every PR: `python3 -m unittest discover -s scripts && python3 scripts/check_gitops.py`.

---

## 1. Change an app's configuration (env var, resources, probes…)
1. Edit under `base/apps/` (all stages) or patch under `stages/<stage>/` (one stage).
2. Preview: `scripts/render_stages.sh /tmp/r` and `diff` the stage files against the previous render.
3. **Before the cutover:** make the same change in the legacy `apps/` tree (`check_legacy_equivalence` insists).
4. PR → merge. New Freight appears with the same images and your commit. Promote it like any Freight
   ([promotion.md](promotion.md)).
5. Env var changes restart pods when synced. Secret *values* don't live here ([§7](#7-rotate-credentials)).

## 2. Change the pipeline (Kargo or Argo CD definitions)
1. Edit `hub/kargo/*.yaml` or `hub/argocd/*.yaml`. Keep the two PromotionTasks' render steps identical
   (CI checks it).
2. PR → CI validates against the pinned Kargo CRDs → merge. Argo CD applies it within minutes:
   `kubectl --context hub -n argocd get application pantry-kargo`.
3. **Risky changes:** Stage `sources`, PromotionTask steps, the Warehouse. Merge when no Promotion is
   running, then do a staging promotion to prove it.
4. Never edit these objects in the Kargo UI. `pantry-kargo` self-heals them back.

## 3. Add a service
1. `base/apps/<svc>/` with Deployment + Service + `kustomization.yaml`; list it in `base/apps/kustomization.yaml`.
2. `hub/kargo/warehouse.yaml`: a new image subscription (set `imageSelectionStrategy`, `strictSemvers`,
   `discoveryLimit` explicitly).
3. `hub/kargo/promotion-tasks.yaml`: add the image to `kustomize-set-image` in **both** tasks.
4. If it uses a new resource kind, add it to each `pantry-<stage>` AppProject's whitelist.
5. If it needs ingress or secrets: extend the Ingresses / ExternalSecrets and the `stage-params` component.
6. `check_gitops.py` flags anything missed (image sets must match across base, Warehouse and tasks).
7. Merge `hub/` first or together. Once the image exists in GHCR, new Freight contains it.

## 4. Add a stage
1. Copy `stages/staging` to `stages/<new>` and edit `params/stage-params.yaml`: namespaces, host and Key Vault
   keys **unique on that cluster**. Update `namespace:` in its `apps`, `infra/app-ns` and `infra/db-ns` kustomizations.
2. `hub/argocd/projects.yaml`: AppProject `pantry-<new>` (destinations = its cluster + two namespaces).
3. `hub/argocd/stages-appset.yaml`: a list element (`stage`, `cluster`, `appNamespace`, `dbNamespace`).
4. `hub/kargo/stages.yaml`: a Stage with `requestedFreight.sources`, task, and verification (`host` = params host).
5. `hub/kargo/rbac.yaml`: add it to `pantry-promoter`'s `resourceNames` if promoters may use it.
6. pantry-infra: Key Vault entries + DNS. Repo admin: `scripts/bootstrap_stage_branches.sh`, and extend
   the rulesets in `scripts/github_rulesets.sh`.
7. First promotion creates the render. Before that, its Applications show a ComparisonError.

## 5. Change a stage's hostname
1. DNS record for the new name → the stage cluster's ingress IP (pantry-infra).
2. One PR changing **both** `stages/<stage>/params/stage-params.yaml` `host` **and** the Stage's
   verification `host` arg in `hub/kargo/stages.yaml` (CI fails if they differ).
3. Promote. cert-manager issues a certificate for the new name (HTTP-01). Verification fails until it's
   Ready, so re-verify then ([T14](troubleshooting.md#t14-tls-certificate-problems)).

## 6. Postgres version or CNPG settings
- **Minor version** (e.g. `imageName: …postgresql:17.5` → `17.6`) in `base/infra/db-ns/postgres-cluster.yaml`:
  it's config Freight, promoted stage by stage. CNPG does a rolling update. Single-instance dev/staging
  restart their only instance (a short outage, typically under a minute). Prod (3 instances) updates the
  replicas first, then switches over.
  **Before the cutover**, mirror it in legacy `infra/postgres-cluster.yaml`.
- **Major version** is **not** an in-place image bump. Plan a CNPG major upgrade (import/logical
  replication) as its own project.
- **Prod-only settings** (instances, backups): `stages/prod/infra/db-ns/cluster-ha-backups.yaml`.
  `check_prod_hardening` enforces ≥ 3 instances, backups, the hook and PDBs.
- Never remove `Prune=false,Delete=false` from the `Cluster`.

## 7. Rotate credentials
| Secret | Steps |
|---|---|
| **Kargo GitHub App key** | 1. GitHub → App `kargo-pantry` → generate a new private key. 2. Update `kargo-github-app-private-key` in the **hub** Key Vault. 3. `kubectl --context hub -n pantry annotate externalsecret kargo-git-pantry-gitops force-sync=$(date +%s) --overwrite`. 4. Run a staging promotion to prove it. 5. Delete the old key in GitHub |
| **Stage DB password** | 1. New value in the stage's `kvDbPasswordKey` (dev/staging: dev vault; prod: prod vault). 2. Force-sync `pantry-app-credentials` in **both** the app and db namespaces. 3. Change the role's password in Postgres to match: `kubectl cnpg psql pantry -n <db ns> --context <ctx> -- -c "ALTER ROLE pantry_app PASSWORD '…'"` (or let the DB owner do it). 4. `kubectl --context <ctx> -n <app ns> rollout restart deploy/pantry-api` |
| **MCP tokens** | 1. Update the stage's `kvMcpTokensKey` (`label:secret[,label:secret]`). 2. Force-sync `pantry-mcp-credentials`. 3. Restart `pantry-api` (env is read at start) |
| **Anthropic API key** | **One key for all stages**: update `anthropic-api-key` in **both** vaults, force-sync `anthropic-credentials` in every app namespace, restart `pantry-api` in every stage |
| **Teams webhook** | pantry-infra: update `argocd-notifications-secret` |

## 8. Start / stop the dev cluster
- `az aks start -g <rg> -n <dev-aks>` / `az aks stop …`. Argo CD reconnects and self-heals within minutes
  of start.
- While stopped: dev/staging Applications show `Unknown`, and their promotions and verification fail.
  **Prod is unaffected.**
- After a start: promote the newest Freight to dev if auto-promotion failed while it was down
  ([T2](troubleshooting.md#t2-dev-did-not-auto-promote-new-freight)).
- Don't stop it in the middle of a staging promotion or verification.

## 9. Freeze changes (holiday, incident, audit)
| Scope | How |
|---|---|
| Stop dev from auto-updating | PR `autoPromotionEnabled: false` for `dev` in `hub/kargo/project.yaml` |
| Stop prod promotions | Announce the freeze. Prod needs a ProdApprover *and* a PR merge, so ask approvers not to merge. Hard freeze: remove eligible PIM assignments for the period |
| Stop *all* syncs to prod | Argo CD **sync window** on AppProject `pantry-prod` (PR to `hub/argocd/projects.yaml`). This also blocks Kargo's prod `argocd-update` and self-heal, so use it only for real freezes |

## 10. Operational actions in Kargo [`Pantry.Oncall`]
| Action | When | How |
|---|---|---|
| **Refresh** the Warehouse | an image is in GHCR but no Freight yet | `kargo refresh warehouse pantry` ⚑ / UI → Warehouse → Refresh |
| **Re-verify** a Stage | verification failed for an environmental reason (cert, DNS, dev stopped) | `kargo verify stage <stage>` ⚑ / UI → Stage → Reverify |
| **Abort** a Promotion | stuck beyond normal timings, or started by mistake | `kargo promote --name <promotion> --abort` ⚑ / UI → Promotion → Abort |
| **Alias** a Freight | quarantine (`bad-<id>`), or label a release | UI → Freight → Edit alias |
| **Approve** Freight for a Stage | IC-approved expedite ([promotion.md §7](promotion.md#7-expediting-a-hotfix)) | `kargo approve --freight <name> --stage <stage>` ⚑ [needs promote on that stage] |

## 11. Assemble Freight by hand
Needed when the exact combination you want doesn't exist as Freight (e.g. the cutover, or pinning one
image while taking another's newest): Kargo UI → Warehouse `pantry` → *Assemble Freight* → pick each
image tag and the commit → create. It then behaves like any Freight (verification rules still apply).

## 12. Switch image discovery to SemVer (ADR 0003, Phase 2)
Once all three app repos push `X.Y.Z` tags (`docs/handoffs/app-repos.md` §2), one PR to
`hub/kargo/warehouse.yaml`, per image:
`imageSelectionStrategy: SemVer`, `strictSemvers: true`, remove `allowTags`. Existing Freight is unaffected.
New Freight is chosen by version instead of build time.

## 13. Enable the provenance gate
Once all three repos sign images (`docs/handoffs/app-repos.md` §3), follow the header of
`hub/kargo/analysis-provenance.yaml`. Add it to **every** stage that takes Freight straight from the
Warehouse; CI refuses a partial enablement. Test by promoting to dev.

## 14. Pin verification images by digest
`crane digest curlimages/curl:8.11.1` (and likewise for the python and cosign images), then replace
`image: <name>:<tag>` with `image: <name>@sha256:<digest>` in `hub/kargo/analysis-*.yaml`.

## 15. Upgrade Kargo
1. Read the release notes between the current and target versions (breaking CRD/field changes).
2. PR bumping `KARGO_VERSION` in `.github/workflows/validate.yaml`. CI validates `hub/kargo/` against the
   new CRDs. Fix any field changes in the **same** PR.
3. pantry-infra upgrades the chart on the hub (same version). Don't let CI and the hub differ for long.
4. Run one staging promotion and one verification before the next prod promotion.
5. Update the Kargo CLI (download from the UI).

## 16. Upgrade platform components (Argo CD, CNPG, ESO, cert-manager, Rollouts)
These are installed by pantry-infra, per cluster, and **do not** travel through Kargo. Upgrade the dev
cluster first, run a staging promotion, then the prod/hub cluster. Watch out for:
- **ESO:** the manifests use `external-secrets.io/v1beta1`. Keep a version that serves it until this repo
  moves to `v1` (KARGO-30).
- **CNPG:** backups use in-tree `barmanObjectStore`. Check its deprecation status before upgrading (ADR 0008).
- **Argo CD:** keep the custom health check `postgresql.cnpg.io_Backup` in `argocd-cm`, or the prod backup
  hook stops gating.

## 17. Monthly: prove backups restore
Run game-day scenario 7 ([break-glass-and-dr.md](break-glass-and-dr.md#game-day-run-quarterly-first-time-before-prod-go-live)),
a point-in-time restore of prod's backups into a scratch `Cluster` **in staging**. Record the restore time.

## 18. Housekeeping
- Kargo's garbage collector prunes old Promotions and Freight (Helm values on the hub). The permanent
  record is `git log origin/stage/<stage>`.
- Old `kargo/…` PR branches (from prod promotions) can be deleted after merge. Enable "automatically
  delete head branches" in the repo settings.
