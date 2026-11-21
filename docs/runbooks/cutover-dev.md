# Runbook: adopt the live environment as Kargo stage `dev` (KARGO-23)

**Goal:** move ownership of `pantry-app`/`pantry-db` on the dev cluster from the legacy Argo CD
(`argocd/` tree) to the hub (`pantry-{infra,apps}-dev`), with **zero data loss** and no Postgres restart.
**Duration:** ~60 min. **People:** 1 operator with admin on both clusters, 1 `Pantry.Promoter`.
**Rehearse first** on a kind pair (docs/kargo-epic/poc-kargo-argocd.md setup) with a seeded database.

## Why this is safe by construction
- The rendered `dev` stage equals the live legacy manifests except for image pins and the host. CI proves
  it (`check_legacy_equivalence`). The CNPG `Cluster` spec is identical, so adoption does not touch Postgres.
- Namespaces and the `Cluster` carry `Prune=false,Delete=false`, including the live legacy copies. Even a
  mistaken cascade can't delete the data; it would "only" delete workloads.
- Every repo state in this runbook is tested (`scripts/test_cutover.py`).

## What changes, visibly
- **One rolling restart of api and frontend** at step 6. Image refs change from `:dev-<sha>` to
  `@sha256:<digest>`: the same bytes, but a new pod template. With readiness probes and the default
  `RollingUpdate`, this causes no downtime. **Postgres is not restarted.**
- **The ingress host** changes to `stages/dev/params` `host` unless you set it to the legacy value first
  (the equivalence check allows either).

## Contexts used below
`--context dev` = dev cluster (legacy Argo CD + workloads). `--context hub` = prod cluster (hub).

---

### 0. Preconditions (stop if any fails)
- [ ] Hub bootstrapped, `pantry-kargo` Synced/Healthy. Staging has been promoted **and verified** at least once.
- [ ] `main` CI green. `stages/dev/params` `host` decided, with DNS and TLS ready for it.
- [ ] Dev-cluster Key Vault entries `pantry-db-password` / `pantry-mcp-tokens` **unchanged** (the adopted
      ExternalSecrets read the same keys).
- [ ] Announce a change window. Ask app-repo owners to pause merges. Legacy bump commits during the
      window are harmless after step 4, but they make step 3 harder.

### 1. Record the "before" state
```bash
P=$(kubectl --context dev -n pantry-db get pod -l cnpg.io/cluster=pantry,cnpg.io/instanceRole=primary -o name)
kubectl --context dev -n pantry-db get pod -l cnpg.io/cluster=pantry \
  -o custom-columns=NAME:.metadata.name,STARTED:.status.startTime,RESTARTS:.status.containerStatuses[0].restartCount \
  | tee before-pods.txt
kubectl --context dev -n pantry-db exec "$P" -- sh -c \
  "psql -d pantry -Atc \"select format('select %L, count(*) from %I.%I', schemaname||'.'||relname, schemaname, relname)
                          from pg_stat_user_tables order by 1\" | psql -d pantry -At" | tee before-counts.txt
```

### 2. Logical backup + restore test
```bash
kubectl --context dev -n pantry-db exec "$P" -- pg_dump -Fc -d pantry > pantry-$(date +%F).dump
kubectl --context dev -n pantry-db exec "$P" -- sh -c 'createdb pantry_restore_test && cat > /tmp/r.dump' < pantry-$(date +%F).dump
kubectl --context dev -n pantry-db exec "$P" -- sh -c 'pg_restore -d pantry_restore_test /tmp/r.dump && \
  psql -d pantry_restore_test -Atc "select count(*) from information_schema.tables where table_schema = current_schema()"; \
  dropdb pantry_restore_test; rm /tmp/r.dump'
```
Keep the dump outside the cluster until step 8 is done.

### 3. Choose the Freight to adopt
Pick Freight whose three image tags equal the live tags in `apps/kustomization.yaml`:
```bash
grep newTag apps/kustomization.yaml
kargo get freight --project pantry        # or the Kargo UI
```
If none match (e.g. a build landed since), assemble Freight with those exact tags in the Kargo UI.
Adopting Freight that differs from what runs turns the cutover into a deployment. Don't.

Check that the dev stage branch holds only the bootstrap README. An earlier promotion attempt during the
interim could have rendered something there, and the hub would apply it as soon as step 5 lands:
```bash
git fetch origin stage/dev && git ls-tree -r --name-only origin/stage/dev   # expect: README.md
```
If it holds manifests, delete `stage/dev` as an admin and re-run `scripts/bootstrap_stage_branches.sh`.

