# Pantry delivery handbook

How to operate, support and change the Kargo + Argo CD delivery platform for pantry-platform.
Written for **on-call engineers** and **engineers joining the team**.

## Who reads what

| You are… | Start with |
|---|---|
| New to the team | [onboarding.md](onboarding.md): mental model, golden rules, access, two-week exercise plan |
| Starting an on-call shift | [oncall.md §2](oncall.md#2-shift-start-15-min) shift-start checks, then keep [reference.md](reference.md) open |
| Responding to an alert | [oncall.md §3](oncall.md#3-alerts-and-what-to-do) alert → action map |
| Shipping something | [promotion.md](promotion.md) |
| Undoing something | [rollback.md](rollback.md) |
| Something failed | [troubleshooting.md](troubleshooting.md) symptom index |
| Changing config, the pipeline, credentials… | [routine-tasks.md](routine-tasks.md) |
| Kargo, GitHub or a cluster is down / data at risk | [break-glass-and-dr.md](break-glass-and-dr.md) |
| Running the one-time dev cutover | [cutover-dev.md](cutover-dev.md) |

## "I need to…" (fast lane)

| I need to… | Go to |
|---|---|
| promote a build to staging | [promotion.md §3](promotion.md#3-promote-to-staging) |
| promote to prod | [promotion.md §4](promotion.md#4-promote-to-prod) |
| roll prod back **now** | [rollback.md §4](rollback.md#4-procedure-a-roll-back-prod-standard), or [BG-1](break-glass-and-dr.md#bg-1-emergency-prod-rollback-faster-than-a-pr) if Kargo/GitHub is down |
| stop a bad build spreading | [rollback.md §7](rollback.md#7-quarantine-the-bad-freight) |
| find out why a promotion failed | [troubleshooting.md](troubleshooting.md) (start block at the top) |
| get a hotfix out faster | [promotion.md §7](promotion.md#7-expediting-a-hotfix) |
| see what runs where | `git log -3 origin/stage/<stage>`, or the Kargo UI. [reference.md §4](reference.md#4-command-cheat-sheet) |
| rotate a secret | [routine-tasks.md §7](routine-tasks.md#7-rotate-credentials) |
| restore prod data | [DR-2](break-glass-and-dr.md#dr-2-restore-prod-data-schema-or-data-corruption) |

## The platform in five sentences
1. App repos push images to GHCR. Merges to `main` under `base/` or `stages/` change manifests.
2. Kargo's **Warehouse** turns anything new into **Freight** (3 image digests + 1 commit).
3. Freight is **promoted** through **Stages** `dev → staging → prod`. Kargo renders it into a
   `stage/<stage>` branch, and Argo CD syncs that branch to the stage's cluster (infra first, then apps,
   with the DB migration before workloads).
4. After each promotion, **verification** checks the stage over its public URL. Only verified Freight moves
   on; prod also needs 1 h in staging, a prod approver, and a merged PR, and takes a backup first.
5. **Rollback** is promoting the previous Freight: same path, same audit trail.

## Keeping this handbook true
- `scripts/test_docs.py` (part of CI) checks every link and anchor in these pages. It also checks that
  every repo path, Argo CD Application, AnalysisTemplate and Kargo role named here still exists. If you
  rename something, CI tells you which page to fix.
- After every incident, fix what was wrong or slow here, in the same week ([oncall.md §9](oncall.md#9-after-any-incident-or-break-glass)).
- Kargo CLI lines marked ⚑ follow Kargo 1.6 syntax. Confirm them with `kargo <cmd> --help` on first use;
  the Kargo UI is the reference.
