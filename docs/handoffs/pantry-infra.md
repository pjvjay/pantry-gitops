# Handoff → `pantry-infra` (M1 platform work)

Covers epic stories **KARGO-06** (Kargo + Argo Rollouts), **KARGO-07** (SSO/RBAC), **KARGO-08**
(credentials), the infra half of **KARGO-11** (per-stage Key Vault entries + DNS), and the platform parts
of M4 (**KARGO-22** notifications) and M6 (**KARGO-27** prod backups). This repo is ready
for all of it. Nothing here applies until the hub is bootstrapped (step 9).

> The Terraform and Helm values below are **illustrative**. They were written without access to the
> `pantry-infra` code or the pinned chart. Check the value keys against `helm show values` for the pinned
> Kargo version before applying.

## 0. Target topology (ADR 0001)

```
                 prod AKS cluster  (always on)                     dev AKS cluster (stopped when idle)
   ┌────────────────────────────────────────────────────┐   ┌──────────────────────────────────────────┐
   │ HUB: Argo CD + Kargo + Argo Rollouts               │   │ legacy Argo CD (pantry-root)             │
   │   pantry-hub-root ─► hub/argocd/                   │   │   runs today's env UNTIL cutover (M5)     │
   │   Kargo project `pantry` (hub/kargo/)              │──►│ dev     : pantry-app / pantry-db          │
   │ prod    : pantry-app / pantry-db                   │   │ staging : pantry-staging-app / -db        │
   └────────────────────────────────────────────────────┘   └──────────────────────────────────────────┘
```

## 1. Prod cluster: same platform contract as dev
ingress-nginx, cert-manager with `ClusterIssuer letsencrypt-prod`, CloudNativePG operator, External Secrets
Operator with `ClusterSecretStore azure-key-vault` pointing at the **prod** Key Vault, and storage class
`managed-csi`.

**ESO version:** the manifests use `external-secrets.io/v1beta1`. Run an ESO release that still serves
`v1beta1` **on both clusters** (0.16.x serves both `v1` and `v1beta1`). Once both clusters are on a
release that serves `v1`, this repo switches (tracked in KARGO-30).

## 2. Hub components on the prod cluster

| Component | Pin | Notes |
|---|---|---|
| Argo CD | current | becomes the hub; OIDC to Entra (see §5) |
| Argo Rollouts | chart + image digest | **Required**: every Stage has verification (`hub/kargo/analysis-templates.yaml`), which uses its `AnalysisTemplate` API. Without it, Kargo can't verify Freight and nothing reaches prod |
| Kargo | **same version as `KARGO_VERSION` in `.github/workflows/validate.yaml`** | chart `oci://ghcr.io/akuity/kargo-charts/kargo`; needs cert-manager (present) |

Kargo values (check the keys against the pinned chart):

```yaml
api:
  host: kargo.example.com                  # CHANGE ME
  ingress:
    enabled: true
    ingressClassName: nginx
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod
    tls:
      enabled: true
  adminAccount:
    enabled: false                         # SSO only; break-glass = Kargo.Admin app role
  oidc:
    enabled: true
    issuerURL: https://login.microsoftonline.com/<tenant-id>/v2.0
    clientID: <kargo app registration client id>
    cliClientID: <same, public client with PKCE>
    admins:
      claims:
        roles: [Kargo.Admin]               # ADR 0007. Project roles are in hub/kargo/rbac.yaml
controller:
  argocd:
    namespace: argocd
    watchArgocdNamespaceOnly: true
```

```hcl
resource "helm_release" "kargo" {
  name             = "kargo"
  namespace        = "kargo"
  create_namespace = true
  repository       = "oci://ghcr.io/akuity/kargo-charts"
  chart            = "kargo"
  version          = var.kargo_version   # == KARGO_VERSION in pantry-gitops CI
  values           = [file("${path.module}/values/kargo.yaml")]
  depends_on       = [helm_release.cert_manager, helm_release.argo_rollouts]
}
```

## 3. Argo CD cluster registration on the hub
The hub refers to clusters **by name** (`hub/argocd/projects.yaml`, `stages-appset.yaml`):

| Name | Server | How |
|---|---|---|
| `prod` | `https://kubernetes.default.svc` | A cluster Secret (label `argocd.argoproj.io/secret-type: cluster`) with `name: prod` and `server: https://kubernetes.default.svc` renames the built-in `in-cluster` |
| `dev` | dev AKS API server | A cluster Secret with Workload Identity / `azure` exec auth to the dev cluster. Scope the identity to the dev cluster only |

