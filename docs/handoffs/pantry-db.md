# Handoff → `pantry-db` (migration compatibility contract, ADR 0004)

With Kargo, **rolling an environment back means promoting older Freight**. The older
`pantry-db-migrate` image finds nothing to apply, so the **older API runs against the newer schema**.
That is only safe if every migration is compatible with the API release already running in prod.

## Add to `CONTRIBUTING.md`

> ### Migrations must be expand/contract
> Every numbered migration must keep working for the **previous** pantry-api release.
> - ✅ Allowed in one step: add a table; add a nullable column; add a column with a default; add an index
>   (`CONCURRENTLY`); add a new enum value at the end.
> - ❌ Never in one step: drop or rename a column or table; change a column's type; add `NOT NULL` without
>   a default; tighten a constraint existing rows or old code could violate.
> - Renames and drops take **≥ 2 releases**:
>   1. expand (add the new column)
>   2. backfill + dual-write from the API
>   3. switch reads
>   4. contract (drop the old column) once no Freight in any stage still reads it
> - The seed file must stay idempotent (it re-runs on every sync).
>
> Rollback never rolls the schema back. Schema restore is break-glass, from the CNPG backup taken
> before the prod promotion.

## Add to the PR template

```markdown
### Migration checklist (ADR 0004)
- [ ] Backward-compatible with the pantry-api version currently in prod (expand/contract)
- [ ] No drop/rename/type change in this PR (or: this is the contract step of #___, released ≥ 1 prod promotion ago)
- [ ] Seed changes are idempotent
```

## Why the gitops side is safe to rely on
- The migration runs as a PreSync hook in `pantry-apps-<stage>`. A failed migration fails the Argo CD sync,
  which fails the Kargo promotion. The Freight is then not verified and can't progress.
- dev → staging → prod gives every migration two rehearsals on real Postgres before prod.
