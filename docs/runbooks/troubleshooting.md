# Troubleshooting

Find your symptom, run the **Diagnose** commands, apply the **Fix**. Names and contexts are in
[reference.md](reference.md). Commands marked ⚑ are Kargo CLI (1.6 syntax; the UI does the same).

**Start here, almost every time:**
```bash
kubectl --context hub -n pantry get promotions --sort-by=.metadata.creationTimestamp | tail -5
kubectl --context hub -n pantry describe promotion <name>          # which step failed, and its message
kubectl --context hub -n argocd get applications                   # any stage not Synced/Healthy?
```
In the Kargo UI, a Promotion shows every step with status and error. The failing step tells you which
section below applies ([promotion.md §1](promotion.md#1-anatomy-of-a-promotion) maps steps to sections).

| Symptom | Go to |
|---|---|
| Built an image or merged to `main`, no new Freight | [T1](#t1-no-new-freight-after-a-build-or-merge) |
| New Freight, but dev didn't get it | [T2](#t2-dev-did-not-auto-promote-new-freight) |
| Stage Unknown/Unhealthy with nothing running | [T3](#t3-stage-or-application-unknown--unhealthy-without-a-promotion) |
| Promotion `Pending`, or the Freight isn't offered | [T4](#t4-promotion-stays-pending--freight-not-offered) |
| Fails at a git step | [T5](#t5-promotion-fails-at-git-clone--git-push--git-open-pr) |
| Fails at kustomize-build | [T6](#t6-promotion-fails-at-kustomize-build) |
| Fails at argocd-update (infra) | [T7](#t7-promotion-fails-at-argocd-update-infra) |
| Prod backup hook failed | [T8](#t8-prod-pre-promotion-backup-fails) |
| Fails at argocd-update (apps) / migration | [T9](#t9-promotion-fails-at-argocd-update-apps) |
| Verification failed | [T10](#t10-verification-fails) |
| Prod PR problems | [T11](#t11-prod-pr-problems) |
| OutOfSync / drift | [T12](#t12-application-outofsync-or-drifting) |
| Secret not Ready / rotation not picked up | [T13](#t13-externalsecret-not-ready-or-rotation-not-picked-up) |
| TLS / certificate | [T14](#t14-tls-certificate-problems) |
| Kargo or hub Argo CD unhealthy | [T15](#t15-kargo-or-the-hub-argo-cd-is-unhealthy) |
| Promotion `Running` forever | [T16](#t16-promotion-stuck-running) |
| CI failing on a PR | [T17](#t17-ci-fails-on-a-pr) |

---

## T1. No new Freight after a build or merge
**Diagnose**
```bash
kubectl --context hub -n pantry describe warehouse pantry         # conditions + last discovery error
kubectl --context hub -n pantry get freight --sort-by=.metadata.creationTimestamp | tail -3
```
| Cause | Fix |
|---|---|
| Just happened; the poll interval is 5 min | Wait, or refresh now: `kargo refresh warehouse pantry` ⚑ [Oncall] |
| Image tag doesn't match `allowTags: ^dev-[0-9a-f]{7}$` (e.g. CI pushed a full 40-char SHA) | Fix the tag in the app repo's CI, or PR the regex in `hub/kargo/warehouse.yaml` |
| `main` change was outside `base/` or `stages/` | Expected: `hub/`, `docs/`, `scripts/` never create Freight |
| GHCR rate limit / auth error in the Warehouse condition | Wait it out. If the packages went private, add an image credential (ADR 0003) |
| Kargo controller unhealthy | [T15](#t15-kargo-or-the-hub-argo-cd-is-unhealthy) |

## T2. Dev did not auto-promote new Freight
| Cause | Check | Fix |
|---|---|---|
| Before the cutover, dev isn't adopted | `autoPromotionEnabled: false` in `hub/kargo/project.yaml` | Expected ([promotion.md §8](promotion.md#8-before-the-dev-cutover-interim)) |
| Auto-promotion paused on purpose | same file | Revert the pause PR when appropriate |
| Dev cluster stopped: the auto-promotion ran and failed | the latest dev Promotion `Errored` at `argocd-update` | `az aks start …`, then promote the newest Freight to dev manually |
| A dev Promotion is still running | `kubectl --context hub -n pantry get promotions` | Wait, or see [T16](#t16-promotion-stuck-running) |

## T3. Stage or Application Unknown / Unhealthy without a promotion
```bash
kubectl --context hub -n argocd get application pantry-apps-<stage> -o jsonpath='{.status.health.status} {.status.sync.status} {.status.conditions}{"\n"}'
argocd app get pantry-apps-<stage>
```
| Cause | Fix |
|---|---|
| dev/staging show `Unknown`: the **dev cluster is stopped** | Expected. `az aks start`; Argo CD reconnects and self-heals within minutes |
| `ComparisonError: … path does not exist` | The stage branch has no render yet (new stage, or just bootstrapped). Promote once |
| A pod is crashlooping (no deploy happened) | App problem: `kubectl --context <ctx> -n <app ns> describe pod …`, logs. Escalate to the app owner. If the last promotion is the cause, see [rollback.md](rollback.md) |
| CNPG Cluster degraded | `kubectl cnpg status pantry -n <db ns> --context <ctx>`; the platform/DB owner |

## T4. Promotion stays Pending / Freight not offered
| Cause | Check | Fix |
|---|---|---|
| Another Promotion for the same Stage is running | `kubectl --context hub -n pantry get promotions` | Wait; Promotions are serialised per Stage. If that one is stuck, see [T16](#t16-promotion-stuck-running) |
| Freight not verified in the upstream stage | Kargo UI: Freight → verified-in badges | Promote or re-verify upstream first |
| Prod: soak not reached (< 1 h in staging) | Kargo UI: when it became verified in staging | Wait. Emergencies: [promotion.md §7](promotion.md#7-expediting-a-hotfix) |
| Staging after the cutover: Freight only exists in the Warehouse | by design, staging needs dev-verified Freight | Promote to dev first (auto) |
| You lack the role | UI shows no Promote button / 403 | PIM-activate `Pantry.ProdApprover`; `Pantry.Promoter` can't do prod |

## T5. Promotion fails at git-clone / git-push / git-open-pr
**Diagnose:** the step's error in the Promotion. Then:
```bash
kubectl --context hub -n pantry get externalsecret kargo-git-pantry-gitops          # must be Ready/SecretSynced
kubectl --context hub -n pantry get secret kargo-git-pantry-gitops --show-labels    # label kargo.akuity.io/cred-type=git
```
| Error says | Cause | Fix |
|---|---|---|
| authentication / 401 / 403 on clone | GitHub App key rotated or revoked; ExternalSecret stale | Check the Key Vault entries `kargo-github-app-*`, force-sync the ExternalSecret ([routine-tasks.md §7](routine-tasks.md#7-rotate-credentials)) |
| push **rejected** by a rule | Ruleset doesn't allow the Kargo App (App re-created → new id) | Re-run `scripts/github_rulesets.sh` with the new `KARGO_APP_ID` (repo admin) |
| non-fast-forward on `stage/<stage>` | Someone pushed to the stage branch (break-glass?) | Find out who. Re-running the Promotion renders on top of the new HEAD |
| cannot open PR / 403 on pulls | App lacks *Pull requests: write* | Fix the App permissions in GitHub |
| PR: "no history in common" | `stage/prod` was deleted/recreated as an orphan | Re-bootstrap (`scripts/bootstrap_stage_branches.sh`). Ask a repo admin |

## T6. Promotion fails at kustomize-build
Means the `main` commit in that Freight doesn't render for this stage. CI should have blocked it.
```bash
git checkout <freight-gitops-commit> && scripts/render_stages.sh /tmp/r
```
**Fix:** fix forward on `main` (new Freight). Then find out how it got past CI: a skipped check or a
`stages/<stage>`-only path? Add a test.

## T7. Promotion fails at argocd-update (infra)
```bash
argocd app get pantry-infra-<stage> --show-operation
kubectl --context <ctx> -n <app ns> get externalsecret
kubectl --context <ctx> -n <db ns> get externalsecret,cluster
```
| Error / state | Cause | Fix |
|---|---|---|
| Kargo: not authorised to update Application | `kargo.akuity.io/authorized-stage` missing/changed | It's generated by `hub/argocd/stages-appset.yaml`. Restore it via PR; never hand-edit |
| Application not found | Stage not adopted (dev before cutover), or the ApplicationSet element is missing | Expected pre-cutover; otherwise check the ApplicationSet |
| ExternalSecret `SecretSyncedError` | Key Vault entry missing/renamed for this stage | Create it (the stage's `kvDbPasswordKey` / `kvMcpTokensKey`). `pantry-mcp-credentials` Degraded blocks the sync |
| CNPG `Cluster` not healthy | Postgres problem, storage full, operator issue | `kubectl cnpg status pantry -n <db ns> --context <ctx>`; the DB owner |
| timed out waiting for revision | Argo CD hasn't fetched the new commit | `argocd app get pantry-infra-<stage> --hard-refresh`, then re-run the Promotion |
| `Unknown` / cluster unreachable | dev cluster stopped | Start it and re-run |

## T8. Prod pre-promotion backup fails
The infra sync waits for `Backup/pantry-pre-promotion` to be `completed`. Failure stops the promotion
**before the migration**. That's the gate working.
```bash
kubectl --context hub -n pantry-db describe backup pantry-pre-promotion        # status.phase, status.error
kubectl cnpg status pantry -n pantry-db --context hub                           # continuous archiving state
kubectl --context hub -n pantry-db logs pantry-1 -c postgres --since=30m | grep -i barman
```
| Cause | Fix |
|---|---|
| Workload Identity: federated credential / client id wrong | Check `azure.workload.identity/client-id` in `stages/prod/infra/db-ns/cluster-ha-backups.yaml` against pantry-infra (handoff §11) |
| Storage: container missing, role missing, network | pantry-infra |
| `walArchivingFailing` | WAL archiving broken: the same causes as above. The backup can't be trusted until fixed |
| Hook passes instantly and never waits | The Argo CD health check for `Backup` isn't installed: **the gate isn't working**. pantry-infra must install `hub/platform/argocd-health-cnpg-backup.lua` |

**Don't** remove the hook to get a promotion through. If prod is down and you need a rollback while
backups are broken, the incident commander decides: use [BG-1](break-glass-and-dr.md#bg-1-emergency-prod-rollback-faster-than-a-pr).

## T9. Promotion fails at argocd-update (apps)
```bash
argocd app get pantry-apps-<stage> --show-operation
kubectl --context <ctx> -n <app ns> get job pantry-db-migrate
kubectl --context <ctx> -n <app ns> logs job/pantry-db-migrate
kubectl --context <ctx> -n <app ns> get pods; kubectl --context <ctx> -n <app ns> describe pod <pod>
```
| Symptom | Cause | Fix |
|---|---|---|
| Migration Job failed | SQL error, incompatible migration, DB not reachable | Read the logs. The stage is still running the **old** code (the migration gates the rollout), so it's not urgent. Fix forward in pantry-db → new Freight. **Check whether the migration half-applied** (the DB owner) |
| Migration `CreateContainerConfigError` | `pantry-app-credentials` Secret missing (ESO) | [T13](#t13-externalsecret-not-ready-or-rotation-not-picked-up) |
| `ImagePullBackOff` | Digest not in GHCR (deleted package version?) or packages went private | Restore the image, or roll back to Freight whose images exist |
| Rollout never becomes ready | New code fails readiness (`/health`, `/healthz`) | Logs. Roll back the stage ([rollback.md](rollback.md)) |
| Sync failed: resource not permitted in project | New kind not in the stage's AppProject whitelist | PR `hub/argocd/projects.yaml`. `check_gitops.py` should have caught it |

## T10. Verification fails
Verification runs as Jobs on the **hub**, namespace `pantry`, against the stage's **public host**.
```bash
kubectl --context hub -n pantry get analysisruns --sort-by=.metadata.creationTimestamp | tail -3
kubectl --context hub -n pantry describe analysisrun <name>
kubectl --context hub -n pantry get jobs --sort-by=.metadata.creationTimestamp | tail -3
kubectl --context hub -n pantry logs job/<job-name>                 # the check's own output
```
| Job output | Meaning | Fix |
|---|---|---|
| `503 /pantry/api/health` (or other 5xx) | API not healthy behind the ingress | Stage is really broken: see T9, or roll back |
| `404 /pantry/…` | Ingress path/host wrong | Check the Ingress hosts vs `stages/<stage>/params` `host`, and DNS |
| curl exit 6 / "Could not resolve host" | DNS record missing | pantry-infra (DNS); check the `host` param |
| curl exit 60 / SSL certificate problem | Cert not issued yet, or wrong host on the cert | [T14](#t14-tls-certificate-problems). Re-verify once the cert is Ready |
| curl exit 7/28, timeouts | dev cluster stopped, or egress from the hub blocked | Start dev; check hub egress (handoff §12) |
| `FAIL openapi declares …` | API contract changed (route renamed/removed) | Intentional? Update `pantry-api-contract` in a PR **first**. If not, it's a real regression: roll back |
| `FAIL /recipes -> 200 JSON` | Recipes endpoint broken | Real regression: roll back staging; don't promote to prod |

After fixing an environmental cause (cert, DNS, cluster started), **re-verify** rather than re-promote:
`kargo verify stage <stage>` ⚑ [Oncall], or Kargo UI → Stage → *Reverify*.

## T11. Prod PR problems
| Problem | Fix |
|---|---|
| Promotion waiting but no PR visible | Promotion step details → `git-open-pr` error ([T5](#t5-promotion-fails-at-git-clone--git-push--git-open-pr)) |
| Can't merge: "review required" | Someone other than the author (the Kargo App) must approve: any reviewer |
| Can't merge: "not allowed to update" | Only GitHub team `pantry-prod-approvers` may merge into `stage/prod` |
| PR has conflicts | `stage/prod` moved (break-glass?). Close the PR (the Promotion fails) and promote again |
| Closed by mistake | The Promotion has failed. Promote the same Freight again |
| Wrong Freight in the PR | Close it; the Promotion fails; prod is unchanged |

## T12. Application OutOfSync or drifting
```bash
argocd app diff pantry-apps-<stage>
```
| Cause | Fix |
|---|---|
| Someone ran `kubectl edit/scale` on a workload | selfHeal reverts it within minutes. Find who and why; if the change was needed, make it via PR |
| A controller mutates a field (e.g. HPA on replicas) | Add an `ignoreDifferences` for that field to the ApplicationSet via PR |
| Stuck OutOfSync after a promotion | `argocd app get pantry-apps-<stage> --hard-refresh`; check the sync operation message |

## T13. ExternalSecret not Ready, or rotation not picked up
```bash
kubectl --context <ctx> -n <ns> get externalsecret
kubectl --context <ctx> -n <ns> describe externalsecret <name>         # event: key not found / auth
```
- Key not found: create the Key Vault entry named in the stage's params (Key Vault per cluster:
  dev and staging share the dev vault).
- Rotated but still old: force the sync
  (`kubectl … annotate externalsecret <name> force-sync=$(date +%s) --overwrite`), then restart the
  consumer. `MCP_AUTH_TOKENS` and the DB password are read at pod start:
  `kubectl --context <ctx> -n <app ns> rollout restart deploy/pantry-api`.

## T14. TLS certificate problems
```bash
kubectl --context <ctx> -n <app ns> get certificate,certificaterequest,order,challenge
kubectl --context <ctx> -n <app ns> describe certificate pantry-tls
```
- ACME HTTP-01 challenge pending: DNS doesn't point at this cluster's ingress yet, or HTTP (port 80) is
  blocked. The frontend Ingress keeps `ssl-redirect: "false"` for this reason.
- Wrong hostname on the cert: the `host` param changed, and cert-manager reissues automatically. Verify again.

## T15. Kargo or the hub Argo CD is unhealthy
```bash
kubectl --context hub -n kargo get pods
kubectl --context hub -n kargo logs deploy/kargo-controller --since=30m | tail -50
kubectl --context hub -n kargo logs deploy/kargo-api --since=30m | tail -50
kubectl --context hub -n argocd get pods
```
- Workloads keep running in every case (see [break-glass-and-dr.md](break-glass-and-dr.md), "What keeps running").
- Kargo down: no promotions or verification. Escalate to the platform team. For urgent changes, use BG-2.
- Hub Argo CD down: no syncs or self-heal anywhere, and Kargo promotions fail. Platform team, high priority.

## T16. Promotion stuck Running
Normal maximums are in [reference.md §3](reference.md#3-timings-what-normal-looks-like). Beyond them:
1. Check which step it's on (UI or `describe promotion`).
2. `git-wait-for-pr` is waiting for a human: it's not stuck ([T11](#t11-prod-pr-problems)).
3. `argocd-update` waiting: look at the Application's operation state (T7/T9). Argo CD may be retrying.
4. If it will never finish: **abort**. `kargo abort promotion <name>` ⚑ [Oncall], or Kargo UI → Promotion
   → *Abort*. The stage keeps whatever was last synced. Check the Applications, and promote again once the
   cause is fixed.

## T17. CI fails on a PR
`scripts/check_gitops.py` prints `FAIL <check>` lines, each naming the rule and the ADR it protects:

| Check | Usual cause |
|---|---|
| `check_stage_params` | a namespace in a kustomization differs from the stage's params file; or an `images:` block added on `main` |
| `check_rendered` | DB_HOST / Key Vault key / host wiring broken, or a protection annotation removed |
| `check_isolation` | two stages on one cluster share a namespace or a Key Vault key |
| `check_argocd` | ApplicationSet/AppProject drift, a new resource kind not whitelisted, notifications missing |
| `check_kargo` | Warehouse/task/stage wiring (e.g. a new image missing from the tasks, `strictSemvers` missing) |
| `check_verification` | a Stage without verification, a host arg ≠ params host, unpinned job image, no soak |
| `check_prod_hardening` | prod lost HA, backups, the pre-promotion hook, or PDBs |
| `check_cutover_state` | cutover flags half-applied (use `scripts/cutover.py`) |
| `check_rbac` | a role gained prod promote or write verbs |
| `check_legacy_equivalence` | `base/` changed without mirroring the legacy `apps/`/`infra/` (pre-cutover), or vice versa |

`kubeconform` failures name the file, the field and the schema. Kargo fields are validated against the
exact Kargo chart version in `KARGO_VERSION`.