Argo CD then sees the dev cluster as `Unknown` while dev is stopped. That's expected.

## 4. Key Vault entries (ADR 0001)

| Vault | Secret name | Value | Consumer |
|---|---|---|---|
| dev | `pantry-db-password` | **existing, unchanged** | dev stage (adopted) |
| dev | `pantry-mcp-tokens` | **existing, unchanged** | dev stage |
| dev | `anthropic-api-key` | **existing** | dev + staging |
| dev | `pantry-staging-db-password` | new `random_password` | staging |
| dev | `pantry-staging-mcp-tokens` | new `label:secret[,…]` | staging |
| prod | `pantry-db-password` | new `random_password` | prod |
| prod | `pantry-mcp-tokens` | new | prod |
| prod | `anthropic-api-key` | **same value as dev** (one key for all stages, per product owner) | prod |
| prod | `kargo-github-app-id` | GitHub App id | `hub/kargo/credentials.yaml` |
| prod | `kargo-github-app-installation-id` | installation id on pantry-gitops | 〃 |
| prod | `kargo-github-app-private-key` | PEM | 〃 |

Create these **before** the stage's first promotion. A missing entry leaves the stage's ExternalSecrets
Degraded, and `pantry-mcp-credentials` holds the infra sync at that wave, as it does today.

## 5. Entra ID (ADR 0007)
1. App registration **kargo-pantry-hub**:
   - web redirect `https://kargo.example.com/login`
   - public client (PKCE) redirect `http://localhost/auth/callback` for the CLI
   - `assignment required = yes`
2. App roles, each with **value** exactly:
   - `Pantry.Viewer`
   - `Pantry.Promoter`
   - `Pantry.ProdApprover`
   - `Pantry.Oncall`
   - `Kargo.Admin`
3. Assign groups to the roles:

   | Group | App role |
   |---|---|
   | `pantry-delivery-viewers` | `Pantry.Viewer` |
   | `pantry-delivery-promoters` | `Pantry.Promoter` |
   | `pantry-prod-approvers` | `Pantry.ProdApprover` (make membership **PIM-eligible**) |
   | `pantry-oncall` | `Pantry.Oncall` (**PIM-eligible**, max 12 h activation) |
   | `platform-admins` | `Kargo.Admin` |
4. Do the same for Argo CD's app registration. In `argocd-rbac-cm`, humans get read-only access to
   `pantry-prod/*`, and only `platform-admins` can sync or delete there. Prod changes come through Kargo.

```hcl
resource "azuread_application" "kargo" {
  display_name = "kargo-pantry-hub"
  app_role {
    allowed_member_types = ["User"]
    description          = "Promote to prod"
    display_name         = "Pantry prod approver"
    id                   = random_uuid.prod_approver.result
    value                = "Pantry.ProdApprover"
  }
  # … Pantry.Viewer, Pantry.Promoter, Kargo.Admin likewise
}
```

## 6. GitHub App `kargo-pantry` (manual: GitHub Apps can't be created by Terraform)
- Permissions: Contents **read/write**, Pull requests **read/write**, Metadata read.
- Install on **pantry-gitops only**.
- Store the three values from §4 in the prod Key Vault.

## 7. DNS
`dev.pantry.<domain>` and `staging.pantry.<domain>` → dev ingress IP. `pantry.<domain>` → prod ingress IP.
`kargo.<domain>` and `argocd.<domain>` → prod ingress IP. Put the chosen names into
`stages/<stage>/params/stage-params.yaml` (`host`, marked CHANGE ME).

## 8. GitHub repo settings (run once, as repo admin, in this order)
```bash
scripts/bootstrap_stage_branches.sh                  # creates stage/dev, stage/staging, stage/prod
KARGO_APP_ID=<id> PROD_APPROVERS_TEAM_ID=<id> scripts/github_rulesets.sh
```

## 9. Bootstrap the hub
```bash
kubectl --context <prod> apply -f hub/bootstrap/root-app.yaml
kubectl --context <prod> -n argocd get applications
# pantry-hub-root        Synced  Healthy
# pantry-kargo           Synced  Healthy      (Kargo project, warehouse, stages, rbac, creds)
# pantry-infra-staging   Unknown/ComparisonError until the first promotion creates content on stage/staging
# pantry-apps-staging    〃
# pantry-infra-prod      〃
# pantry-apps-prod       〃
kubectl --context <prod> -n pantry get warehouse,stages,freight
```

