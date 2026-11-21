# Reference: names, commands, timings

The page to keep open on call. Everything here is derived from the repo. `scripts/test_docs.py` fails CI
if a name or path on these pages drifts from it.

> ⚠️ **Same namespace names, different clusters.** `pantry-app` / `pantry-db` exist on **both** clusters.
> On `--context dev` they are **dev**; on `--context hub` they are **prod**. Always pass `--context`
> explicitly, and show the current context in your shell prompt.

## 1. Where everything lives

| Stage | Cluster → kube context | App ns | DB ns | Argo CD Applications | Branch | Host (from `stages/<stage>/params`) |
|---|---|---|---|---|---|---|
| dev | dev AKS → `dev` | `pantry-app` | `pantry-db` | `pantry-infra-dev`, `pantry-apps-dev` | `stage/dev` | `dev.pantry.example.com` |
| staging | dev AKS → `dev` | `pantry-staging-app` | `pantry-staging-db` | `pantry-infra-staging`, `pantry-apps-staging` | `stage/staging` | `staging.pantry.example.com` |
| prod | prod AKS → `hub` | `pantry-app` | `pantry-db` | `pantry-infra-prod`, `pantry-apps-prod` | `stage/prod` | `pantry.example.com` |

**Control plane ("hub" = the prod cluster, `--context hub`):**

| Thing | Name |
|---|---|
| Argo CD namespace / seed / pipeline app | `argocd` / `pantry-hub-root` / `pantry-kargo` |
| Argo CD AppProjects | `pantry-dev`, `pantry-staging`, `pantry-prod`, `pantry-delivery` |
| Kargo project = namespace | `pantry` |
| Warehouse | `pantry` (3 images + `main` at `base/`, `stages/`) |
| Stages | `dev` → `staging` → `prod` |
| PromotionTasks | `render-and-push` (dev, staging), `render-and-pr` (prod) |
| Verification (AnalysisTemplates) | `pantry-smoke` (all), `pantry-api-contract` (staging), `pantry-provenance` (ready, not enabled) |
| Kargo git credential | ExternalSecret `kargo-git-pantry-gitops` (ns `pantry`) |

**Per stage, inside its namespaces:**

| Object | Name | Notes |
|---|---|---|
| CNPG Postgres cluster | `pantry` (pods `pantry-1`, prod also `-2`, `-3`) | RW service `pantry-rw.<db ns>.svc` |
| Migration Job (PreSync hook) | `pantry-db-migrate` | recreated on every apps sync |
| Deployments | `pantry-api` (:8000), `pantry-frontend` (:80) | prod: 2 replicas + PDBs |
| Ingresses | `pantry-api` (`/pantry/api` → rewrite), `pantry-frontend` (`/pantry`, owns TLS `pantry-tls`) | |
| ExternalSecrets | `pantry-app-credentials` (app + db ns), `anthropic-credentials`, `pantry-mcp-credentials` | |
| Prod only | `ScheduledBackup/pantry-daily` (03:00), `Backup/pantry-pre-promotion` (every promotion) | |

## 2. Roles (Entra app roles → Kargo)

| Role | Can | Typical holder |
|---|---|---|
| `Pantry.Viewer` | see everything in Kargo | every engineer |
| `Pantry.Promoter` | promote to dev, staging | engineers after onboarding sign-off |
| `Pantry.ProdApprover` | promote to prod (PIM) | senior engineers, on-call rotation |
| `Pantry.Oncall` | refresh, re-verify, abort Promotion, Freight alias (PIM, per shift) | on-call rotation |
| `Kargo.Admin` | everything in Kargo (break-glass) | platform admins |
| GitHub `pantry-prod-approvers` team | merge PRs into `stage/prod` | = ProdApprover holders |
| GitHub repo admin | bypass rulesets (break-glass only) | platform admins |

## 3. Timings (what "normal" looks like)

| Step | Typical | Investigate after |
|---|---|---|
| New image or `main` commit → Freight | ≤ 5 min (Warehouse poll) | 15 min |
| Render + push (`git-clone` → `git-push`) | < 1 min | 5 min |
| Infra sync (dev/staging) | 1–2 min | 10 min |
| Infra sync (prod, incl. pre-promotion backup) | 2–10 min (depends on DB size) | 20 min |
| Apps sync (migration + rollout) | 2–5 min | 15 min |
| Verification | 1–3 min (Job deadline 5 min) | 6 min → it has failed |
| Soak before prod | 1 h in staging | n/a |
| ExternalSecret refresh | ≤ 1 h (force: annotate) | n/a |
| Argo CD sync retry backoff | 10 s → 3 min, 5 tries | n/a |

## 4. Command cheat-sheet

> Every Kargo action is also in the **Kargo UI** (Project `pantry`), which is the reference. CLI lines
> marked ⚑ follow Kargo 1.6 syntax. Confirm with `kargo <cmd> --help` the first time you use one (PoC row 19).

