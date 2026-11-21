#!/usr/bin/env python3
"""Convert CustomResourceDefinitions into kubeconform JSON schemas.

CI uses this to validate hub/kargo/ against the CRDs of the EXACT Kargo
chart version pinned for the hub (ADR 0006), instead of a community
catalog that may track a different version.

    python3 scripts/crd_to_jsonschema.py <dir-or-file>... <out-dir>

Writes <out-dir>/<group>/<kind>_<version>.json (kind lower-cased): the
layout kubeconform's
  -schema-location '<out-dir>/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
expects.

Strict by design: every object schema that declares `properties` (and
isn't marked x-kubernetes-preserve-unknown-fields) rejects unknown keys,
so a misspelled field fails CI instead of being silently dropped by the
API server. Helm template lines (`{{ ... }}` on their own line, common
around CRDs in charts) are stripped before parsing.

Limitation worth knowing: Kargo declares each promotion step's `config:`
as free-form JSON, so typos INSIDE a step's config can't be caught here.
Kargo validates those when the promotion runs.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

_TEMPLATE_LINE = re.compile(r"^\s*\{\{.*\}\}\s*$")


def strictify(schema):
    """Return a copy of an OpenAPI v3 (CRD) schema as strict JSON Schema."""
    if isinstance(schema, list):
        return [strictify(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strictify(v) for k, v in schema.items()}
    if out.pop("x-kubernetes-int-or-string", False):
        out.pop("type", None)
        out["anyOf"] = [{"type": "integer"}, {"type": "string"}]
    if out.pop("nullable", False) and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    if ("properties" in out and "additionalProperties" not in out
            and not out.get("x-kubernetes-preserve-unknown-fields")):
        out["additionalProperties"] = False
    return out


def crds_in(path: Path):
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.suffix in (".yaml", ".yml"))
    for f in files:
        text = "\n".join(line for line in f.read_text().splitlines() if not _TEMPLATE_LINE.match(line))
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError:
            continue  # a templated non-CRD file; not our concern
        for d in docs:
            if isinstance(d, dict) and d.get("kind") == "CustomResourceDefinition":
                yield d


def convert(sources: list[Path], out_dir: Path) -> list[Path]:
    written = []
    for crd in (c for s in sources for c in crds_in(s)):
        group = crd["spec"]["group"]
        kind = crd["spec"]["names"]["kind"].lower()
        for v in crd["spec"]["versions"]:
            schema = v.get("schema", {}).get("openAPIV3Schema")
            if not schema:
                continue
            target = out_dir / group / f"{kind}_{v['name']}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(strictify(schema), indent=1, sort_keys=True))
            written.append(target)
    return written


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    written = convert([Path(a) for a in argv[:-1]], Path(argv[-1]))
    if not written:
        print("no CustomResourceDefinitions found", file=sys.stderr)
        return 1
    for w in written:
        print(w)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
