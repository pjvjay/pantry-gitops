# ADR 0003: Freight composition and artifact identity

**Status:** Accepted · **Epic story:** KARGO-03

## Decision
- **One Warehouse, `pantry`**, with four subscriptions: `ghcr.io/pjvjay/pantry-api`, `…/pantry-frontend`,
  `…/pantry-db-migrate`, and this repo's `main` (limited to `base/` and `stages/`). One Freight = one coherent
  release candidate. "What's in prod" is then a single Freight ID.
- **Phase 1 (now):** `imageSelectionStrategy: NewestBuild`, `strictSemvers: false`,
  `allowTags: ^dev-[0-9a-f]{7}$`, because the existing tags aren't orderable. The app repos need no changes.
- **Phase 2 (KARGO-24):** CI also pushes a neutral, orderable tag (SemVer). The Warehouse switches to
  `imageSelectionStrategy: SemVer` **with** `strictSemvers: true`, so only full `X.Y.Z` tags count.
- `strictSemvers` is required by the Kargo 1.6 schema even though it has a server-side default. Always set it
  explicitly, as `true` only together with `SemVer`. `check_gitops.py` enforces this.
  That drops the misleading `dev-` prefix from artifacts that reach prod.
- **Promotions always pin digests** (`image@sha256:…`) in rendered manifests. Tags are only how images are
  discovered, never what gets deployed.
- **No registry credentials.** No Deployment has `imagePullSecrets`, and AKS pulls these images today, so the
  GHCR packages are public. If they are ever made private, add a `kargo.akuity.io/cred-type: image` Secret
  (the same ESO pattern as `hub/kargo/credentials.yaml`).

## Known race
The api image and the migrate image are built by different repos, so Freight can briefly contain a new API
without its migration. ADR 0004's compatibility contract makes this harmless. Dev verification (M4) catches it if not.
