"""Tests for bump_image_tag.

The step this replaces was a downloaded binary and therefore untestable;
being testable is half the point of removing it.

Run: python3 -m unittest discover -s scripts
"""
import unittest

from bump_image_tag import bump

FIXTURE = """\
# a comment that must survive
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
images:
- name: ghcr.io/pjvjay/pantry-api
  newName: ghcr.io/pjvjay/pantry-api
  newTag: dev-old111
- name: ghcr.io/pjvjay/pantry-frontend
  newName: ghcr.io/pjvjay/pantry-frontend
  newTag: dev-old222
"""


class TestBump(unittest.TestCase):
    def test_sets_only_the_named_image(self):
        out = bump(FIXTURE, "ghcr.io/pjvjay/pantry-api", "dev-new999")
        self.assertIn("newTag: dev-new999", out)
        self.assertIn("newTag: dev-old222", out)      # sibling untouched
        self.assertNotIn("dev-old111", out)

    def test_preserves_comments_and_structure(self):
        out = bump(FIXTURE, "ghcr.io/pjvjay/pantry-api", "dev-new999")
        self.assertTrue(out.startswith("# a comment that must survive"))
        self.assertEqual(len(out.splitlines()), len(FIXTURE.splitlines()))

    def test_unknown_image_raises_rather_than_silently_doing_nothing(self):
        with self.assertRaises(ValueError):
            bump(FIXTURE, "ghcr.io/pjvjay/does-not-exist", "dev-new999")

    def test_rejects_a_tag_that_could_smuggle_a_registry_or_path(self):
        for bad in ["evil.io/x:latest", "../../etc/passwd", "a b", "", "x/y",
                    "$(whoami)", "tag\nnewTag: other"]:
            with self.assertRaises(ValueError, msg=bad):
                bump(FIXTURE, "ghcr.io/pjvjay/pantry-api", bad)

    def test_idempotent(self):
        once = bump(FIXTURE, "ghcr.io/pjvjay/pantry-api", "dev-new999")
        twice = bump(once, "ghcr.io/pjvjay/pantry-api", "dev-new999")
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
