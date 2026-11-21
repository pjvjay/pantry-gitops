#!/usr/bin/env python3
"""Pin one image in apps/kustomization.yaml: `newTag`, or with --digest a digest.

Replaces a `curl | tar | kustomize edit set image` step in three app-repo
CI workflows. That step downloaded a third-party binary over the network
with **no integrity verification of any kind** and then executed it inside
a job holding a credential with write access to this repository — the sole
deployment mechanism for the cluster. Pinning a version string is not a
supply-chain control: a release asset is mutable by its publisher, and
there was no checksum, signature or digest binding what ran to what was
reviewed. (Fetching a checksum from the same host would not have fixed it
either — same channel, same trust.)

The job it replaces does exactly one thing: rewrite one `newTag:` line.
That needs no dependency, so it now has none. Stdlib only, no YAML
library, so this runs on a bare `python3` and can be unit-tested.

Deliberately line-oriented rather than parse-and-redump: a YAML round-trip
would reformat and strip the comments that explain this file to whoever
reads it next.

    python3 scripts/bump_image_tag.py ghcr.io/pjvjay/pantry-api 0.2.0
    python3 scripts/bump_image_tag.py ghcr.io/pjvjay/pantry-api 0.2.0 \\
        --digest sha256:<64 hex>

With --digest the entry gets `digest: sha256:... # 0.2.0` instead of a
`newTag` line: kustomize then deploys exactly that image even if the tag
is ever moved, and the comment keeps the version readable (the platform
release train reads it back). An entry carries one pin, never both, so
whichever pin line it has is rewritten in place.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "apps" / "kustomization.yaml"
# A tag is what ends up in an image reference — refuse anything that could
# smuggle a different registry, a path separator, or shell metacharacters.
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PIN_KEYS = ("newTag:", "digest:")


def bump(text: str, image: str, tag: str, digest: str | None = None) -> str:
    """Return `text` with the entry whose name is `image` pinned to `tag`.

    The entry's pin line (`newTag:` or `digest:`) becomes `newTag: <tag>`,
    or with `digest` the line `digest: <digest> # <tag>`. A second pin line
    in the same entry is dropped: with both, kustomize would deploy the
    digest while the tag claimed something else.

    Raises ValueError if the image is absent, so a typo fails the build
    instead of silently deploying nothing.
    """
    if not TAG_RE.match(tag):
        raise ValueError(f"refusing to write an implausible tag: {tag!r}")
    if digest is not None and not DIGEST_RE.match(digest):
        raise ValueError(f"refusing to write an implausible digest: {digest!r}")
    pin = f"digest: {digest} # {tag}" if digest is not None else f"newTag: {tag}"

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    entry_indent: int | None = None   # the "- name:" indent while inside our entry
    pinned_here = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("- name:"):
            # A new list entry begins; we are inside ours only if it matches.
            ours = stripped.split("- name:", 1)[1].strip() == image
            entry_indent = indent if ours else None
            pinned_here = False
        elif (entry_indent is not None and stripped and not stripped.startswith("#")
              and indent <= entry_indent):
            entry_indent = None           # back at the list's level: our entry ended
        elif entry_indent is not None and stripped.startswith(PIN_KEYS):
            if pinned_here:
                continue                  # one pin per entry, never both
            newline = "\n" if line.endswith("\n") else ""
            out.append(f"{line[:indent]}{pin}{newline}")
            pinned_here = replaced = True
            continue
        out.append(line)

    if not replaced:
        raise ValueError(f"no images entry named {image!r} — nothing bumped")
    return "".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bump_image_tag.py", description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", help="the images entry's name, e.g. ghcr.io/pjvjay/pantry-api")
    parser.add_argument("tag", help="the tag to deploy, e.g. 0.2.0 or dev-abc1234")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH,
                        help="the kustomization (default: apps/kustomization.yaml)")
    parser.add_argument("--digest", help="sha256:<64 hex>: pin this digest, the tag as a comment")
    try:
        # Intermixed, so --digest may come before or after the optional path.
        args = parser.parse_intermixed_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    original = args.path.read_text()
    updated = bump(original, args.image, args.tag, args.digest)
    what = f"{args.tag} ({args.digest})" if args.digest else args.tag
    if updated == original:
        print(f"{args.image} already at {what}; nothing to do")
        return 0
    args.path.write_text(updated)
    print(f"{args.image} -> {what}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
