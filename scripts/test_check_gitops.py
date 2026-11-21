"""Tests for check_gitops.

A checker that passes proves little; these tests prove each rule FAILS
on the mistake it exists to catch. Each test copies the real repo,
applies one realistic mistake, and asserts the right check reports it.

Run: python3 -m unittest discover -s scripts
Needs `kustomize` on PATH or $KUSTOMIZE (skipped otherwise).
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import check_gitops

REPO = Path(__file__).resolve().parent.parent
KUSTOMIZE = os.environ.get("KUSTOMIZE") or shutil.which("kustomize")
COPY = ["base", "stages", "hub", "apps", "infra"]


@unittest.skipUnless(KUSTOMIZE, "kustomize not found")
class TestCheckGitops(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in COPY:
            shutil.copytree(REPO / d, self.tmp / d)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def edit(self, rel, old, new, count=1):
        p = self.tmp / rel
        text = p.read_text()
        self.assertEqual(text.count(old), count, f"fixture drift: {old!r} in {rel}")
        p.write_text(text.replace(old, new))

    def assertCaught(self, check, needle):
        errs = check_gitops.run(self.tmp, KUSTOMIZE)[check]
        self.assertTrue(any(needle in e for e in errs), f"{check} did not report {needle!r}; got {errs}")

    # ---- the real repo is clean
    def test_repo_passes_every_check(self):
        results = check_gitops.run(self.tmp, KUSTOMIZE)
        self.assertEqual({k: v for k, v in results.items() if v}, {})

    # ---- isolation (ADR 0001)
    def test_staging_reusing_dev_namespace_is_caught(self):
        for f in ("stages/staging/params/stage-params.yaml", "stages/staging/apps/kustomization.yaml",
                  "stages/staging/infra/app-ns/kustomization.yaml"):
            self.edit(f, "pantry-staging-app", "pantry-app")
        self.assertCaught("check_isolation", "share cluster 'dev' and namespaces")
        self.assertCaught("check_argocd", "outside AppProject pantry-staging")

    def test_staging_sharing_dev_key_vault_entry_is_caught(self):
        self.edit("stages/staging/params/stage-params.yaml",
                  "kvDbPasswordKey: pantry-staging-db-password", "kvDbPasswordKey: pantry-db-password")
        self.assertCaught("check_isolation", "kvDbPasswordKey 'pantry-db-password'")

    def test_params_and_kustomization_namespace_drift_is_caught(self):
        self.edit("stages/staging/infra/db-ns/kustomization.yaml", "pantry-staging-db", "pantry-stg-db")
        self.assertCaught("check_stage_params", "stages/staging/infra/db-ns namespace")

    # ---- data safety (ADR 0005 §5)
    def test_removing_namespace_protection_is_caught(self):
        self.edit("base/infra/db-ns/namespace.yaml",
                  "    argocd.argoproj.io/sync-options: Prune=false,Delete=false\n", "")
        self.assertCaught("check_rendered", "Namespace/pantry-db needs wave -15")

    def test_finalizer_on_generated_applications_is_caught(self):
        self.edit("hub/argocd/stages-appset.yaml", '      name: "pantry-{{.layer}}-{{.stage}}"\n',
                  '      name: "pantry-{{.layer}}-{{.stage}}"\n      finalizers:\n'
                  "        - resources-finalizer.argocd.argoproj.io\n")
        self.assertCaught("check_argocd", "no finalizers")

    def test_changing_the_live_database_spec_is_caught(self):
        self.edit("base/infra/db-ns/postgres-cluster.yaml", "  instances: 1\n", "  instances: 3\n")
        self.assertCaught("check_legacy_equivalence", "Cluster/pantry.spec.instances")

    def test_dev_host_change_is_allowed_by_equivalence(self):
        self.edit("stages/dev/params/stage-params.yaml", "host: dev.pantry.example.com", "host: x.example.com")
        self.assertEqual(check_gitops.run(self.tmp, KUSTOMIZE)["check_legacy_equivalence"], [])

    # ---- Argo CD <-> Kargo wiring (ADR 0005)
    def test_missing_authorized_stage_annotation_is_caught(self):
        self.edit("hub/argocd/stages-appset.yaml", 'kargo.akuity.io/authorized-stage: "pantry:{{.stage}}"',
                  'example.com/other: "x"')
        self.assertCaught("check_argocd", "annotation")

    def test_update_target_revision_is_caught(self):
        self.edit("hub/kargo/promotion-tasks.yaml",
                  "                desiredRevision: ${{ task.outputs.commit.commit }}\n",
                  "                desiredRevision: ${{ task.outputs.commit.commit }}\n"
                  "                updateTargetRevision: true\n", count=2)
        self.assertCaught("check_kargo", "updateTargetRevision forbidden")

    def test_apps_before_infra_is_caught(self):
        self.edit("hub/kargo/promotion-tasks.yaml", "name: pantry-infra-${{ ctx.stage }}", "name: TMP", count=2)
        self.edit("hub/kargo/promotion-tasks.yaml", "name: pantry-apps-${{ ctx.stage }}",
                  "name: pantry-infra-${{ ctx.stage }}", count=2)
        self.edit("hub/kargo/promotion-tasks.yaml", "name: TMP", "name: pantry-apps-${{ ctx.stage }}", count=2)
        self.assertCaught("check_kargo", "infra then apps")

    def test_render_drift_between_tasks_is_caught(self):
        p = self.tmp / "hub/kargo/promotion-tasks.yaml"
        text = p.read_text()
        i = text.rindex("outPath: ./out/apps/manifests.yaml")  # only in the prod task
        p.write_text(text[:i] + "outPath: ./out/apps/all.yaml" + text[i + len("outPath: ./out/apps/manifests.yaml"):])
        self.assertCaught("check_kargo", "render")

    def test_image_not_tracked_by_warehouse_is_caught(self):
        self.edit("hub/kargo/warehouse.yaml", "repoURL: ghcr.io/pjvjay/pantry-frontend",
                  "repoURL: ghcr.io/pjvjay/pantry-frontend-old")
        self.assertCaught("check_kargo", "Warehouse images")

    def test_missing_strict_semvers_is_caught(self):
        self.edit("hub/kargo/warehouse.yaml", "        strictSemvers: false\n", "", count=3)
        self.assertCaught("check_kargo", "strictSemvers must be set explicitly")

    def test_strict_semvers_with_newest_build_is_caught(self):
        self.edit("hub/kargo/warehouse.yaml", "strictSemvers: false", "strictSemvers: true", count=3)
        self.assertCaught("check_kargo", "strictSemvers: true only with imageSelectionStrategy SemVer")

    def test_hub_changes_creating_freight_is_caught(self):
        self.edit("hub/kargo/warehouse.yaml", "          - stages/\n", "          - stages/\n          - hub/\n")
        self.assertCaught("check_kargo", "includePaths")

    def test_prod_without_pr_gate_is_caught(self):
        self.edit("hub/kargo/stages.yaml", "name: render-and-pr", "name: render-and-push")
        self.assertCaught("check_kargo", "open a PR")

    def test_prod_direct_from_warehouse_is_caught(self):
        self.edit("hub/kargo/stages.yaml", "        stages:\n          - staging\n",
                  "        stages:\n          - staging\n        direct: true\n")
        self.assertCaught("check_kargo", "never take Freight directly")

    def test_tag_pinned_on_main_is_caught(self):
        self.edit("stages/staging/apps/kustomization.yaml", "components:",
                  "images:\n  - name: ghcr.io/pjvjay/pantry-api\n    newTag: dev-abc1234\ncomponents:")
        self.assertCaught("check_stage_params", "images: block")
        self.assertCaught("check_rendered", "pinned on main")

    # ---- cutover state (KARGO-23)
    def test_auto_promoting_unadopted_dev_is_caught(self):
        self.edit("hub/kargo/project.yaml", "autoPromotionEnabled: false", "autoPromotionEnabled: true")
        self.assertCaught("check_cutover_state", "auto-promotion enabled for 'dev'")

    def test_removing_interim_direct_before_cutover_is_caught(self):
        p = self.tmp / "hub/kargo/stages.yaml"
        text = p.read_text()
        i = text.index("        # INTERIM ONLY")
        j = text.index("        direct: true\n", i) + len("        direct: true\n")
        p.write_text(text[:i] + text[j:])
        self.assertCaught("check_cutover_state", "needs the interim direct: true")

    def test_forgetting_to_remove_interim_direct_at_cutover_is_caught(self):
        self.edit("hub/argocd/stages-appset.yaml",
                  "                # - stage: dev\n                #   cluster: dev\n"
                  "                #   appNamespace: pantry-app\n                #   dbNamespace: pantry-db\n",
                  "                - stage: dev\n                  cluster: dev\n"
                  "                  appNamespace: pantry-app\n                  dbNamespace: pantry-db\n")
        self.assertCaught("check_cutover_state", "remove the interim direct: true")

    # ---- verification & gates (M4)
    def test_stage_without_verification_is_caught(self):
        p = self.tmp / "hub/kargo/stages.yaml"
        text = p.read_text()
        i = text.index("  verification:")  # dev's
        j = text.index("---", i)
        p.write_text(text[:i] + text[j:])
        self.assertCaught("check_verification", "Stage dev: no verification")

    def test_verification_host_drift_is_caught(self):
        self.edit("stages/staging/params/stage-params.yaml", "host: staging.pantry.example.com",
                  "host: stg.pantry.example.com")
        self.assertCaught("check_verification", "verification host 'staging.pantry.example.com'")

    def test_prod_feeder_without_contract_is_caught(self):
        self.edit("hub/kargo/stages.yaml", "      - name: pantry-api-contract\n", "")
        self.assertCaught("check_verification", "feeds prod, so it must run pantry-api-contract")

    def test_prod_without_soak_is_caught(self):
        self.edit("hub/kargo/stages.yaml", "        requiredSoakTime: 1h\n", "")
        self.assertCaught("check_verification", "requiredSoakTime")

    def test_unpinned_verification_image_is_caught(self):
        self.edit("hub/kargo/analysis-templates.yaml", "curlimages/curl:8.11.1", "curlimages/curl")
        self.assertCaught("check_verification", "must be pinned")

    def test_kargo_kind_not_allowed_by_delivery_project_is_caught(self):
        self.edit("hub/argocd/projects.yaml", "    - group: argoproj.io\n      kind: AnalysisTemplate\n", "")
        self.assertCaught("check_argocd", "('argoproj.io', 'AnalysisTemplate') not allowed by AppProject pantry-delivery")

    def test_missing_failure_notifications_is_caught(self):
        self.edit("hub/argocd/stages-appset.yaml",
                  "        notifications.argoproj.io/subscribe.on-sync-failed.teams: pantry-delivery\n", "")
        self.assertCaught("check_argocd", "on-sync-failed")

    PROVENANCE_ARGS = (
        "      - name: api-image\n        value: x@sha256:1\n"
        "      - name: web-image\n        value: x@sha256:2\n"
        "      - name: migrate-image\n        value: x@sha256:3\n")

    def enable_provenance(self, stage_host):
        self.edit("hub/kargo/stages.yaml", f"      - name: host\n        value: {stage_host}\n",
                  f"      - name: host\n        value: {stage_host}\n" + self.PROVENANCE_ARGS)
        self.edit("hub/kargo/stages.yaml",
                  f"    args:\n      - name: host\n        value: {stage_host}\n",
                  f"      - name: pantry-provenance\n    args:\n      - name: host\n        value: {stage_host}\n")

    def test_provenance_bypass_via_direct_stage_is_caught(self):
        self.enable_provenance("dev.pantry.example.com")  # dev only; staging is still direct (interim)
        self.assertCaught("check_verification", "Stage staging: takes Freight directly from the Warehouse")

    def test_provenance_on_every_entry_point_passes(self):
        self.enable_provenance("dev.pantry.example.com")
        self.enable_provenance("staging.pantry.example.com")
        self.assertEqual(check_gitops.run(self.tmp, KUSTOMIZE)["check_verification"], [])

    # ---- prod hardening (M6)
    def test_prod_single_instance_is_caught(self):
        self.edit("stages/prod/infra/db-ns/cluster-ha-backups.yaml", "  instances: 3\n", "  instances: 1\n")
        self.assertCaught("check_prod_hardening", "instances >= 3")

    def test_prod_without_pre_promotion_backup_is_caught(self):
        self.edit("stages/prod/infra/db-ns/kustomization.yaml", "  - pre-promotion-backup.yaml\n", "")
        self.assertCaught("check_prod_hardening", "pre-promotion Backup hook")

    def test_pre_promotion_backup_before_cluster_exists_is_caught(self):
        self.edit("stages/prod/infra/db-ns/pre-promotion-backup.yaml", 'sync-wave: "-4"', 'sync-wave: "-6"')
        self.assertCaught("check_prod_hardening", "after its wave")

    def test_prod_deployment_without_pdb_is_caught(self):
        self.edit("stages/prod/apps/kustomization.yaml", "  - pdb.yaml\n", "")
        self.assertCaught("check_prod_hardening", "Deployment/pantry-api has no PodDisruptionBudget")

    def test_prod_hardening_never_leaks_into_dev(self):
        # dev must stay identical to the live legacy Cluster while hardening prod.
        results = check_gitops.run(self.tmp, KUSTOMIZE)
        self.assertEqual(results["check_legacy_equivalence"], [])
        self.assertEqual(results["check_prod_hardening"], [])

    # ---- RBAC (ADR 0007)
    def test_promoters_gaining_prod_is_caught(self):
        self.edit("hub/kargo/rbac.yaml", "resourceNames: [dev, staging]", "resourceNames: [dev, staging, prod]")
        self.assertCaught("check_rbac", "only Pantry.ProdApprover allowed")

    def test_hand_editing_pipeline_permission_is_caught(self):
        self.edit("hub/kargo/rbac.yaml", "verbs: [promote]\n---\napiVersion: rbac.authorization.k8s.io/v1\n"
                  "kind: RoleBinding\nmetadata:\n  name: pantry-prod-approver",
                  "verbs: [promote, update]\n---\napiVersion: rbac.authorization.k8s.io/v1\n"
                  "kind: RoleBinding\nmetadata:\n  name: pantry-prod-approver")
        self.assertCaught("check_rbac", "no write verbs")

    def test_oncall_gaining_update_is_caught(self):
        self.edit("hub/kargo/rbac.yaml", "    resources: [freights, promotions, stages, warehouses]\n    verbs: [patch]",
                  "    resources: [freights, promotions, stages, warehouses]\n    verbs: [patch, update]")
        self.assertCaught("check_rbac", "no write verbs")

    def test_oncall_patching_pipeline_config_is_caught(self):
        self.edit("hub/kargo/rbac.yaml", "resources: [freights, promotions, stages, warehouses]",
                  "resources: [freights, promotions, stages, warehouses, projectconfigs]")
        self.assertCaught("check_rbac", "only for Pantry.Oncall")

    def test_patch_for_a_non_oncall_role_is_caught(self):
        self.edit("hub/kargo/rbac.yaml", "    resourceNames: [dev, staging]\n    verbs: [promote]",
                  "    resourceNames: [dev, staging]\n    verbs: [promote, patch]")
        self.assertCaught("check_rbac", "only for Pantry.Oncall")

    # ---- AppProject least privilege
    def test_new_resource_kind_outside_project_whitelist_is_caught(self):
        (self.tmp / "base/apps/cm.yaml").write_text(
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: x\n  namespace: pantry-app\n")
        self.edit("base/apps/kustomization.yaml", "  - migrate-job.yaml\n", "  - migrate-job.yaml\n  - cm.yaml\n")
        self.assertCaught("check_argocd", "('', 'ConfigMap') not in AppProject")


if __name__ == "__main__":
    unittest.main()
