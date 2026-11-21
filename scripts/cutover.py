#!/usr/bin/env python3
"""Repo-side edits for adopting the live environment as Kargo stage `dev`.

Used by docs/runbooks/cutover-dev.md (KARGO-23). Each step is its own PR,
merged at a specific point in the runbook. Never run ahead of it.

    python3 scripts/cutover.py adopt-dev
        Enables the `dev` ApplicationSet element (the hub starts managing
        pantry-app/pantry-db on the dev cluster) and removes staging's
        interim `direct: true` (staging again only takes Freight proven in
        dev). Merge ONLY after the legacy Applications were deleted
        non-cascading (runbook step 4).

    python3 scripts/cutover.py enable-dev-autopromotion
        Turns on dev auto-promotion. Merge ONLY after the first, manual
        promotion to dev was verified (runbook step 7). In the same PR as
        adopt-dev, Kargo could auto-promote the newest Freight mid-cutover.

Line-oriented like bump_image_tag.py: comments survive, and an
unexpected file state is refused, never guessed at. Re-running a step
that's already applied is a no-op.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPSET = "hub/argocd/stages-appset.yaml"
STAGES = "hub/kargo/stages.yaml"
PROJECT = "hub/kargo/project.yaml"

DEV_PENDING_START = "                # dev is PENDING ADOPTION."
DEV_PENDING_END = "                #   dbNamespace: pantry-db\n"
DEV_ADOPTED = """                # dev: the original environment, adopted in place (KARGO-23).
                - stage: dev
                  cluster: dev
                  appNamespace: pantry-app
                  dbNamespace: pantry-db
"""
INTERIM_START = "        # INTERIM ONLY"
INTERIM_END = "        direct: true\n"

AUTO_OFF = """    # dev auto-promotes every new Freight: the target state. It stays OFF
    # until dev is adopted (KARGO-23), because until then no Application
    # pantry-*-dev exists for Kargo to sync (see hub/argocd/stages-appset.yaml).
    # check_gitops.py fails CI if this is enabled while dev is pending.
    - stageSelector:
        name: dev
      autoPromotionEnabled: false
"""
AUTO_ON = """    # dev auto-promotes every new Freight (enabled at cutover, KARGO-23).
    # Staging and prod are always promoted by a person (ADR 0007).
    - stageSelector:
        name: dev
      autoPromotionEnabled: true
"""


class Refused(Exception):
    pass


def _cut(text: str, start: str, end: str) -> tuple[int, int]:
    i = text.find(start)
    j = text.find(end, i)
    if i < 0 or j < 0:
        raise Refused(f"expected block starting {start.strip()!r} not found")
    return i, j + len(end)


def adopt_dev(root: Path) -> bool:
    appset, stages = root / APPSET, root / STAGES
    a, s = appset.read_text(), stages.read_text()
    if DEV_ADOPTED in a and INTERIM_START not in s:
        return False
    if DEV_ADOPTED not in a:
        i, j = _cut(a, DEV_PENDING_START, DEV_PENDING_END)
        a = a[:i] + DEV_ADOPTED + a[j:]
    if INTERIM_START in s:
        i, j = _cut(s, INTERIM_START, INTERIM_END)
        s = s[:i] + s[j:]
    appset.write_text(a)
    stages.write_text(s)
    return True


def enable_dev_autopromotion(root: Path) -> bool:
    if DEV_ADOPTED not in (root / APPSET).read_text():
        raise Refused("dev is not adopted yet: run and merge `adopt-dev` first (runbook step 5)")
    project = root / PROJECT
    text = project.read_text()
    if AUTO_ON in text:
        return False
    if text.count(AUTO_OFF) != 1:
        raise Refused("dev promotion policy is not in the expected state")
    project.write_text(text.replace(AUTO_OFF, AUTO_ON))
    return True


STEPS = {"adopt-dev": adopt_dev, "enable-dev-autopromotion": enable_dev_autopromotion}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in STEPS:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        changed = STEPS[argv[0]](ROOT)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(f"{argv[0]}: {'applied; review the diff, run check_gitops.py, open a PR' if changed else 'already applied'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