## 10. Notifications (KARGO-22)
Every stage Application subscribes to `on-deployed`, `on-sync-failed` and `on-health-degraded` on the
`teams` service, recipient `pantry-delivery` (`hub/argocd/stages-appset.yaml`). On the hub:
```yaml
# argocd-notifications-cm
data:
  service.teams: |
    recipientUrls:
      pantry-delivery: $teams-pantry-delivery-url
  # triggers + templates: install the Argo CD notifications catalog
  # (provides on-deployed / on-sync-failed / on-health-degraded)
# argocd-notifications-secret
stringData:
  teams-pantry-delivery-url: <Teams incoming-webhook URL, from Key Vault via ESO>
```
If your tenant has retired Office 365 connectors, point a `webhook` service at a Teams Workflow URL instead,
and change the service name in the three annotations (one file).
*Gap:* Kargo-level failures that happen before any sync (git push rejected, PR closed, verification failed)
don't pass through Argo CD. To alert on those, forward Kubernetes Events from namespace `pantry` (Kargo
emits Promotion/Freight events) with an event exporter to the same channel. Confirm the event reason names
in the PoC.

## 11. Prod Postgres backups (KARGO-27)
`stages/prod/infra/db-ns/cluster-ha-backups.yaml` needs:

| Resource | Value |
|---|---|
| Storage account + container `pantry-prod-backups` | put the URL into `destinationPath` (CHANGE ME) |
| User-assigned identity `pantry-prod-cnpg-backup` | role **Storage Blob Data Contributor** on the container; put its **client id** into `azure.workload.identity/client-id` (CHANGE ME) |
| Federated credential on that identity | issuer = prod AKS OIDC issuer, subject `system:serviceaccount:pantry-db:pantry` (CNPG names the SA after the Cluster) |
| AKS | Workload Identity + OIDC issuer enabled |
| Blob lifecycle | retain ≥ the CNPG `retentionPolicy` (30d); enable soft delete |

**And, critically, on the hub's `argocd-cm`:**
```yaml
data:
  resource.customizations.health.postgresql.cnpg.io_Backup: |
    <contents of hub/platform/argocd-health-cnpg-backup.lua>
```
Without it, Argo CD can't tell when a `Backup` has finished. The pre-promotion backup hook would then pass
instantly and **not** gate the prod migration. The Lua is unit-tested (`scripts/test_platform_lua.py`).
Check that Argo CD also reports `postgresql.cnpg.io/Cluster` health (recent Argo CD versions include this
built in). If yours doesn't, prod's first infra sync won't wait for Postgres to be ready.

## 12. Verification jobs (M4)
- The Jobs run on the **hub** in namespace `pantry` and need outbound HTTPS to the three stage hosts, plus
  Sigstore (`fulcio.sigstore.dev`, `rekor.sigstore.dev`, `tuf-repo-cdn.sigstore.dev`) once provenance is
  enabled. Allow this in any egress policy or firewall.
- Their images are pinned by version tag (`curlimages/curl:8.11.1`, `python:3.12.8-alpine3.21`,
  `ghcr.io/sigstore/cosign/cosign:v2.4.1`). Resolve them to digests (`crane digest <image>`), and pin the
  `@sha256:` form in `hub/kargo/analysis-*.yaml`. The checker accepts either, but digests are the goal.

## Acceptance (what "M1 done" looks like)
- [ ] `kargo login` via Entra works. A `Pantry.Promoter` user sees the Promote button on dev/staging only.
- [ ] Warehouse `pantry` has Freight containing 3 image digests and a gitops commit.
- [ ] A promotion to **staging** renders to `stage/staging`, and Argo CD syncs `pantry-infra-staging` then
      `pantry-apps-staging` on the dev cluster. The migration Job runs, and the app answers on the staging host.
- [ ] A promotion to **prod** opens a PR on `stage/prod`. After merge by a prod approver, prod syncs.
- [ ] The legacy `pantry-root` on the dev cluster is still Synced/Healthy and untouched.
- [ ] M4: a staging promotion shows `pantry-smoke` + `pantry-api-contract` AnalysisRuns Successful. A
      deliberately broken build leaves the Freight unverified. A Teams message arrives for deploy and failure.
- [ ] M6: prod `Cluster` shows 3 instances and `ContinuousArchiving=True`. A prod promotion creates
      `Backup/pantry-pre-promotion`, and the apps sync only starts after it's `completed`.
