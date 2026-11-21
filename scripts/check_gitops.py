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
KARGO_API = "kargo.akuity.io/v1alpha1"
LAYERS = ("infra", "apps")
# Steps that must be byte-identical in every PromotionTask (ADR 0002):
# everything that determines WHAT gets rendered.
RENDER_STEP_USES = ("git-clone", "git-clear", "kustomize-set-image", "kustomize-build", "git-commit")
PROTECT = "Prune=false,Delete=false"
# Kargo's operational actions (refresh, reverify, abort, alias) are
# annotation patches on exactly these resources (ADR 0007).
ONCALL_PATCHABLE = {"freights", "promotions", "stages", "warehouses"}


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
        self.argocd = load_dir(root / "hub/argocd")
        self.kargo = load_dir(root / "hub/kargo")
        self.appset = one(self.argocd, "ApplicationSet")
        self.projects = {p["metadata"]["name"]: p for p in by_kind(self.argocd, "AppProject")}
        self.kargo_project = one(self.kargo, "Project")
        self.warehouse = one(self.kargo, "Warehouse")
        self.stages = {s["metadata"]["name"]: s for s in by_kind(self.kargo, "Stage")}
        self.tasks = {t["metadata"]["name"]: t for t in by_kind(self.kargo, "PromotionTask")}
        self.templates = {t["metadata"]["name"]: t for t in by_kind(self.kargo, "AnalysisTemplate")}
        git_subs = [s["git"] for s in self.warehouse["spec"]["subscriptions"] if "git" in s]
        self.repo_url = git_subs[0]["repoURL"] if git_subs else None
        self.legacy = None
        if (root / "apps/kustomization.yaml").exists() and (root / "infra").is_dir():
            self.legacy = kustomize_build(kustomize, root / "apps") + load_dir(root / "infra", recurse=True)

    # ---- derived facts
    def appset_elements(self) -> list[dict]:
        stage_gen = self.appset["spec"]["generators"][0]["matrix"]["generators"][0]
        return stage_gen["list"]["elements"]

    def appset_layers(self) -> list[str]:
        layer_gen = self.appset["spec"]["generators"][0]["matrix"]["generators"][1]
        return [e["layer"] for e in layer_gen["list"]["elements"]]

    def adopted(self) -> set[str]:
        """Stages whose Argo CD Applications exist (ApplicationSet element enabled)."""
        return {e["stage"] for e in self.appset_elements()}

    def cluster_of(self, stage: str) -> str | None:
        for e in self.appset_elements():
            if e["stage"] == stage:
                return e["cluster"]
        names = {d.get("name") for d in self.projects.get(f"pantry-{stage}", {}).get("spec", {}).get("destinations", [])}
        return names.pop() if len(names) == 1 else None

    def task_of(self, stage: str) -> dict | None:
        steps = self.stages[stage]["spec"]["promotionTemplate"]["spec"]["steps"]
        names = [s["task"]["name"] for s in steps if "task" in s]
        return self.tasks.get(names[0]) if len(names) == 1 else None

    def base_images(self) -> set[str]:
        imgs = set()
        for s in self.stage_names:
            for d in self.rendered[s]["apps"]:
                for c in _containers(d):
                    imgs.add(c["image"])
        return imgs

    def warehouse_images(self) -> set[str]:
        return {s["image"]["repoURL"] for s in self.warehouse["spec"]["subscriptions"] if "image" in s}


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