### 4. Detach the legacy Argo CD WITHOUT cascading
```bash
# Stop the legacy root from recreating its children.
kubectl --context dev -n argocd patch application pantry-root --type merge -p '{"spec":{"syncPolicy":null}}'
# Remove the cascade finalizers (analysis R1), then delete: resources stay, unmanaged.
for app in pantry-apps pantry-infra pantry-root; do
  kubectl --context dev -n argocd patch application "$app" --type json \
    -p '[{"op":"remove","path":"/metadata/finalizers"}]'
done
kubectl --context dev -n argocd get application pantry-apps pantry-infra pantry-root \
  -o jsonpath='{range .items[*]}{.metadata.name}: {.metadata.finalizers}{"\n"}{end}'   # must be empty
kubectl --context dev -n argocd delete application pantry-apps pantry-infra pantry-root
```
**Verify** that nothing was deleted:
```bash
kubectl --context dev -n pantry-app get deploy,svc,ingress,externalsecret
kubectl --context dev -n pantry-db  get cluster,externalsecret,pvc
diff <(kubectl --context dev -n pantry-db get pod -l cnpg.io/cluster=pantry \
  -o custom-columns=NAME:.metadata.name,STARTED:.status.startTime,RESTARTS:.status.containerStatuses[0].restartCount) before-pods.txt
```

### 5. PR #1: hand dev to the hub
```bash
python3 scripts/cutover.py adopt-dev && python3 scripts/check_gitops.py
git switch -c cutover/adopt-dev && git commit -am "cutover: adopt dev (KARGO-23 step 5)" && git push
```
Merge after CI is green. The hub creates `pantry-infra-dev` and `pantry-apps-dev`. They show a
ComparisonError (there's no `infra/`/`apps/` on `stage/dev` yet) and **change nothing**. Staging now only
takes Freight verified in dev.

### 6. First promotion to dev (by the Pantry.Promoter)
```bash
kargo promote --project pantry --freight <freight-from-step-3> --stage dev
```
Watch it in the Kargo UI. The sequence is:
1. Render and push to `stage/dev`.
2. Infra sync: adopts Namespaces, ExternalSecrets and the Cluster. Only labels/annotations change.
3. Apps sync: the PreSync migration runs, finds nothing pending, and finishes.
4. The rolling restart from the digest pins.
5. `pantry-smoke` verification.

### 7. Verify the "after" state
```bash
kubectl --context hub -n argocd get application pantry-infra-dev pantry-apps-dev   # Synced / Healthy
diff <(kubectl --context dev -n pantry-db get pod -l cnpg.io/cluster=pantry \
  -o custom-columns=NAME:.metadata.name,STARTED:.status.startTime,RESTARTS:.status.containerStatuses[0].restartCount) before-pods.txt \
  && echo "Postgres not restarted"
kubectl --context dev -n pantry-db exec "$P" -- sh -c \
  "psql -d pantry -Atc \"select format('select %L, count(*) from %I.%I', schemaname||'.'||relname, schemaname, relname)
                          from pg_stat_user_tables order by 1\" | psql -d pantry -At" | diff - before-counts.txt \
  && echo "row counts identical"
```
The Freight must show as **verified in dev** in the Kargo UI.

### 8. PR #2: turn on dev auto-promotion
```bash
python3 scripts/cutover.py enable-dev-autopromotion && python3 scripts/check_gitops.py
```
Merge. From here, new builds reach dev with no human action.

### 9. Afterwards
- App repos: remove the bump step and revoke the credential (`docs/handoffs/app-repos.md`, KARGO-24).
- After ≥ 2 stable weeks (KARGO-25):
  ```bash
  git rm -r apps infra argocd scripts/bump_image_tag.py scripts/test_bump_image_tag.py
  ```
  Also drop the legacy section from the README. pantry-infra uninstalls the dev cluster's own Argo CD.

---

## Rollback
| Failed at | Do this | Data |
|---|---|---|
| 1–3 | Stop. Nothing has changed. | untouched |
| 4 | `kubectl --context dev apply -f argocd/root-app.yaml`. The legacy Argo CD re-adopts everything as before | untouched |
| 5–7 | Revert PR #1. The hub deletes `pantry-*-dev` **without cascading** (no finalizers, `preserveResourcesOnDeletion`). Then `kubectl --context dev apply -f argocd/root-app.yaml`. Legacy re-syncs images back to tags: one rolling restart | untouched |
| 8 | Revert PR #2 (auto-promotion off). Everything else stays on the hub | untouched |
| Data doubt at any point | Stop. Compare against `before-counts.txt`. The dump from step 2 is the restore source | restore from dump |
