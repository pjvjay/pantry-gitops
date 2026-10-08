"""Tests for check_images.

verify.yml runs this on every PR and every push to main, with the output
of `kubectl kustomize apps`. These pin down what counts as pinned.

Run: python3 -m unittest discover -s scripts
"""
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from bump_image_tag import bump
from check_images import check_kustomization, check_rendered, main, ref_problem

DIGEST = "sha256:" + "ab" * 32
REAL = (Path(__file__).resolve().parent.parent / "apps" / "kustomization.yaml").read_text()

KUSTOMIZATION = """\
# a comment
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- pantry-api
images:
- name: ghcr.io/pjvjay/pantry-api
  newName: ghcr.io/pjvjay/pantry-api
  newTag: 0.2.0
- name: ghcr.io/pjvjay/pantry-frontend
  newName: ghcr.io/pjvjay/pantry-frontend
  newTag: dev-ff846e7
"""

# The shapes `kubectl kustomize apps` prints (a container key, and a list item
# whose first key is image), trimmed to the lines that matter.
RENDERED = """\
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
      - env:
        - name: DB_HOST
        image: ghcr.io/pjvjay/pantry-api:0.2.0
        name: api
---
kind: Deployment
spec:
  template:
    spec:
      containers:
      - image: ghcr.io/pjvjay/pantry-frontend:dev-ff846e7
        name: frontend
"""


class TestKustomization(unittest.TestCase):
    def test_todays_file_passes(self):
        self.assertEqual(check_kustomization(REAL), [])

    def test_versions_and_dev_tags_pass(self):
        self.assertEqual(check_kustomization(KUSTOMIZATION), [])

    def test_latest_is_refused(self):
        bad = KUSTOMIZATION.replace("newTag: 0.2.0", "newTag: latest")
        [problem] = check_kustomization(bad)
        self.assertIn("pantry-api: newTag latest", problem)

    def test_an_entry_without_a_pin_is_refused(self):
        bad = KUSTOMIZATION.replace("  newTag: 0.2.0\n", "")
        [problem] = check_kustomization(bad)
        self.assertIn("pantry-api: no newTag or digest", problem)

    def test_both_pins_are_refused(self):
        bad = KUSTOMIZATION.replace("  newTag: 0.2.0\n", f"  newTag: 0.2.0\n  digest: {DIGEST}"
                                                          " # 0.2.0\n")
        [problem] = check_kustomization(bad)
        self.assertIn("both newTag and digest", problem)

    def test_what_bump_writes_with_digest_passes(self):
        pinned = bump(KUSTOMIZATION, "ghcr.io/pjvjay/pantry-api", "0.2.0", DIGEST)
        self.assertEqual(check_kustomization(pinned), [])

    def test_a_digest_needs_a_valid_digest_and_its_version(self):
        no_comment = KUSTOMIZATION.replace("  newTag: 0.2.0\n", f"  digest: {DIGEST}\n")
        self.assertIn("needs its version", check_kustomization(no_comment)[0])
        short = KUSTOMIZATION.replace("  newTag: 0.2.0\n", "  digest: sha256:abc # 0.2.0\n")
        self.assertIn("not sha256:<64 hex>", check_kustomization(short)[0])
        latest = KUSTOMIZATION.replace("  newTag: 0.2.0\n", f"  digest: {DIGEST} # latest\n")
        self.assertIn("is not a version", check_kustomization(latest)[0])

    def test_no_images_block_is_refused(self):
        self.assertIn("no images block", check_kustomization("resources:\n- a\n")[0])

    def test_only_the_images_list_counts(self):
        # A `- name:` under another key is not an image entry.
        text = KUSTOMIZATION + "replicas:\n- name: pantry-api\n  count: 2\n"
        self.assertEqual(check_kustomization(text), [])


class TestRendered(unittest.TestCase):
    def test_pinned_references_pass(self):
        problems, passed = check_rendered(RENDERED)
        self.assertEqual(problems, [])
        self.assertEqual(passed, ["ghcr.io/pjvjay/pantry-api:0.2.0",
                                  "ghcr.io/pjvjay/pantry-frontend:dev-ff846e7"])

    def test_untagged_and_latest_are_refused(self):
        problems, _ = check_rendered(RENDERED.replace("pantry-api:0.2.0", "pantry-api")
                                     .replace("pantry-frontend:dev-ff846e7",
                                              "pantry-frontend:latest"))
        self.assertEqual(len(problems), 2)
        self.assertIn("no tag, which means latest", problems[0])
        self.assertIn("latest moves", problems[1])

    def test_reference_forms(self):
        self.assertIsNone(ref_problem(f"ghcr.io/pjvjay/pantry-api@{DIGEST}"))
        self.assertIsNone(ref_problem(f"ghcr.io/pjvjay/pantry-api:0.2.0@{DIGEST}"))
        self.assertIsNone(ref_problem("localhost:5000/pantry-api:0.2.0"))
        self.assertIsNotNone(ref_problem("localhost:5000/pantry-api"))   # a port, not a tag
        self.assertIsNotNone(ref_problem("nginx"))
        self.assertIsNotNone(ref_problem("ghcr.io/pjvjay/pantry-api@sha256:abc"))

    def test_quoted_references_are_read(self):
        problems, passed = check_rendered('      image: "ghcr.io/pjvjay/pantry-api:0.2.0"\n')
        self.assertEqual((problems, passed), ([], ["ghcr.io/pjvjay/pantry-api:0.2.0"]))

    def test_nothing_rendered_is_a_problem(self):
        problems, _ = check_rendered("kind: Service\n")
        self.assertIn("no image: lines", problems[0])


class TestMain(unittest.TestCase):
    def run_main(self, kustomization, rendered=None):
        with tempfile.TemporaryDirectory() as tmp:
            k = Path(tmp) / "kustomization.yaml"
            k.write_text(kustomization)
            argv = ["--kustomization", str(k)]
            if rendered is not None:
                r = Path(tmp) / "rendered.yaml"
                r.write_text(rendered)
                argv += ["--rendered", str(r)]
            with redirect_stdout(io.StringIO()) as out:
                code = main(argv)
        return code, out.getvalue()

    def test_exit_codes(self):
        self.assertEqual(self.run_main(KUSTOMIZATION, RENDERED)[0], 0)
        code, out = self.run_main(KUSTOMIZATION.replace("0.2.0", "latest"), RENDERED)
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertEqual(self.run_main(KUSTOMIZATION, RENDERED + "  image: nginx\n")[0], 1)

    def test_a_missing_file_is_a_usage_error(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--kustomization", "/nonexistent/k.yaml"]), 2)


if __name__ == "__main__":
    unittest.main()