def check_isolation(r: Repo) -> list[str]:
    """Stages sharing a cluster (and its Key Vault) must not collide (ADR 0001)."""
    errs = []
    by_cluster: dict[str, list[str]] = {}
    for s in r.stage_names:
        c = r.cluster_of(s)
        if c is None:
            errs.append(f"{s}: cannot determine cluster (ApplicationSet element or single-cluster AppProject)")
            continue
        by_cluster.setdefault(c, []).append(s)
    for c, stages in by_cluster.items():
        for i, a in enumerate(stages):
            for b in stages[i + 1:]:
                pa, pb = r.params[a], r.params[b]
                shared = {pa["appNamespace"], pa["dbNamespace"]} & {pb["appNamespace"], pb["dbNamespace"]}
                if shared:
                    errs.append(f"{a}/{b} share cluster {c!r} and namespaces {sorted(shared)}")
                for key in ("kvDbPasswordKey", "kvMcpTokensKey"):
                    if pa[key] == pb[key]:
                        errs.append(f"{a}/{b} share cluster {c!r} (one Key Vault) and {key} {pa[key]!r}")
    hosts = [r.params[s]["host"] for s in r.stage_names]
    if len(set(hosts)) != len(hosts):
        errs.append(f"stage hosts must be unique: {hosts}")
    return errs


def check_argocd(r: Repo) -> list[str]:
    """ApplicationSet + AppProjects match the stages and ADR 0005's rules."""
    errs = []
    a = r.appset
    if a is None:
        return ["hub/argocd: no ApplicationSet"]
    tmpl = a["spec"]["template"]
    project = r.kargo_project["metadata"]["name"]
    if not a["spec"].get("syncPolicy", {}).get("preserveResourcesOnDeletion"):
        errs.append("ApplicationSet: syncPolicy.preserveResourcesOnDeletion must be true (ADR 0005 §5)")
    if tmpl["metadata"].get("finalizers"):
        errs.append("ApplicationSet template: no finalizers; deleting an Application must not cascade (ADR 0005 §5)")
    want = {
        "annotation": (tmpl["metadata"].get("annotations", {}).get("kargo.akuity.io/authorized-stage"),
                       f"{project}:{{{{.stage}}}}"),
        "name": (tmpl["metadata"].get("name"), "pantry-{{.layer}}-{{.stage}}"),
        "project": (tmpl["spec"].get("project"), "pantry-{{.stage}}"),
        "targetRevision": (tmpl["spec"]["source"].get("targetRevision"), "stage/{{.stage}}"),
        "path": (tmpl["spec"]["source"].get("path"), "{{.layer}}"),
        "repoURL": (tmpl["spec"]["source"].get("repoURL"), r.repo_url),
    }
    for what, (got, exp) in want.items():
        if got != exp:
            errs.append(f"ApplicationSet template {what}: {got!r} != {exp!r}")
    if "CreateNamespace=true" in tmpl["spec"].get("syncPolicy", {}).get("syncOptions", []):
        errs.append("ApplicationSet: no CreateNamespace; the infra layer owns namespaces")
    if sorted(r.appset_layers()) != sorted(LAYERS):
        errs.append(f"ApplicationSet layers {r.appset_layers()} != {list(LAYERS)}")
    for e in r.appset_elements():
        s = e["stage"]
        if s not in r.params:
            errs.append(f"ApplicationSet element {s!r} has no stages/{s}/params")
            continue
        for key in ("appNamespace", "dbNamespace"):
            if e.get(key) != r.params[s][key]:
                errs.append(f"ApplicationSet element {s}: {key} {e.get(key)!r} != params {r.params[s][key]!r}")
    delivery = r.projects.get("pantry-delivery")
    if delivery is None:
        errs.append("no AppProject pantry-delivery for hub/kargo")
    else:
        allowed = {(w["group"], w["kind"]) for w in delivery["spec"].get("namespaceResourceWhitelist", []) +
                   delivery["spec"].get("clusterResourceWhitelist", [])}
        for d in r.kargo:
            gk = (d["apiVersion"].split("/")[0] if "/" in d["apiVersion"] else "", d["kind"])
            if gk not in allowed:
                errs.append(f"hub/kargo {gk} not allowed by AppProject pantry-delivery")
    subs = tmpl["metadata"].get("annotations", {})
    for trigger in ("on-deployed", "on-sync-failed", "on-health-degraded"):
        if not any(k.startswith(f"notifications.argoproj.io/subscribe.{trigger}.") for k in subs):
            errs.append(f"ApplicationSet template: no notification subscription for {trigger} (KARGO-22)")
    for s in r.stage_names:
        proj = r.projects.get(f"pantry-{s}")
        if proj is None:
            errs.append(f"{s}: no AppProject pantry-{s}")
            continue
        spec = proj["spec"]
        if spec.get("sourceRepos") != [r.repo_url]:
            errs.append(f"AppProject pantry-{s}: sourceRepos must be exactly [{r.repo_url}]")
        dests = {(d.get("name"), d.get("namespace")) for d in spec.get("destinations", [])}
        c = r.cluster_of(s)
        want_dests = {(c, r.params[s]["appNamespace"]), (c, r.params[s]["dbNamespace"])}
        if dests != want_dests:
            errs.append(f"AppProject pantry-{s}: destinations {sorted(dests)} != {sorted(want_dests)}")
        ns_kinds = {(w["group"], w["kind"]) for w in spec.get("namespaceResourceWhitelist", [])}
        cl_kinds = {(w["group"], w["kind"]) for w in spec.get("clusterResourceWhitelist", [])}
        for layer in LAYERS:
            for d in r.rendered[s][layer]:
                gk = (d["apiVersion"].split("/")[0] if "/" in d["apiVersion"] else "", d["kind"])
                ns = d["metadata"].get("namespace")
                if ns is None:
                    if gk not in cl_kinds:
                        errs.append(f"{s}: cluster-scoped {gk} not allowed by AppProject pantry-{s}")
                else:
                    if gk not in ns_kinds:
                        errs.append(f"{s}: {gk} not in AppProject pantry-{s} namespaceResourceWhitelist")
                    if ns not in {n for _, n in dests}:
                        errs.append(f"{s}: {d['kind']}/{d['metadata']['name']} in namespace {ns!r} "
                                    f"outside AppProject pantry-{s}")
    return errs


