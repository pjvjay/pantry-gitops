#!/usr/bin/env python3
"""Every image the apps deploy is pinned, and none is `latest`.

    kubectl kustomize apps > /tmp/apps.yaml
    python3 scripts/check_images.py --rendered /tmp/apps.yaml

A merge here is a deployment, so a pin that moves on its own (`latest`, or
no tag at all, which means latest) would let the cluster change without a
commit saying so. Two checks, stdlib only like bump_image_tag.py:

1. apps/kustomization.yaml's `images` block, where every pin lives: each
   entry has exactly one of `newTag` (a plausible tag, never latest) or
   `digest` (sha256:<64 hex> with the version as a comment, the form
   `bump_image_tag.py --digest` writes). Never both: kustomize would deploy
   the digest while the tag claimed something else.
2. With --rendered, the manifests kustomize actually produces: every
   `image:` names a tag or a digest, and no tag is latest. This catches a
   container whose image the `images` block does not cover at all.

Exit codes: 0 every image pinned, 1 a problem (each one printed), 2 usage.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_KUSTOMIZATION = Path(__file__).resolve().parent.parent / "apps" / "kustomization.yaml"
# The same rules bump_image_tag.py writes by.
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_LINE_RE = re.compile(r"^\s*(?:-\s+)?image:\s*(?P<ref>\S+)")


def _value(text: str) -> str:
    """A scalar after its key, without a trailing comment or quotes."""
    return text.split("#", 1)[0].strip().strip("'\"")


def images_block(text: str) -> list[dict[str, str | None]]:
    """The entries of the top-level `images:` list, line by line: name, newTag,
    digest and the digest line's comment (the version)."""
    entries: list[dict[str, str | None]] = []
    inside = False
    for line in text.splitlines():
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        if not line.startswith((" ", "\t", "-")):        # a top-level key
            inside = body.startswith("images:")
            continue
        if not inside:
            continue
        if body.startswith("- name:"):
            entries.append({"name": _value(body.split(":", 1)[1]), "newTag": None,
                            "digest": None, "comment": None})
        elif entries and body.startswith("newTag:"):
            entries[-1]["newTag"] = _value(body.split(":", 1)[1])
        elif entries and body.startswith("digest:"):
            value, _, comment = body.split(":", 1)[1].partition("#")
            entries[-1]["digest"] = value.strip().strip("'\"")
            entries[-1]["comment"] = comment.strip() or None
    return entries


def check_kustomization(text: str) -> list[str]:
    """Every way the images block fails to pin its images; empty when it pins them all."""
    entries = images_block(text)
    if not entries:
        return ["no images block: every image pin lives there (bump_image_tag.py writes it)"]
    problems = []
    for entry in entries:
        name, tag, digest = entry["name"], entry["newTag"], entry["digest"]
        if tag is not None and digest is not None:
            problems.append(f"{name}: both newTag and digest; keep one (kustomize deploys the "
                            f"digest, so the tag would mislead)")
        elif tag is None and digest is None:
            problems.append(f"{name}: no newTag or digest, so the manifest's own tag deploys "
                            f"(usually none, which means latest)")
        if tag is not None:
            if tag == "latest":
                problems.append(f"{name}: newTag latest moves without a commit; pin a version")
            elif not TAG_RE.match(tag):
                problems.append(f"{name}: implausible newTag {tag!r}")
        if digest is not None:
            if not DIGEST_RE.match(digest):
                problems.append(f"{name}: digest {digest!r} is not sha256:<64 hex>")
            if not entry["comment"]:
                problems.append(f"{name}: a digest needs its version as a comment "
                                f"(digest: sha256:... # 0.2.0)")
            elif not TAG_RE.match(entry["comment"]) or entry["comment"] == "latest":
                problems.append(f"{name}: the digest's comment {entry['comment']!r} "
                                f"is not a version")
    return problems


def image_refs(rendered: str) -> list[tuple[int, str]]:
    """(line number, reference) for every `image:` in rendered manifests."""
    refs = []
    for number, line in enumerate(rendered.splitlines(), start=1):
        match = IMAGE_LINE_RE.match(line)
        if match:
            refs.append((number, match.group("ref").strip("'\"")))
    return refs


def ref_problem(ref: str) -> str | None:
    """Why one image reference is not pinned, or None. A colon after the last
    slash is a tag; one before it is a registry port (localhost:5000/x)."""
    name, at, digest = ref.partition("@")
    if at:
        return None if DIGEST_RE.match(digest) else f"digest {digest!r} is not sha256:<64 hex>"
    last = name.rsplit("/", 1)[-1]
    if ":" not in last:
        return "no tag, which means latest"
    tag = last.rsplit(":", 1)[1]
    if tag == "latest":
        return "latest moves without a commit"
    return None if TAG_RE.match(tag) else f"implausible tag {tag!r}"


def check_rendered(rendered: str) -> tuple[list[str], list[str]]:
    """(problems, the references that passed)."""
    refs = image_refs(rendered)
    if not refs:
        return ["the rendered manifests contain no image: lines"], []
    problems, passed = [], []
    for number, ref in refs:
        problem = ref_problem(ref)
        if problem:
            problems.append(f"rendered line {number}: {ref}: {problem}")
        else:
            passed.append(ref)
    return problems, passed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check_images.py",
                                     description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--kustomization", type=Path, default=DEFAULT_KUSTOMIZATION)
    parser.add_argument("--rendered", help="output of `kubectl kustomize apps`; - for stdin")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        problems = check_kustomization(args.kustomization.read_text())
        passed: list[str] = []
        if args.rendered:
            rendered = sys.stdin.read() if args.rendered == "-" else Path(args.rendered).read_text()
            found, passed = check_rendered(rendered)
            problems += found
    except OSError as exc:
        print(f"check_images: {exc}", file=sys.stderr)
        return 2
    for ref in passed:
        print(f"ok    {ref}")
    for problem in problems:
        print(f"FAIL  {problem}")
    if problems:
        return 1
    print("every image is pinned" + ("" if args.rendered else " (images block only)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
