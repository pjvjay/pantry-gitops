# Architecture Decision Records

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-stage-topology.md) | Three stages; dev + staging on the dev cluster, prod on the prod cluster; hub-and-spoke control plane on prod | Accepted |
| [0002](0002-rendered-stage-branches.md) | Kargo renders each stage into a `stage/<stage>` branch; `main` is human-only | Accepted |
| [0003](0003-freight-and-artifact-identity.md) | One Warehouse: 3 images + this repo; promotions pin digests | Accepted |
| [0004](0004-migration-compatibility.md) | Expand/contract migrations; rollback never rolls back the schema | Accepted (pantry-db to adopt) |
| [0005](0005-kargo-argocd-integration.md) | Kargo writes git and triggers syncs; it never edits Application specs | Accepted |
| [0006](0006-platform-and-bootstrap.md) | Kargo + Argo Rollouts are platform prerequisites installed by pantry-infra | Accepted (pantry-infra to implement) |
| [0007](0007-identity-and-approvals.md) | Entra ID OIDC with app roles; prod needs a Kargo prod-approver AND a PR merge | Accepted |
| [0008](0008-verification-and-gates.md) | Verify every stage on its public host; staging runs the API contract; prod soaks 1 h, takes a pre-promotion backup and runs HA | Accepted |

Every ADR records the *why*. If a decision changes, supersede the ADR with a new one rather than editing history.