def _resolve(value, vars_: dict):
    """Resolve a bare `${{ vars.<name> }}` reference against a task's vars."""
    m = re.fullmatch(r"\$\{\{\s*vars\.(\w+)\s*\}\}", value or "")
    return vars_.get(m.group(1)) if m else value


def _step(task: dict, uses: str) -> list[dict]:
    return [s for s in task["spec"]["steps"] if s.get("uses") == uses]


def check_kargo(r: Repo) -> list[str]:
    """Kargo pipeline is complete, consistent with Argo CD, and follows ADR 0002/0003/0005."""
    errs = []
    project = r.kargo_project["metadata"]["name"]
    for d in r.kargo:
        if d["kind"] != "Project" and d["metadata"].get("namespace") != project:
            errs.append(f"{d['kind']}/{d['metadata']['name']} must live in namespace {project!r}")
    pc = one(r.kargo, "ProjectConfig")
    if pc is None or pc["metadata"]["name"] != project:
        errs.append(f"ProjectConfig must exist and be named {project!r}")
    # Warehouse
    if r.repo_url is None:
        errs.append("Warehouse: needs a git subscription to this repo (ADR 0002)")
    else:
        git = next(s["git"] for s in r.warehouse["spec"]["subscriptions"] if "git" in s)
        inc = git.get("includePaths", [])
        if git.get("branch") != "main" or sorted(inc) != ["base/", "stages/"]:
            errs.append("Warehouse git subscription: branch main, includePaths exactly [base/, stages/] "
                        "(hub/ changes must not create Freight)")
    # Kargo CRD fields that have a server-side default but are REQUIRED by the
    # schema (strict offline validation applies no defaults, and Kargo 1.6
    # rejects their absence). Always set explicitly. strictSemvers only makes
    # sense with SemVer selection; elsewhere it must be false (tags like
    # dev-<sha> aren't semvers and would otherwise be filtered out).
    for sub in (s["image"] for s in r.warehouse["spec"]["subscriptions"] if "image" in s):
        repo = sub.get("repoURL")
        for field in ("imageSelectionStrategy", "strictSemvers", "discoveryLimit"):
            if field not in sub:
                errs.append(f"Warehouse image {repo}: {field} must be set explicitly (required in Kargo 1.6)")
        if "strictSemvers" in sub and not isinstance(sub["strictSemvers"], bool):
            errs.append(f"Warehouse image {repo}: strictSemvers must be a boolean")
        elif sub.get("strictSemvers") is True and sub.get("imageSelectionStrategy") != "SemVer":
            errs.append(f"Warehouse image {repo}: strictSemvers: true only with imageSelectionStrategy SemVer")
    if r.warehouse_images() != r.base_images():
        errs.append(f"Warehouse images {sorted(r.warehouse_images())} != images deployed by base "
                    f"{sorted(r.base_images())}")
    # Stages
    if set(r.stages) != set(r.stage_names):
        errs.append(f"Kargo Stages {sorted(r.stages)} != stages/ dirs {r.stage_names}")
    wh = r.warehouse["metadata"]["name"]
    for name, st in r.stages.items():
        for rf in st["spec"]["requestedFreight"]:
            if rf["origin"] != {"kind": "Warehouse", "name": wh}:
                errs.append(f"Stage {name}: origin must be Warehouse {wh!r}")
            for up in rf["sources"].get("stages", []):
                if up not in r.stages:
                    errs.append(f"Stage {name}: unknown upstream stage {up!r}")
        if r.task_of(name) is None:
            errs.append(f"Stage {name}: must run exactly one known PromotionTask")
    if "prod" in r.stages:
        rf = r.stages["prod"]["spec"]["requestedFreight"][0]["sources"]
        if rf.get("direct"):
            errs.append("Stage prod: must never take Freight directly from the Warehouse")
        t = r.task_of("prod")
        if t is not None and not (_step(t, "git-open-pr") and _step(t, "git-wait-for-pr")):
            errs.append("Stage prod: its task must open a PR and wait for the merge (ADR 0007)")
    # Tasks
    want_images = r.warehouse_images()
    ref = None
    for tname, t in r.tasks.items():
        vars_ = {v["name"]: v.get("value") for v in t["spec"].get("vars", [])}
        if vars_.get("repoURL") != r.repo_url:
            errs.append(f"PromotionTask {tname}: var repoURL != {r.repo_url}")
        ksi = _step(t, "kustomize-set-image")
        if len(ksi) != 1 or ksi[0]["config"]["path"] != "./src/stages/${{ ctx.stage }}/apps":
            errs.append(f"PromotionTask {tname}: exactly one kustomize-set-image on ./src/stages/${{{{ ctx.stage }}}}/apps")
        else:
            imgs = ksi[0]["config"]["images"]
            if {i["image"] for i in imgs} != want_images:
                errs.append(f"PromotionTask {tname}: kustomize-set-image images != Warehouse images")
            for i in imgs:
                if i.get("digest") != f'${{{{ imageFrom("{i["image"]}").Digest }}}}' or "tag" in i:
                    errs.append(f"PromotionTask {tname}: {i['image']} must be pinned by its own digest (ADR 0003)")
        builds = [(b["config"]["path"], b["config"]["outPath"]) for b in _step(t, "kustomize-build")]
        want_builds = [(f"./src/stages/${{{{ ctx.stage }}}}/{layer}", f"./out/{layer}/manifests.yaml") for layer in LAYERS]
        if builds != want_builds:
            errs.append(f"PromotionTask {tname}: kustomize-build steps must render {list(LAYERS)} to ./out/<layer>/")
        updates = _step(t, "argocd-update")
        names = [a["name"] for u in updates for a in u["config"]["apps"]]
        if names != [f"pantry-{layer}-${{{{ ctx.stage }}}}" for layer in LAYERS] or \
                [len(u["config"]["apps"]) for u in updates] != [1, 1]:
            errs.append(f"PromotionTask {tname}: argocd-update must sync infra then apps, one step each (ADR 0005 §4)")
        for u in updates:
            for a in u["config"]["apps"]:
                for src in a.get("sources", []):
                    if src.get("updateTargetRevision"):
                        errs.append(f"PromotionTask {tname}: updateTargetRevision forbidden (ADR 0005 §2)")
                    if not src.get("desiredRevision") or _resolve(src.get("repoURL"), vars_) != r.repo_url:
                        errs.append(f"PromotionTask {tname}: argocd-update needs desiredRevision + repoURL {r.repo_url}")
        render = [s for s in t["spec"]["steps"] if s.get("uses") in RENDER_STEP_USES]
        if ref is None:
            ref = (tname, render)
        elif render != ref[1]:
            errs.append(f"PromotionTask {tname}: render steps drifted from {ref[0]} (ADR 0002)")
    return errs


