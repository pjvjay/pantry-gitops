# Runbook: break-glass, disaster recovery and game day (KARGO-29)

Break-glass bypasses the pipeline's gates. Use it only when the normal path (promote previous Freight) is
unavailable or too slow for the incident. **Every use gets an incident ticket and a follow-up promotion
that brings Kargo back in line.**

## What keeps running when something is down
| Down | Effect | Workloads |
|---|---|---|
| Kargo | No promotions or verification. Argo CD keeps syncing and self-healing every stage | unaffected |
| Hub Argo CD | No sync or self-heal on any stage. Kargo promotions fail at `argocd-update` | keep running as-is |
| Dev cluster (stopped) | dev/staging Applications `Unknown`; their promotions/verifications fail | prod unaffected |
| GitHub | No promotions (Kargo can't push), no Argo CD refresh | keep running |

## BG-1: emergency prod rollback, faster than a PR
For when the prod PR/review loop takes too long during an incident. Needs a repo admin
(ruleset bypass) **and** a second person on the call.
```bash
git fetch origin stage/prod
git log --oneline -5 origin/stage/prod            # each commit = one promotion (Freight id in message)
git switch -c bg/prod-revert origin/stage/prod
git revert --no-edit HEAD                         # back to the previous promotion's rendered state
git push origin HEAD:stage/prod                   # admin bypass of the stage/prod rulesets
kubectl --context hub -n argocd annotate application pantry-apps-prod argocd.argoproj.io/refresh=hard --overwrite
```
Argo CD syncs the previous manifests. This **does not** restore the schema (see DR-2).
**Afterwards:** promote that previous Freight to prod through Kargo. The PR will be empty, so just merge
it. This puts Kargo's view of prod back in line with the branch.

## BG-2: Kargo is down and a stage needs a change
Same as BG-1 on `stage/<stage>`. For dev/staging the Kargo GitHub App ruleset is bypassed by admins.
Never edit `main` to fix a stage: `main` changes become Freight and have to travel the pipeline.

## BG-3: a bad promotion is stuck waiting on a PR
Close the PR on `stage/prod`. The Promotion fails, and prod keeps what it had.

## DR-1: hub cluster lost
1. pantry-infra recreates the prod cluster and the hub (handoff §1–3), then applies
   `hub/bootstrap/root-app.yaml`.
2. The Kargo pipeline comes back from `hub/kargo/`. **Freight history does not.** Freight and Promotion
   objects lived in the cluster. The audit trail survives in the `stage/*` branch histories.
3. Argo CD re-syncs each stage from its `stage/<stage>` branch. What ran is what runs.
4. The Warehouse re-discovers Freight. Promote the Freight matching each stage's current images to
   re-establish history. The renders are identical, so nothing rolls.
5. **Prod data:** the prod Postgres lives on the lost cluster, so restore it as in DR-2.

## DR-2: restore prod data (schema or data corruption)
Prod archives WAL continuously and takes a daily base backup plus one before every promotion, to Azure
Blob (`stages/prod/infra/db-ns/cluster-ha-backups.yaml`). Restore goes into a **new** Cluster (CNPG
recovery) and the app is switched over:
```yaml
# stages/prod/infra/db-ns/ … recovery Cluster (PR, reviewed; promoted like any change)
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: pantry-restore
spec:
  instances: 3
  bootstrap:
    recovery:
      source: pantry-backup
      recoveryTarget:
        targetTime: "2025-01-31 09:59:00+00"      # just before the bad migration/promotion
  externalClusters:
    - name: pantry-backup
      barmanObjectStore:
        destinationPath: https://CHANGEME.blob.core.windows.net/pantry-prod-backups
        serverName: pantry
        azureCredentials:
          inheritFromAzureAD: true
```
Then point `DB_HOST` at `pantry-restore-rw`, or rename once it's verified. **Practise this in staging
first** (the game day below), never for the first time in an incident.
Dev/staging have no object-store backups by design. Restore them from a `pg_dump` (cutover step 2 shows how).

## Game day (run quarterly; first time before prod go-live)
| # | Scenario | Pass criteria |
|---|---|---|
| 1 | Roll prod back one Freight via Kargo (PR path) | prod healthy on previous Freight in **< 10 min** |
| 2 | BG-1 emergency revert, then re-align Kargo | prod healthy on previous render < 5 min; Kargo shows the correct Freight afterwards |
| 3 | Promote Freight whose migration fails (staging) | Promotion fails, staging keeps serving, Freight not verified |
| 4 | Promote Freight that fails `pantry-api-contract` (staging) | Freight not verified in staging, not offered for prod |
| 5 | Close the prod PR | Promotion fails, prod unchanged |
| 6 | Scale Kargo to 0, then BG-2 on staging | Argo CD applies the revert; Kargo reconciles after scale-up |
| 7 | DR-2 PITR restore in **staging** (after enabling backups there for the exercise) | Restored cluster has data as of the target time; app works against it |
| 8 | Separation-of-duties audit: compare, for the last prod promotions, the Kargo promoter with the PR approver | All different. This is **policy, not enforced**: the PR author is the Kargo App, so GitHub can't tell that the approver is also the promoter (ADR 0007) |

Record the times. They are the epic's rollback metric (< 10 min).
