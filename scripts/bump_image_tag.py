#!/usr/bin/env python3
"""Set `newTag` for one image in apps/kustomization.yaml.

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

    python3 scripts/bump_image_tag.py ghcr.io/pjvjay/pantry-api dev-abc1234
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "apps" / "kustomization.yaml"
# A tag is what ends up in an image reference — refuse anything that could
# smuggle a different registry, a path separator, or shell metacharacters.
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def bump(text: str, image: str, tag: str) -> str:
    """Return `text` with `newTag` set for the entry whose name is `image`.

    Raises ValueError if the image is absent, so a typo fails the build
    instead of silently deploying nothing.
    """
    if not TAG_RE.match(tag):
        raise ValueError(f"refusing to write an implausible tag: {tag!r}")

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_entry = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- name:"):
            # A new list entry begins; we are inside ours only if it matches.
            in_entry = stripped.split("- name:", 1)[1].strip() == image
        elif in_entry and stripped.startswith("newTag:"):
            indent = line[: len(line) - len(line.lstrip())]
            newline = "\n" if line.endswith("\n") else ""
            out.append(f"{indent}newTag: {tag}{newline}")
            replaced = True
            in_entry = False
            continue
        out.append(line)

    if not replaced:
        raise ValueError(f"no images entry named {image!r} — nothing bumped")
    return "".join(out)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    image, tag = argv[0], argv[1]
    path = Path(argv[2]) if len(argv) == 3 else DEFAULT_PATH
    original = path.read_text()
    updated = bump(original, image, tag)
    if updated == original:
        print(f"{image} already at {tag}; nothing to do")
        return 0
    path.write_text(updated)
    print(f"{image} -> {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