def _pod_spec(template: dict) -> dict:
    return template["spec"]["metrics"][0]["provider"]["job"]["spec"]["template"]["spec"]


def check_verification(r: Repo) -> list[str]:
    """Every stage is verified; the stage feeding prod runs the contract; prod soaks (M4)."""
    errs = []
    for tname, tmpl in r.templates.items():
        for m in tmpl["spec"]["metrics"]:
            pod = m["provider"]["job"]["spec"]["template"]["spec"]
            if not pod.get("securityContext", {}).get("runAsNonRoot"):
                errs.append(f"AnalysisTemplate {tname}: pod must runAsNonRoot")
            for c in pod["containers"]:
                img = c["image"]
                tag = img.rsplit(":", 1)[1] if ":" in img.split("/")[-1] else None
                if "@sha256:" not in img and (tag is None or tag == "latest"):
                    errs.append(f"AnalysisTemplate {tname}: image {img!r} must be pinned (version tag or digest)")
                sc = c.get("securityContext", {})
                if sc.get("allowPrivilegeEscalation") is not False or not sc.get("readOnlyRootFilesystem"):
                    errs.append(f"AnalysisTemplate {tname}/{c['name']}: no privilege escalation, read-only root fs")
    feeds_prod = {u for rf in r.stages.get("prod", {}).get("spec", {}).get("requestedFreight", [])
                  for u in rf["sources"].get("stages", [])}
    for name, st in r.stages.items():
        ver = st["spec"].get("verification")
        if not ver:
            errs.append(f"Stage {name}: no verification (KARGO-19)")
            continue
        used = [a["name"] for a in ver.get("analysisTemplates", [])]
        for u in used:
            if u not in r.templates:
                errs.append(f"Stage {name}: unknown AnalysisTemplate {u!r}")
        if "pantry-smoke" not in used:
            errs.append(f"Stage {name}: must run pantry-smoke")
        if name in feeds_prod and "pantry-api-contract" not in used:
            errs.append(f"Stage {name}: feeds prod, so it must run pantry-api-contract (KARGO-20)")
        args = {a["name"]: a.get("value") for a in ver.get("args", [])}
        for u in used:
            for a in r.templates.get(u, {}).get("spec", {}).get("args", []):
                if "value" not in a and a["name"] not in args:
                    errs.append(f"Stage {name}: AnalysisTemplate {u} needs arg {a['name']!r}")
        if name in r.params and args.get("host") != r.params[name]["host"]:
            errs.append(f"Stage {name}: verification host {args.get('host')!r} != "
                        f"stages/{name}/params host {r.params[name]['host']!r}")
    # Once provenance is enforced anywhere, every entry point from the
    # Warehouse must enforce it, or unsigned Freight walks in the side door.
    uses = {n: {a["name"] for a in (s["spec"].get("verification") or {}).get("analysisTemplates", [])}
            for n, s in r.stages.items()}
    if any("pantry-provenance" in u for u in uses.values()):
        for n, s in r.stages.items():
            direct = any(rf["sources"].get("direct") for rf in s["spec"]["requestedFreight"])
            if direct and "pantry-provenance" not in uses[n]:
                errs.append(f"Stage {n}: takes Freight directly from the Warehouse, so it must run "
                            "pantry-provenance (KARGO-28)")
    if "prod" in r.stages:
        for rf in r.stages["prod"]["spec"]["requestedFreight"]:
            if not rf["sources"].get("requiredSoakTime"):
                errs.append("Stage prod: Freight must soak upstream first (requiredSoakTime, KARGO-21)")
    return errs


