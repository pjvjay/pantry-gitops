# ADR 0007: Identity provider and prod approvals

**Status:** Accepted · **Epic story:** KARGO-07, KARGO-14

## Decision
- **IdP: Microsoft Entra ID** via OIDC (authorization code + PKCE) for the Kargo UI/CLI and Argo CD. It is the
  identity plane already used for AKS and Key Vault, and it brings MFA, conditional access and PIM.
- **Map Entra *app roles*, not group object IDs.** The Kargo app registration defines app roles. Entra groups are
  assigned to the roles, and tokens carry a readable `roles` claim. This keeps GUIDs out of git and avoids the
  200-group token overage problem.

| App role (claim value) | Assigned Entra group | Kargo permissions (project `pantry`) |
|---|---|---|
| `Pantry.Viewer` | `pantry-delivery-viewers` (all engineers) | read everything |
| `Pantry.Promoter` | `pantry-delivery-promoters` | promote to **dev, staging** |
| `Pantry.ProdApprover` | `pantry-prod-approvers` (small; **PIM-eligible**, time-boxed activation) | promote to **prod** |
| `Pantry.Oncall` | `pantry-oncall` (the rotation; **PIM-eligible**, activated per shift) | operate: refresh, re-verify, abort a Promotion, Freight aliases (`patch` only, no promotion rights) |
| `Kargo.Admin` | `platform-admins` | Kargo system admin (Helm `api.oidc.admins`); break-glass |

- Nobody edits Kargo resources by hand. The pipeline changes only through PRs to `hub/kargo/`, and Argo CD
  selfHeal reverts any manual edit.
- **Prod has two independent gates.**
  1. **Kargo RBAC:** only `Pantry.ProdApprover` can start a prod promotion.
  2. **GitHub:** the promotion opens a PR against `stage/prod`. A ruleset requires one approval, and a
     second ruleset lets only the prod-approvers team (plus admins) update `stage/prod`, so merging is the gate.
  Policy: the person who promotes should not be the person who merges. GitHub cannot enforce this across systems,
  so it is recorded here and checked in the game day (KARGO-29).
- **GitHub App "kargo-pantry"** is the only automated writer to the repo. It has contents + pull-requests write on
  `pantry-gitops` only, and its private key lives in Key Vault, projected by ESO.

## Consequences
- `hub/kargo/rbac.yaml` maps the app-role claim values to Kargo roles.
- Team-based rulesets need the repo to live in a GitHub **organisation**. On a personal account, fall back to
  "PR + 1 approval" and treat the Kargo prod-approver role as the main gate.
