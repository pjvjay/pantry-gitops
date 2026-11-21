# ADR 0004: Database migrations under promotion and rollback

**Status:** Accepted; pantry-db must adopt the contract (see `docs/handoffs/pantry-db.md`) · **Epic story:** KARGO-04

## Context
The `pantry-db-migrate` PreSync hook applies pending migrations before the app rolls. Migrations only move
forward, and the seed is idempotent. In Kargo, "rollback" means promoting **older** Freight. The older migrate
image finds nothing to do, so the **older API runs against the newer schema**.

## Decision
1. **Expand/contract only.** Every migration must be backward-compatible with the API release currently in prod.
   Renames and drops happen over two or more releases: add → backfill/dual-write → switch reads → drop.
2. **Rollback never rolls back the schema.** Restoring a schema is break-glass: restore the CNPG backup taken
   before the prod promotion (KARGO-27, M6).
3. The migration stays a **PreSync hook inside `pantry-apps-<stage>`**. A failed migration fails the sync, and
   so the Kargo promotion. The Freight is then not verified and cannot move on.
4. Infra and apps stay **separate Applications** per stage. A PreSync hook runs before every sync wave, so
   merging them would deadlock a cold stage: the migration would wait for a database that doesn't exist yet.