def check_cutover_state(r: Repo) -> list[str]:
    """Stages not yet adopted by the hub can't be promoted into, so don't automate them."""
    errs = []
    adopted = r.adopted()
    pending = set(r.stage_names) - adopted
    pc = one(r.kargo, "ProjectConfig") or {"spec": {}}
    for pol in pc["spec"].get("promotionPolicies", []):
        st = pol.get("stageSelector", {}).get("name") or pol.get("stage")
        if st in pending and pol.get("autoPromotionEnabled"):
            errs.append(f"auto-promotion enabled for {st!r}, whose Applications don't exist yet "
                        "(enable its ApplicationSet element first: KARGO-23)")
    for name, st in r.stages.items():
        srcs = st["spec"]["requestedFreight"][0]["sources"]
        ups = srcs.get("stages", [])
        if ups and any(u in pending for u in ups) and not srcs.get("direct"):
            errs.append(f"Stage {name}: upstream {ups} not adopted yet, so it needs the interim direct: true")
        if ups and not any(u in pending for u in ups) and srcs.get("direct"):
            errs.append(f"Stage {name}: upstream {ups} is adopted; remove the interim direct: true")
    return errs


def check_rbac(r: Repo) -> list[str]:
    """Only Pantry.ProdApprover can promote to prod; nobody edits the pipeline by hand (ADR 0007)."""
    errs = []
    sas = {d["metadata"]["name"]: d for d in by_kind(r.kargo, "ServiceAccount")}
    roles = {d["metadata"]["name"]: d for d in by_kind(r.kargo, "Role")}
    for rb in by_kind(r.kargo, "RoleBinding"):
        role = roles.get(rb["roleRef"]["name"])
        if role is None:
            errs.append(f"RoleBinding {rb['metadata']['name']}: unknown Role")
            continue
        claims = set()
        for subj in rb["subjects"]:
            sa = sas.get(subj["name"])
            claims |= set((ann(sa, "rbac.kargo.akuity.io/claim.roles") or "").split(",")) if sa else set()
        for rule in role["rules"]:
            verbs = set(rule["verbs"])
            if "kargo.akuity.io" in rule["apiGroups"]:
                if verbs & {"create", "update", "delete", "deletecollection", "*"}:
                    errs.append(f"Role {role['metadata']['name']}: no write verbs on Kargo resources (pipeline is GitOps)")
                if "patch" in verbs and (claims != {"Pantry.Oncall"} or
                                         not set(rule["resources"]) <= ONCALL_PATCHABLE):
                    errs.append(f"Role {role['metadata']['name']}: `patch` on Kargo resources is only for "
                                f"Pantry.Oncall, on {sorted(ONCALL_PATCHABLE)} (refresh/reverify/abort)")
            if "promote" in verbs:
                names = set(rule.get("resourceNames", []))
                if not names:
                    errs.append(f"Role {role['metadata']['name']}: promote must be scoped by resourceNames")
                if "prod" in names and claims != {"Pantry.ProdApprover"}:
                    errs.append(f"Role {role['metadata']['name']}: prod promotion bound to {sorted(claims)}, "
                                "only Pantry.ProdApprover allowed")
    return errs


def check_credentials(r: Repo) -> list[str]:
    errs = []
    creds = [e for e in by_kind(r.kargo, "ExternalSecret")
             if e["spec"]["target"].get("template", {}).get("metadata", {}).get("labels", {})
             .get("kargo.akuity.io/cred-type") == "git"]
    if len(creds) != 1 or creds[0]["spec"]["target"]["template"]["data"].get("repoURL") != r.repo_url:
        errs.append(f"exactly one git credential (ExternalSecret, cred-type git) for {r.repo_url}")
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


CHECKS = [check_stage_params, check_rendered, check_isolation, check_argocd, check_kargo,
          check_verification, check_cutover_state, check_rbac, check_credentials, check_legacy_equivalence]


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
