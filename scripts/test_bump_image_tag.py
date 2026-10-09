"""Tests for bump_image_tag.

The step this replaces was a downloaded binary and therefore untestable;
being testable is half the point of removing it.

Run: python3 -m unittest discover -s scripts
"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from bump_image_tag import bump, main

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

    def test_a_release_version_is_a_plain_tag(self):
        out = bump(FIXTURE, "ghcr.io/pjvjay/pantry-api", "0.2.0")
        self.assertIn("  newTag: 0.2.0\n", out)


DIGEST = "sha256:" + "ab" * 32
API = "ghcr.io/pjvjay/pantry-api"


class TestDigest(unittest.TestCase):
    """--digest: the entry deploys an exact image, the version kept as a comment."""

    def test_writes_a_digest_line_with_the_version_and_drops_newtag(self):
        out = bump(FIXTURE, API, "0.2.0", DIGEST)
        self.assertIn(f"  digest: {DIGEST} # 0.2.0\n", out)
        self.assertNotIn("dev-old111", out)
        self.assertIn("newTag: dev-old222", out)       # sibling untouched
        # One pin per entry: the api entry has the digest and no newTag left.
        api_entry = out.split("- name: ghcr.io/pjvjay/pantry-frontend")[0]
        self.assertEqual(api_entry.count("newTag:"), 0)

    def test_preserves_comments_and_structure(self):
        out = bump(FIXTURE, API, "0.2.0", DIGEST)
        self.assertTrue(out.startswith("# a comment that must survive"))
        self.assertEqual(len(out.splitlines()), len(FIXTURE.splitlines()))

    def test_idempotent(self):
        once = bump(FIXTURE, API, "0.2.0", DIGEST)
        self.assertEqual(bump(once, API, "0.2.0", DIGEST), once)

    def test_rejects_a_malformed_digest(self):
        for bad in ["sha256:abc", "sha512:" + "ab" * 64, "sha256:" + "AB" * 32,
                    "ab" * 32, DIGEST + "\nnewTag: other", DIGEST + " # 9.9.9", ""]:
            with self.assertRaises(ValueError, msg=bad):
                bump(FIXTURE, API, "0.2.0", bad)

    def test_a_new_digest_replaces_the_old_one(self):
        other = "sha256:" + "cd" * 32
        out = bump(bump(FIXTURE, API, "0.2.0", DIGEST), API, "0.3.0", other)
        self.assertIn(f"digest: {other} # 0.3.0", out)
        self.assertNotIn(DIGEST, out)

    def test_a_tag_bump_on_a_digest_pinned_entry_drops_the_digest(self):
        # Without this, kustomize would keep deploying the digest while the
        # file claimed the new tag.
        out = bump(bump(FIXTURE, API, "0.2.0", DIGEST), API, "0.2.1")
        self.assertIn("  newTag: 0.2.1\n", out)
        self.assertNotIn("digest:", out)

    def test_an_entry_with_both_pins_ends_with_one(self):
        both = FIXTURE.replace("  newTag: dev-old111\n",
                               f"  newTag: dev-old111\n  digest: {DIGEST}\n")
        out = bump(both, API, "0.2.0")
        api_entry = out.split("- name: ghcr.io/pjvjay/pantry-frontend")[0]
        self.assertEqual((api_entry.count("newTag:"), api_entry.count("digest:")), (1, 0))

    def test_stops_at_the_end_of_the_images_list(self):
        # A digest: key after the images block belongs to something else.
        text = FIXTURE + "replicas:\n- name: pantry-api\n  count: 2\ndigest: keep-me\n"
        out = bump(text, "ghcr.io/pjvjay/pantry-frontend", "0.2.0", DIGEST)
        self.assertTrue(out.endswith("digest: keep-me\n"))


class TestMain(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "kustomization.yaml"
        self.path.write_text(FIXTURE)

    def tearDown(self):
        self.dir.cleanup()

    def run_main(self, *argv):
        with redirect_stdout(io.StringIO()) as out:
            code = main(list(argv))
        return code, out.getvalue()

    def test_positional_usage_is_unchanged(self):
        code, out = self.run_main(API, "dev-new999", str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("-> dev-new999", out)
        self.assertEqual(self.path.read_text(), bump(FIXTURE, API, "dev-new999"))

    def test_digest_flag(self):
        code, _ = self.run_main(API, "0.2.0", str(self.path), "--digest", DIGEST)
        self.assertEqual(code, 0)
        self.assertIn(f"digest: {DIGEST} # 0.2.0", self.path.read_text())
        code, out = self.run_main(API, "0.2.0", "--digest", DIGEST, str(self.path))
        self.assertEqual(code, 0)
        self.assertIn("nothing to do", out)

    def test_usage_errors_exit_2(self):
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main([]), 2)
            self.assertEqual(main([API, "0.2.0", "a", "b"]), 2)
        self.assertIn("usage:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
