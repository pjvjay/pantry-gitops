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
COPY = ["base", "stages", "apps", "infra"]


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
    def test_params_and_kustomization_namespace_drift_is_caught(self):
        self.edit("stages/staging/infra/db-ns/kustomization.yaml", "pantry-staging-db", "pantry-stg-db")
        self.assertCaught("check_stage_params", "stages/staging/infra/db-ns namespace")

    # ---- data safety (ADR 0005 §5)
    def test_removing_namespace_protection_is_caught(self):
        self.edit("base/infra/db-ns/namespace.yaml",
                  "    argocd.argoproj.io/sync-options: Prune=false,Delete=false\n", "")
        self.assertCaught("check_rendered", "Namespace/pantry-db needs wave -15")

    def test_changing_the_live_database_spec_is_caught(self):
        self.edit("base/infra/db-ns/postgres-cluster.yaml", "  instances: 1\n", "  instances: 3\n")
        self.assertCaught("check_legacy_equivalence", "Cluster/pantry.spec.instances")

    def test_dev_host_change_is_allowed_by_equivalence(self):
        self.edit("stages/dev/params/stage-params.yaml", "host: dev.pantry.example.com", "host: x.example.com")
        self.assertEqual(check_gitops.run(self.tmp, KUSTOMIZE)["check_legacy_equivalence"], [])

    def test_tag_pinned_on_main_is_caught(self):
        self.edit("stages/staging/apps/kustomization.yaml", "components:",
                  "images:\n  - name: ghcr.io/pjvjay/pantry-api\n    newTag: dev-abc1234\ncomponents:")
        self.assertCaught("check_stage_params", "images: block")
        self.assertCaught("check_rendered", "pinned on main")


if __name__ == "__main__":
    unittest.main()
