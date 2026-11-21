# Handoff → `pantry-api`, `pantry-frontend`, `pantry-db` (KARGO-24, KARGO-28)

Three changes per repo, in this order. **§1 must wait for the dev cutover** (`docs/runbooks/cutover-dev.md`),
because until then the bump commit is still how dev deploys.

## 1. Stop writing to pantry-gitops, and revoke the credential (after cutover)
- Delete the workflow step that runs `scripts/bump_image_tag.py` and pushes to `pantry-gitops`.
- Delete the secret that held the gitops write token or deploy key, in the repo **and** any org-level
  secret. Revoke the token or key itself in GitHub.
- Verify: `pantry-gitops` → Settings → Deploy keys / the PAT owner's token list shows nothing for these repos.
  The only automated writer left is the `kargo-pantry` GitHub App.

CI then ends at "push image". Kargo's Warehouse discovers the image within 5 minutes.

## 2. Publish an orderable SemVer tag (Phase 2 of ADR 0003)
Keep pushing `dev-<sha7>` (traceability), and **also** push a SemVer tag for the same digest:

```yaml
# .github/workflows/<build>.yaml (sketch)
- name: Tags
  id: tags
  run: echo "semver=0.${{ github.run_number }}.0" >> "$GITHUB_OUTPUT"   # or derive from release tags
- uses: docker/build-push-action@<sha> # pin by SHA
  with:
    push: true
    tags: |
      ghcr.io/pjvjay/pantry-api:dev-${{ github.sha }}
      ghcr.io/pjvjay/pantry-api:${{ steps.tags.outputs.semver }}
```

Once all **three** repos publish SemVer tags, switch the Warehouse in one PR to pantry-gitops:

```yaml
    - image:
        repoURL: ghcr.io/pjvjay/pantry-api
        imageSelectionStrategy: SemVer
        strictSemvers: true          # only full X.Y.Z tags count (check_gitops.py enforces the pairing)
        discoveryLimit: 10
```
Also drop the `allowTags: ^dev-…` lines. SemVer selection reads tags only, with no per-tag manifest fetch,
so this also cuts GHCR API calls.

## 3. Sign images (keyless cosign), then enable the provenance gate (KARGO-28)
```yaml
permissions:
  contents: read
  packages: write
  id-token: write          # GitHub OIDC → Sigstore (no keys to manage)
steps:
  - uses: sigstore/cosign-installer@<sha> # pin by SHA
  - name: Sign by digest
    run: cosign sign --yes "ghcr.io/pjvjay/pantry-api@${{ steps.build.outputs.digest }}"
```
Sign **by digest**, from a workflow on `main`. The gate checks for exactly that identity:
`https://github.com/pjvjay/pantry-<repo>/.github/workflows/…@refs/heads/main`.

When all three repos have signed at least one build, enable `pantry-provenance` on every stage that takes
Freight directly from the Warehouse (instructions in `hub/kargo/analysis-provenance.yaml`).
`check_gitops.py` refuses a partial enablement.

## 4. pantry-api only: optional deeper functional suite (KARGO-20, follow-up)
Staging already runs `pantry-api-contract` (black-box: OpenAPI declares `/health`, `/recipes`, `/plan…`;
`/recipes` answers 200 JSON). **Please confirm those assumptions.** They were inferred from pantry-gitops'
docs, and a wrong one blocks every prod promotion.

For a deeper suite, publish `ghcr.io/pjvjay/pantry-api-tests` from the **same commit and tag** as the app image:
- Entrypoint runs the suite against `$BASE_URL` (e.g. `https://staging.pantry.example.com/pantry/api`)
  and exits non-zero on failure. Keep runtime under 10 min.
- `/plan` tests stub the LLM, or run against a budget-capped key, never an open-ended spend.
- Needs no cluster access, only HTTP(S).

pantry-gitops then adds an AnalysisTemplate for it plus a fourth Warehouse subscription, so the tests
always match the app in the same Freight.

## 5. pantry-db only
Adopt the migration contract in `docs/handoffs/pantry-db.md` (expand/contract; rollback never reverts the schema).
