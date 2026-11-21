# Promotion: moving Freight through dev → staging → prod

## 1. Anatomy of a promotion

Every promotion runs one PromotionTask (`hub/kargo/promotion-tasks.yaml`). Knowing the steps tells you
where a failure happened; the Kargo UI shows each step's status inside the Promotion.

| # | Step (`uses`) | What happens | Fails typically because | Fix in |
|---|---|---|---|---|
| 1 | `git-clone` | clones `main` **at the Freight's commit** → `./src`, and `stage/<stage>` → `./out` | GitHub credential, GitHub outage | [T5](troubleshooting.md#t5-promotion-fails-at-git-clone--git-push--git-open-pr) |
| 2 | `git-clear` | empties `./out` (renders are always complete, never patched) | (rare) | n/a |
| 3 | `kustomize-set-image` | pins the Freight's 3 image **digests** in the throwaway `./src` | image missing from Freight | [T4](troubleshooting.md#t4-promotion-stays-pending--freight-not-offered) |
| 4–5 | `kustomize-build` ×2 | renders `stages/<stage>/infra` and `/apps` → `./out/{infra,apps}/manifests.yaml` | a broken overlay at that commit (CI should have caught it) | [T6](troubleshooting.md#t6-promotion-fails-at-kustomize-build) |
| 6 | `git-commit` | "promote `<freight>` to `<stage>`" + gitops commit + image tags | nothing changed (re-promoting the same Freight) | n/a |
| 7 | `git-push` | dev/staging: pushes to `stage/<stage>`. **prod:** pushes a generated `kargo/…` branch | ruleset rejects the push, credential | [T5](troubleshooting.md#t5-promotion-fails-at-git-clone--git-push--git-open-pr) |
| 7a | `git-open-pr` (prod) | opens a PR `kargo/…` → `stage/prod` | credential lacks PR permission | [T5](troubleshooting.md#t5-promotion-fails-at-git-clone--git-push--git-open-pr) |
| 7b | `git-wait-for-pr` (prod) | **waits for a human merge**. Closed unmerged = Promotion fails | nobody merged it | §4 |
| 8 | `argocd-update` infra | syncs `pantry-infra-<stage>` to exactly the new commit and waits. **prod:** includes the pre-promotion backup | ESO secret missing, CNPG unhealthy, backup failed | [T7](troubleshooting.md#t7-promotion-fails-at-argocd-update-infra), [T8](troubleshooting.md#t8-prod-pre-promotion-backup-fails) |
| 9 | `argocd-update` apps | syncs `pantry-apps-<stage>`: **PreSync migration** then workloads | migration failed, image pull, readiness | [T9](troubleshooting.md#t9-promotion-fails-at-argocd-update-apps) |
| — | verification | after the stage is Healthy: AnalysisTemplates run (Jobs on the hub) | the app is actually broken, TLS/DNS | [T10](troubleshooting.md#t10-verification-fails) |

A failure at any step fails the Promotion, and **the stage keeps running what it had before.** Only
one Promotion runs per Stage at a time; later ones queue as `Pending`.

## 2. Which Freight can go where

| Target | Offered Freight | Who may promote | Automatic? |
|---|---|---|---|
| dev | any Freight from the Warehouse | `Pantry.Promoter` | **yes**, newest Freight (after cutover) |
| staging | Freight **verified in dev** *(interim, before the cutover: also straight from the Warehouse)* | `Pantry.Promoter` | no |
| prod | Freight **verified in staging and soaked ≥ 1 h there** | `Pantry.ProdApprover` **+ a merged PR** | no |

If the Freight you want isn't offered, it hasn't met these conditions ([T4](troubleshooting.md#t4-promotion-stays-pending--freight-not-offered)).

---

## 3. Promote to staging

**Who:** `Pantry.Promoter`. **Takes:** ~5–10 min. **Dev cluster must be running.**

**Before:**
- [ ] The Freight is **verified in dev** (green in the Kargo UI).
- [ ] You know what's in it. In the UI, Freight → details shows the image tags and commit. Compare with
      what staging runs now:
  ```bash
  git fetch origin main 'refs/heads/stage/*:refs/remotes/origin/stage/*'
  git log -1 --format=%B origin/stage/staging                 # current: freight, gitops commit, tags
  git log --oneline <staging-gitops-commit>..<freight-gitops-commit> -- base stages   # config changes coming
  ```
- [ ] Nobody else is mid-promotion or testing on staging (say so in the team channel).
- [ ] If `dev` is stopped: `az aks start -g <rg> -n <dev-aks>` and wait until `kubectl --context dev get nodes` is Ready.

**Do (UI):** Kargo → project `pantry` → stage **staging** → *Promote* → pick the Freight → confirm.
**Do (CLI):** `kargo promote --stage staging --freight <name>` ⚑

**Watch:**
```bash
kubectl --context hub -n pantry get promotions --sort-by=.metadata.creationTimestamp | tail -3
kubectl --context hub -n argocd get application pantry-infra-staging pantry-apps-staging -w
kubectl --context dev -n pantry-staging-app get pods -w            # migration Job, then rollout
```

**Done when:**
- [ ] Promotion `Succeeded`.
- [ ] Both Applications `Synced` / `Healthy` at the new `stage/staging` commit.
- [ ] Verification: `pantry-smoke` and `pantry-api-contract` AnalysisRuns `Successful`, and the Freight is
      shown as verified in staging.
- [ ] Quick manual look: `https://staging.pantry.example.com/pantry/` loads.

The **1 h soak clock** for prod starts now.

---

## 4. Promote to prod

**Who:** a `Pantry.ProdApprover` starts it. A reviewer approves the PR (by policy, not the person who
started it; ADR 0007), and a member of GitHub `pantry-prod-approvers` merges it. **Takes:** ~15–30 min
plus review time. **When:** working hours, not Friday afternoon, no open prod incident, unless this *is*
the incident fix ([rollback.md](rollback.md)).

### 4.1 Pre-flight checklist
- [ ] The Freight is **verified in staging** and has soaked ≥ 1 h. Kargo only offers it once both are true.
- [ ] **What's changing**, written in one line for the announcement:
  ```bash
  git log -1 --format=%B origin/stage/prod                           # what prod runs now
  git log --oneline <prod-gitops-commit>..<freight-gitops-commit> -- base stages
  ```
  For each image tag that differs, skim the app-repo commits between the two `dev-<sha>` tags.
- [ ] **Migrations?** If the `pantry-db-migrate` tag changed, open the pantry-db PRs and confirm they are
      expand/contract (`docs/handoffs/pantry-db.md`). A contract step (drop/rename) needs extra care: is
      any Freight still in staging or prod reading the old column?
- [ ] **Backups healthy:**
  ```bash
  kubectl --context hub -n pantry-db get scheduledbackup pantry-daily
  kubectl --context hub -n pantry-db get backups --sort-by=.metadata.creationTimestamp | tail -3   # last ones "completed"
  kubectl cnpg status pantry -n pantry-db --context hub | grep -i -A2 "continuous backup"          # archiving OK
  ```
- [ ] Prod is healthy right now (`kubectl --context hub -n argocd get application pantry-infra-prod pantry-apps-prod`).
- [ ] Announce in the team channel: *"Promoting `<alias>` to prod: `<one line>`. Migration: yes/no."*

### 4.2 Start the promotion
PIM-activate `Pantry.ProdApprover`, then:
**UI:** stage **prod** → *Promote* → Freight → confirm. **CLI:** `kargo promote --stage prod --freight <name>` ⚑

Within a minute the Promotion reaches `git-wait-for-pr` and a PR `kargo/… → stage/prod` exists
(`gh pr list -R pjvjay/pantry-gitops --base stage/prod`). The PR link is also in the Promotion's step details.

### 4.3 Review the PR: what to look for
The diff is the **fully rendered** YAML that will run in prod. Review it like production config, because it is.
```bash
gh pr diff -R pjvjay/pantry-gitops <n> | less
```
- [ ] **Images:** exactly the three digests of the Freight you meant (compare with the Freight details in the UI).
- [ ] **infra/manifests.yaml:** expect *no* change unless the Freight carries an infra change. Stop and ask
      about any change to the CNPG `Cluster` (instances, storage, `backup`, `imageName`), Namespaces or
      ExternalSecrets (Key Vault keys!).
- [ ] **Protection intact:** Namespaces and `Cluster` still have `Prune=false,Delete=false`. The backup
      hook and PDBs are still present.
- [ ] **Nothing surprising:** no resource removed that you didn't expect, no host change, no replica drop.
- [ ] Then **approve**: `gh pr review -R pjvjay/pantry-gitops <n> --approve`. It must be someone other
      than the promoter (policy).

### 4.4 Merge, then watch
A `pantry-prod-approvers` member merges (`gh pr merge -R pjvjay/pantry-gitops <n> --merge`). Kargo resumes:

| Phase | Watch | Normal |
|---|---|---|
| Infra sync + **pre-promotion backup** | `kubectl --context hub -n pantry-db get backup pantry-pre-promotion -w` | `completed` in 2–10 min |
| Apps sync: **migration** | `kubectl --context hub -n pantry-app logs job/pantry-db-migrate -f` | exits 0 |
| Apps sync: rollout | `kubectl --context hub -n pantry-app rollout status deploy/pantry-api` (then `deploy/pantry-frontend`) | 2/2 ready, no restarts |
| Verification | `kubectl --context hub -n pantry get analysisruns --sort-by=.metadata.creationTimestamp \| tail -2` | `Successful` |

### 4.5 After
- [ ] Promotion `Succeeded`; Freight shown in prod.
- [ ] Watch for 30 min: `kubectl --context hub -n pantry-app logs deploy/pantry-api --since=30m | grep -iE "error|exception" | tail`,
      plus pod restarts and any Teams alerts.
- [ ] Post the result in the team channel (*"`<alias>` live in prod, verified"*).
- [ ] If anything looks wrong: **roll back first, investigate second** ([rollback.md](rollback.md)).

---

## 5. Dev (automatic)

After the cutover, every new Freight is auto-promoted to dev. You normally do nothing. To check:
```bash
kubectl --context hub -n pantry get promotions --sort-by=.metadata.creationTimestamp | grep -i dev | tail -3
git log -1 --format=%B origin/stage/dev
```
- **Dev cluster stopped:** the auto-promotion fails at `argocd-update`. After `az aks start`, promote the
  newest Freight to dev manually (`kargo promote --stage dev --freight <name>` ⚑).
- **Pausing dev** (e.g. to hold a known-good dev for a demo): PR `autoPromotionEnabled: false` for `dev`
  in `hub/kargo/project.yaml`, and revert that PR afterwards.

## 6. Config-only Freight

A merge to `main` under `base/` or `stages/` creates Freight with the *same* images and a new commit. It is
promoted exactly like a code change, so the same procedures apply. Two differences in review:
- The infra diff matters most. A Postgres `imageName` bump, for example, is a rolling Postgres update in
  every stage it reaches, and a switchover in prod.
- Changes under `stages/prod/` only show up in prod's render. Staging can't have tested them, so review
  those prod diffs with extra care.

## 7. Expediting a hotfix

The fastest *safe* path for an urgent fix is:
1. Fix in the app repo.
2. Auto to dev.
3. Staging promotion.
4. Wait out the 1 h soak.
5. Prod.

That is about 1.5 h. Options if that's too slow:

| Option | When | How | Caveat |
|---|---|---|---|
| **Roll back instead** | prod is broken *now* | [rollback.md](rollback.md) | the usual answer |
| Manual Freight approval for prod | the incident commander decides the soak must be skipped | `kargo approve --freight <name> --stage prod` ⚑ [ProdApprover], then promote as §4 | bypasses the staging verification **and** soak gates. Record it in the incident. Confirm in the PoC how approval interacts with soak |
| Break-glass on `stage/prod` | Kargo or GitHub unavailable | [break-glass-and-dr.md BG-1](break-glass-and-dr.md) | reverts only; never push new, unverified manifests by hand |

## 8. Before the dev cutover (interim)
Until `docs/runbooks/cutover-dev.md` has been executed:
- `pantry-*-dev` Applications don't exist, and **don't promote to dev** (the promotion fails at `argocd-update`).
- Staging takes Freight straight from the Warehouse.
- The live dev environment is still deployed by the legacy bump commits to `apps/kustomization.yaml`.
