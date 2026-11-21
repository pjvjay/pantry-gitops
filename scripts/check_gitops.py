#!/usr/bin/env python3
"""Cross-check the stage parameters, Argo CD and Kargo layers of this repo.

Schema validators (kubeconform, in CI) check each manifest on its own.
This checks what they can't: that the layers agree with each other, and
that the safety properties the design depends on still hold. Each rule
cites the ADR it protects (docs/adr/).

    python3 scripts/check_gitops.py            # exits 1 on any violation
    KUSTOMIZE=/path/to/kustomize python3 scripts/check_gitops.py

Needs `kustomize` on PATH (or $KUSTOMIZE) and PyYAML.
"""
from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LAYERS = ("infra", "apps")
PROTECT = "Prune=false,Delete=false"


# --------------------------------------------------------------- loading
def load_docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def load_dir(path: Path, recurse: bool = False) -> list[dict]:
    pattern = "**/*.yaml" if recurse else "*.yaml"
    return [d for f in sorted(path.glob(pattern)) for d in load_docs(f)]


def kustomize_build(kustomize: str, path: Path) -> list[dict]:
    out = subprocess.run([kustomize, "build", str(path)], check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def one(docs: list[dict], kind: str, name: str | None = None) -> dict | None:
    for d in docs:
        if d.get("kind") == kind and (name is None or d["metadata"]["name"] == name):
            return d
    return None


def ann(doc: dict, key: str) -> str | None:
    return (doc.get("metadata", {}).get("annotations") or {}).get(key)


class Repo:
    """Everything the checks need, loaded once."""

    def __init__(self, root: Path, kustomize: str):
        self.root = root
        self.kustomize = kustomize
        self.stage_names = sorted(p.parent.parent.name for p in root.glob("stages/*/params/stage-params.yaml"))
        self.params = {s: one(load_docs(root / f"stages/{s}/params/stage-params.yaml"), "ConfigMap")["data"]
                       for s in self.stage_names}
        self.kustomizations = {
            s: {sub: yaml.safe_load((root / f"stages/{s}/{sub}/kustomization.yaml").read_text())
                for sub in ("apps", "infra/app-ns", "infra/db-ns")}
            for s in self.stage_names}
        self.rendered = {s: {layer: kustomize_build(kustomize, root / f"stages/{s}/{layer}") for layer in LAYERS}
                         for s in self.stage_names}
        self.legacy = None
        if (root / "apps/kustomization.yaml").exists() and (root / "infra").is_dir():
            self.legacy = kustomize_build(kustomize, root / "apps") + load_dir(root / "infra", recurse=True)


def _containers(doc: dict) -> list[dict]:
    spec = doc.get("spec", {}).get("template", {}).get("spec", {})
    return spec.get("containers", []) + spec.get("initContainers", [])


def _env(container: dict, name: str) -> str | None:
    for e in container.get("env", []):
        if e["name"] == name:
            return e.get("value")
    return None


# --------------------------------------------------------------- checks
def check_stage_params(r: Repo) -> list[str]:
    """Each stage's namespaces are declared once (params) and used consistently."""
    errs = []
    if not r.stage_names:
        return ["no stages found under stages/*/params"]
    for s in r.stage_names:
        p, k = r.params[s], r.kustomizations[s]
        for key in ("appNamespace", "dbNamespace", "host", "kvDbPasswordKey", "kvMcpTokensKey"):
            if not p.get(key):
                errs.append(f"{s}: params missing {key}")
        for sub, key in (("apps", "appNamespace"), ("infra/app-ns", "appNamespace"), ("infra/db-ns", "dbNamespace")):
            if k[sub].get("namespace") != p.get(key):
                errs.append(f"{s}: stages/{s}/{sub} namespace {k[sub].get('namespace')!r} != params {key} {p.get(key)!r}")
        if "images" in k["apps"]:
            errs.append(f"{s}: stages/{s}/apps has an images: block; only Kargo pins images (ADR 0003)")
    return errs


def check_rendered(r: Repo) -> list[str]:
    """Rendered stages keep the wiring and safety annotations (ADR 0001/0004/0005)."""
    errs = []
    for s in r.stage_names:
        p = r.params[s]
        infra, apps = r.rendered[s]["infra"], r.rendered[s]["apps"]
        want_db = f"pantry-rw.{p['dbNamespace']}.svc.cluster.local"
        for kind, name, cname in (("Deployment", "pantry-api", "api"), ("Job", "pantry-db-migrate", "migrate")):
            d = one(apps, kind, name)
            c = next((c for c in _containers(d) if c["name"] == cname), None) if d else None
            if not c or _env(c, "DB_HOST") != want_db:
                errs.append(f"{s}: {kind}/{name} DB_HOST != {want_db}")
        job = one(apps, "Job", "pantry-db-migrate")
        if not job or ann(job, "argocd.argoproj.io/hook") != "PreSync":
            errs.append(f"{s}: migration Job must stay a PreSync hook (ADR 0004)")
        for ns in by_kind(infra, "Namespace"):
            if ann(ns, "argocd.argoproj.io/sync-wave") != "-15" or ann(ns, "argocd.argoproj.io/sync-options") != PROTECT:
                errs.append(f"{s}: Namespace/{ns['metadata']['name']} needs wave -15 and {PROTECT}")
        if {n["metadata"]["name"] for n in by_kind(infra, "Namespace")} != {p["appNamespace"], p["dbNamespace"]}:
            errs.append(f"{s}: rendered Namespaces != params app/db namespaces")
        cluster = one(infra, "Cluster")
        if not cluster or ann(cluster, "argocd.argoproj.io/sync-options") != PROTECT \
                or ann(cluster, "argocd.argoproj.io/sync-wave") != "-5":
            errs.append(f"{s}: CNPG Cluster needs wave -5 and {PROTECT} (ADR 0005 §5)")
        for es in by_kind(infra, "ExternalSecret"):
            if ann(es, "argocd.argoproj.io/sync-wave") != "-10":
                errs.append(f"{s}: ExternalSecret/{es['metadata']['name']} must be wave -10")
        kv = {(e["metadata"]["name"], d["secretKey"]): d["remoteRef"]["key"]
              for e in by_kind(infra, "ExternalSecret") for d in e["spec"]["data"]}
        for (es, sk), want in ((("pantry-app-credentials", "password"), p["kvDbPasswordKey"]),
                               (("pantry-mcp-credentials", "tokens"), p["kvMcpTokensKey"])):
            got = {v for (n, k), v in kv.items() if n == es and k == sk}
            if got != {want}:
                errs.append(f"{s}: ExternalSecret {es}/{sk} Key Vault key {sorted(got)} != params {want!r}")
        hosts = {h for ing in by_kind(apps, "Ingress") for h in
                 [rule["host"] for rule in ing["spec"].get("rules", [])] +
                 [h for t in ing["spec"].get("tls", []) for h in t["hosts"]]}
        if hosts != {p["host"]}:
            errs.append(f"{s}: Ingress hosts {sorted(hosts)} != params host {p['host']!r}")
        for d in apps:
            for c in _containers(d):
                if ":" in c["image"].split("/")[-1] or "@" in c["image"]:
                    errs.append(f"{s}: {d['kind']}/{d['metadata']['name']} image {c['image']!r} is pinned on main; "
                                "only Kargo pins images (ADR 0003)")
    return errs


# Fields allowed to differ between the live (legacy) tree and the rendered dev
# stage: image pins move to Kargo, and the host is a per-stage placeholder.
_LEGACY_ALLOWED = [
    re.compile(r"^Deployment/.*\.spec\.template\.spec\.containers\[\d+\]\.image$"),
    re.compile(r"^Job/pantry-db-migrate\.spec\.template\.spec\.containers\[\d+\]\.image$"),
    re.compile(r"^Ingress/.*\.spec\.(rules\[\d+\]\.host|tls\[\d+\]\.hosts\[\d+\])$"),
]


def _diff(a, b, path):
    if type(a) is not type(b):
        yield path
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                yield f"{path}.{k}"
            else:
                yield from _diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        if len(a) != len(b):
            yield path
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                yield from _diff(x, y, f"{path}[{i}]")
    elif a != b:
        yield path


def check_legacy_equivalence(r: Repo) -> list[str]:
    """Adopting dev must change nothing material, above all not the database (ADR 0001).

    Runs while the legacy apps/ + infra/ trees exist (until KARGO-23/25).
    """
    if r.legacy is None or "dev" not in r.stage_names:
        return []
    key = lambda d: (d["kind"], d["metadata"].get("namespace", ""), d["metadata"]["name"])  # noqa: E731
    legacy = {key(d): d for d in r.legacy}
    dev = {key(d): d for d in r.rendered["dev"]["infra"] + r.rendered["dev"]["apps"]}
    errs = [f"only in legacy: {k}" for k in sorted(set(legacy) - set(dev))]
    errs += [f"only in dev stage: {k}" for k in sorted(set(dev) - set(legacy))]
    for k in sorted(set(legacy) & set(dev)):
        for path in _diff(copy.deepcopy(legacy[k]), dev[k], f"{k[0]}/{k[2]}"):
            if not any(p.match(path) for p in _LEGACY_ALLOWED):
                errs.append(f"dev stage differs from live legacy at {path}")
    return errs


CHECKS = [check_stage_params, check_rendered, check_legacy_equivalence]


def run(root: Path = REPO_ROOT, kustomize: str | None = None) -> dict[str, list[str]]:
    r = Repo(root, kustomize or os.environ.get("KUSTOMIZE", "kustomize"))
    return {c.__name__: c(r) for c in CHECKS}


def main() -> int:
    results = run()
    failed = 0
    for name, errs in results.items():
        print(f"{'FAIL' if errs else 'ok  '} {name}")
        for e in errs:
            print(f"       - {e}")
        failed += bool(errs)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