### Log in / set up
```bash
kargo login https://kargo.example.com --sso                # ⚑ Entra sign-in
kargo config set-project pantry                             # ⚑ default project for later commands
argocd login argocd.example.com --sso
az aks get-credentials -g <rg> -n <dev-aks>  --context dev && kubelogin convert-kubeconfig -l azurecli
az aks get-credentials -g <rg> -n <prod-aks> --context hub && kubelogin convert-kubeconfig -l azurecli
```

### Look (read-only, safe any time)
```bash
# Kargo objects (they're CRDs on the hub)
kubectl --context hub -n pantry get warehouses,stages
kubectl --context hub -n pantry get freight --sort-by=.metadata.creationTimestamp
kubectl --context hub -n pantry get promotions --sort-by=.metadata.creationTimestamp
kubectl --context hub -n pantry describe stage prod                 # current Freight, health, conditions
kubectl --context hub -n pantry get analysisruns --sort-by=.metadata.creationTimestamp
kargo get stages                                                    # ⚑
kargo get freight                                                   # ⚑
kargo get promotions --stage prod                                   # ⚑

# Argo CD
kubectl --context hub -n argocd get applications
argocd app get pantry-apps-prod
argocd app history pantry-apps-prod
argocd app diff pantry-apps-prod

# What runs (git is the record)
git fetch origin 'refs/heads/stage/*:refs/remotes/origin/stage/*'
git log --format='%h %ad %s' --date=iso -10 origin/stage/prod     # one commit per promotion
git show origin/stage/prod --stat
git show origin/stage/prod:apps/manifests.yaml | grep 'image:'

# Workloads and data
kubectl --context hub -n pantry-app get deploy,pods,ingress,certificate,externalsecret
kubectl --context hub -n pantry-app logs deploy/pantry-api --since=15m
kubectl --context hub -n pantry-db get cluster,backups,scheduledbackups,externalsecret
kubectl cnpg status pantry -n pantry-db --context hub              # needs the cnpg kubectl plugin
```

### Act (role needed in brackets)
```bash
kargo promote --stage staging --freight <freight-name>             # ⚑ [Promoter]
kargo promote --stage prod --freight-alias <alias>                 # ⚑ [ProdApprover]
kargo refresh warehouse pantry                                     # ⚑ [Oncall]  discover now, don't wait 5 min
kargo verify stage staging                                         # ⚑ [Oncall]  re-run verification
kargo abort promotion <promotion-name>                             # ⚑ [Oncall]  stop a stuck Promotion
gh pr list  -R pjvjay/pantry-gitops --base stage/prod               # prod promotions waiting for review
gh pr diff  -R pjvjay/pantry-gitops <n>
gh pr review -R pjvjay/pantry-gitops <n> --approve
gh pr merge  -R pjvjay/pantry-gitops <n> --merge                    # [pantry-prod-approvers]
kubectl --context <ctx> -n <ns> annotate externalsecret <name> force-sync="$(date +%s)" --overwrite
argocd app get pantry-apps-<stage> --hard-refresh                   # re-read git now
```

## 5. Annotations and labels you will see

| Annotation | Where | Meaning |
|---|---|---|
| `kargo.akuity.io/authorized-stage: pantry:<stage>` | stage Applications | the only Kargo Stage allowed to sync it. **Never remove** |
| `argocd.argoproj.io/sync-options: Prune=false,Delete=false` | Namespaces, CNPG `Cluster`, Kargo `Project` | Argo CD will never delete these. **Never remove** |
| `argocd.argoproj.io/sync-wave` | infra resources | order: -15 ns → -10 secrets → -5 Cluster → -4 prod backup hook → 0 workloads |
| `argocd.argoproj.io/hook: PreSync` | `pantry-db-migrate` | migration runs before workloads roll |
| `argocd.argoproj.io/hook: Sync` | prod `Backup/pantry-pre-promotion` | backup must complete before the apps sync |
| `notifications.argoproj.io/subscribe.*` | stage Applications | Teams alerts (deployed / sync failed / degraded) |
| `kargo.akuity.io/cred-type: git` | Kargo credential Secret | how Kargo finds its GitHub App credential |

## 6. Glossary

| Term | Meaning here |
|---|---|
| **Freight** | One release candidate: the digests of api, frontend and db-migrate, plus one `main` commit. Has a generated name (a hash) and a human **alias** |
| **Warehouse** | Watches GHCR and `main`; creates Freight |
| **Stage** | A promotion target (`dev`, `staging`, `prod`) = two Argo CD Applications + one branch |
| **Promotion** | One run of a PromotionTask that puts a Freight into a Stage |
| **Verified in X** | Freight was promoted to X, X became Healthy, and X's AnalysisTemplates passed |
| **Soak** | Minimum time Freight must have spent in staging before prod will accept it (1 h) |
| **Render** | `kustomize build` of `stages/<stage>` at the Freight's commit, with digests pinned |
| **Hub** | The prod cluster, which also runs Argo CD + Kargo for all stages |
| **Legacy** | `argocd/`, `apps/`, `infra/`: the pre-Kargo loop, live on dev until the cutover |
| **Break-glass** | Bypassing Kargo or rulesets in an emergency ([break-glass-and-dr.md](break-glass-and-dr.md)) |
