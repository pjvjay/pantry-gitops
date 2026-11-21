# ADR 0002: Rendered stage branches

**Status:** Accepted · **Epic story:** KARGO-02

## Decision
- `main` holds what humans write: `base/`, `stages/<stage>/` (per-stage parameters), `hub/` (Argo CD + Kargo).
  It changes only through a PR with CI. **Kargo never writes to `main`.**
- For each promotion, Kargo checks out `main` **at the commit recorded in the Freight**, pins image digests in the
  working copy, runs `kustomize build`, and commits the plain YAML to **`stage/<stage>`**:
  ```
  stage/<stage>
  ├── infra/manifests.yaml   → Argo CD Application pantry-infra-<stage>
  └── apps/manifests.yaml    → Argo CD Application pantry-apps-<stage>
  ```
- Prod goes through a PR to `stage/prod` (see ADR 0007).

## Why (the alternative was overlays on `main`, with Kargo committing to `main`)
1. **Config changes are gated.** The Warehouse subscribes to `main`, so a change to `base/` becomes Freight and
   moves dev → staging → prod like an image does. With overlays on `main`, a `base/` edit would reach every
   stage in one commit.
2. **`desiredRevision` is unambiguous.** Only promotions to that stage move its branch.
3. **No feedback loop.** Kargo's commits don't land on the branch the Warehouse watches.
4. **Audit.** `git log -p stage/prod` shows exactly what YAML changed in prod, and when.
5. **Comments survive.** Kargo's kustomize edits strip comments (we confirmed this), but they only ever touch a
   throwaway working copy.

## Consequences
- Stage branches are machine-owned. Branch rules make them writable only by the Kargo GitHub App (ADR 0007).
- Argo CD no longer runs Kustomize for these Applications. It applies plain YAML.
- Adding a file under `base/` or `stages/` triggers new Freight. Files under `hub/`, `docs/` and `scripts/` do not.
