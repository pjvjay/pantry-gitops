# ADR 0006: Platform components and bootstrap

**Status:** Accepted; pantry-infra to implement (see `docs/handoffs/pantry-infra.md`) · **Epic story:** KARGO-06

- Kargo and Argo Rollouts (whose `AnalysisTemplate` API Kargo uses for verification in M4) join the **platform
  contract**, next to Argo CD, ingress-nginx, cert-manager, CNPG and ESO. pantry-infra installs them on the hub.
  This repo consumes the contract and does not install platform components, the same as in the baseline.
- Pin versions: Helm chart version plus image digests. The Kargo version is set in one place for CI
  (`KARGO_VERSION` in `.github/workflows/validate.yaml`) and must match what pantry-infra installs.
- Bootstrap order on the hub:
  1. Platform Terraform
  2. Key Vault secrets
  3. Register the dev cluster
  4. `kubectl apply -f hub/bootstrap/root-app.yaml`
