# ADR 0005: Rules for Kargo ↔ Argo CD integration

**Status:** Accepted · **Epic story:** KARGO-05

1. **Authorisation by annotation.** Every Kargo-managed Application has
   `kargo.akuity.io/authorized-stage: pantry:<stage>`. Kargo refuses to sync an Application without it.
   This annotation is the trust boundary between the Kargo project and Argo CD.
2. **Kargo writes git and never edits Application specs.** Applications permanently target `stage/<stage>`.
   The `argocd-update` step only triggers a sync and waits for the Application to reach `desiredRevision`.
   It never uses `updateTargetRevision`. Otherwise the hub root app and the ApplicationSet, which both
   self-heal, would fight Kargo.
3. **Auto-sync and selfHeal stay on.** Drift is still reverted. Auto-sync can only pull what Kargo committed.
4. **Infra, then apps.** Each promotion runs `argocd-update` for `pantry-infra-<stage>` and then for
   `pantry-apps-<stage>`. This orders the two Applications explicitly, where the baseline relied on retry/backoff.
5. **Nothing cascades into data.**
   - Generated Applications have no `resources-finalizer`.
   - The ApplicationSet sets `preserveResourcesOnDeletion: true`.
   - Namespaces and the CNPG `Cluster` carry `argocd.argoproj.io/sync-options: Prune=false,Delete=false`.
6. **Pipeline-as-GitOps.** Kargo resources live in `hub/kargo/`, synced by the `pantry-kargo` Application in
   AppProject `pantry-delivery`. Each stage has its own AppProject (`pantry-<stage>`), locked to that stage's
   cluster and namespaces, so a staging overlay mistake cannot deploy into dev's namespaces or into prod.
