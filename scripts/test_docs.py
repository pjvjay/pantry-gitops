"""Keep the runbooks true: fail CI when docs drift from the repo.

On-call follows these pages under pressure. A dead link, a renamed file or
a wrong Application name in a runbook is an incident multiplier. This test
checks, for the operational docs:
  - every relative link and #anchor resolves (GitHub heading slugs)
  - every repo path mentioned (base/, stages/, hub/, scripts/, docs/, .github/) exists
  - every Argo CD Application, namespace, Kargo role, AnalysisTemplate and
    PromotionTask named in the text exists in the manifests

Run: python3 -m unittest discover -s scripts
"""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# Operational docs: checked for links AND names. The epic design docs
# (docs/kargo-epic/0[1-3]) describe the plan as it was proposed, so they
# only get the link check.
OPERATIONAL = [ROOT / "README.md", *sorted((ROOT / "docs/runbooks").glob("*.md")),
               *sorted((ROOT / "docs/handoffs").glob("*.md")), *sorted((ROOT / "docs/adr").glob("*.md")),
               ROOT / "docs/kargo-epic/poc-kargo-argocd.md"]
ALL_MD = sorted(set(OPERATIONAL) | set(ROOT.glob("docs/**/*.md")))
FENCE = re.compile(r"```.*?```", re.S)
LINK = re.compile(r"\]\(([^)\s]+)\)")
PATH = re.compile(r"(?<![\w/.<-])((?:base|stages|hub|scripts|docs|\.github)/(?:[\w./*-]|<[a-z]+>)*(?:[\w/*]|<[a-z]+>))")
PLACEHOLDERS = {"<stage>": None, "<new>": [], "<svc>": [], "<layer>": ["infra", "apps"], "<n>": []}


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    s = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return s.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    for line in FENCE.sub("", md.read_text()).splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if m:
            base = slug(m.group(1))
            n = seen.get(base, 0)
            out.add(base if n == 0 else f"{base}-{n}")
            seen[base] = n + 1
    return out


def stages() -> list[str]:
    return sorted(p.parent.parent.name for p in ROOT.glob("stages/*/params/stage-params.yaml"))


def kargo_docs() -> list[dict]:
    return [d for f in sorted((ROOT / "hub/kargo").glob("*.yaml")) for d in yaml.safe_load_all(f.read_text()) if d]


class TestDocs(unittest.TestCase):
    def test_links_and_anchors_resolve(self):
        broken = []
        for md in ALL_MD:
            for target in LINK.findall(FENCE.sub("", md.read_text())):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                path, _, frag = target.partition("#")
                dest = (md.parent / path).resolve() if path else md
                if not dest.exists():
                    broken.append(f"{md.relative_to(ROOT)}: missing {target}")
                elif frag and dest.suffix == ".md" and frag not in anchors(dest):
                    broken.append(f"{md.relative_to(ROOT)}: no heading for #{frag} in {dest.relative_to(ROOT)}")
        self.assertEqual(broken, [])

    def test_repo_paths_mentioned_exist(self):
        missing = []
        for md in OPERATIONAL:
            for raw in set(PATH.findall(md.read_text())):
                candidates = [raw]
                for ph, values in PLACEHOLDERS.items():
                    if ph in raw:
                        vals = stages() if values is None else values
                        candidates = [c.replace(ph, v) for c in candidates for v in vals]
                if any("<" in c for c in candidates) or not candidates:
                    continue  # an unexpanded placeholder (e.g. <new>): illustrative, not a path
                for c in candidates:
                    if not list(ROOT.glob(c.rstrip("/"))):
                        missing.append(f"{md.relative_to(ROOT)}: {c}")
        self.assertEqual(sorted(set(missing)), [])

    def test_named_objects_exist(self):
        st = stages()
        kd = kargo_docs()
        templates = {d["metadata"]["name"] for d in kd if d["kind"] == "AnalysisTemplate"}
        tasks = {d["metadata"]["name"] for d in kd if d["kind"] == "PromotionTask"}
        roles = {r for d in kd if d["kind"] == "ServiceAccount"
                 for r in (d["metadata"].get("annotations", {}).get("rbac.kargo.akuity.io/claim.roles", "").split(","))}
        roles.add("Kargo.Admin")  # configured in the Helm values (pantry-infra), not here
        namespaces = {"argocd", "kargo", "pantry"}
        for s in st:
            params = yaml.safe_load((ROOT / f"stages/{s}/params/stage-params.yaml").read_text())["data"]
            namespaces |= {params["appNamespace"], params["dbNamespace"]}
        wrong = []
        for md in OPERATIONAL:
            text = md.read_text()
            for layer, stage in re.findall(r"pantry-(infra|apps)-(<stage>|[a-z]+)", text):
                if stage not in st and stage != "<stage>":
                    wrong.append(f"{md.name}: Application pantry-{layer}-{stage}")
            for name in re.findall(r"\b(pantry-(?:smoke|api-contract|provenance)[\w-]*)", text):
                if name not in templates:
                    wrong.append(f"{md.name}: AnalysisTemplate {name}")
            for name in re.findall(r"\b(render-and-[a-z]+)\b", text):
                if name not in tasks:
                    wrong.append(f"{md.name}: PromotionTask {name}")
            for role in re.findall(r"\b((?:Pantry|Kargo)\.[A-Z][A-Za-z]+)\b", text):
                if role not in roles:
                    wrong.append(f"{md.name}: role {role}")
            for ns in re.findall(r"(?:-n|--namespace)\s+([a-z][a-z0-9-]*)\b", text):
                if ns not in namespaces:
                    wrong.append(f"{md.name}: namespace {ns}")
        self.assertEqual(sorted(set(wrong)), [])

    def test_slug_matches_github(self):
        self.assertEqual(slug("T4. Promotion stays Pending / Freight not offered"),
                         "t4-promotion-stays-pending--freight-not-offered")
        self.assertEqual(slug("2. Shift start (15 min)"), "2-shift-start-15-min")
        self.assertEqual(slug("BG-1: emergency prod rollback, faster than a PR"),
                         "bg-1-emergency-prod-rollback-faster-than-a-pr")


if __name__ == "__main__":
    unittest.main()
