"""Tests for cutover.py: every intermediate repo state of the cutover must
pass check_gitops.py, including the final state after the legacy trees are
retired (KARGO-25).

Run: python3 -m unittest discover -s scripts
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import check_gitops
import cutover

REPO = Path(__file__).resolve().parent.parent
KUSTOMIZE = os.environ.get("KUSTOMIZE") or shutil.which("kustomize")


class TestCutover(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("base", "stages", "hub", "apps", "infra", "argocd"):
            shutil.copytree(REPO / d, self.tmp / d)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def failures(self):
        return {k: v for k, v in check_gitops.run(self.tmp, KUSTOMIZE).items() if v}

    def test_autopromotion_before_adoption_is_refused(self):
        with self.assertRaises(cutover.Refused):
            cutover.enable_dev_autopromotion(self.tmp)

    def test_steps_are_idempotent(self):
        self.assertTrue(cutover.adopt_dev(self.tmp))
        self.assertFalse(cutover.adopt_dev(self.tmp))
        self.assertTrue(cutover.enable_dev_autopromotion(self.tmp))
        self.assertFalse(cutover.enable_dev_autopromotion(self.tmp))

    def test_unexpected_file_state_is_refused_not_guessed(self):
        p = self.tmp / cutover.APPSET
        p.write_text(p.read_text().replace(cutover.DEV_PENDING_START, "                # edited by hand"))
        with self.assertRaises(cutover.Refused):
            cutover.adopt_dev(self.tmp)

    def test_adopt_dev_changes_only_the_intended_lines(self):
        before = {f: (self.tmp / f).read_text() for f in (cutover.APPSET, cutover.STAGES, cutover.PROJECT)}
        cutover.adopt_dev(self.tmp)
        after = {f: (self.tmp / f).read_text() for f in before}
        self.assertEqual(before[cutover.PROJECT], after[cutover.PROJECT])
        self.assertIn("- stage: dev", after[cutover.APPSET])
        self.assertNotIn("direct: true", after[cutover.STAGES].split("name: staging")[1].split("---")[0])
        self.assertIn("direct: true", after[cutover.STAGES].split("name: dev")[1].split("---")[0])

    @unittest.skipUnless(KUSTOMIZE, "kustomize not found")
    def test_every_cutover_state_passes_all_checks(self):
        self.assertEqual(self.failures(), {}, "before cutover")
        cutover.adopt_dev(self.tmp)
        self.assertEqual(self.failures(), {}, "after adopt-dev")
        cutover.enable_dev_autopromotion(self.tmp)
        self.assertEqual(self.failures(), {}, "after enable-dev-autopromotion")
        for legacy in ("apps", "infra", "argocd"):  # KARGO-25
            shutil.rmtree(self.tmp / legacy)
        self.assertEqual(self.failures(), {}, "after retiring the legacy trees")

    @unittest.skipUnless(KUSTOMIZE, "kustomize not found")
    def test_adopting_dev_is_rejected_while_staging_still_bypasses_it(self):
        # A half-done manual edit (element enabled, interim not removed) is caught.
        p = self.tmp / cutover.APPSET
        text = p.read_text()
        i, j = cutover._cut(text, cutover.DEV_PENDING_START, cutover.DEV_PENDING_END)
        p.write_text(text[:i] + cutover.DEV_ADOPTED + text[j:])
        self.assertIn("check_cutover_state", self.failures())


if __name__ == "__main__":
    unittest.main()
