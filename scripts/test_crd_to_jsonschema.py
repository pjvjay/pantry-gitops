"""Tests for crd_to_jsonschema.

Run: python3 -m unittest discover -s scripts
"""
import json
import tempfile
import unittest
from pathlib import Path

from crd_to_jsonschema import convert, strictify

CHART_CRD = """\
{{- if .Values.crds.install }}
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: widgets.example.io
spec:
  group: example.io
  names:
    kind: Widget
  scope: Namespaced
  versions:
    - name: v1alpha1
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              # Like Kargo's strictSemvers: defaulted server-side, yet required.
              required: [mode]
              properties:
                mode:
                  type: boolean
                  default: true
                size:
                  x-kubernetes-int-or-string: true
                note:
                  type: string
                  nullable: true
                config:
                  type: object
                  x-kubernetes-preserve-unknown-fields: true
                labels:
                  type: object
                  additionalProperties:
                    type: string
{{- end }}
"""


class TestStrictify(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "chart/templates").mkdir(parents=True)
        (self.tmp / "chart/templates/widget.yaml").write_text(CHART_CRD)
        (self.tmp / "chart/templates/deploy.yaml").write_text("kind: {{ .Values.kind }}\n  bad: [yaml")
        self.written = convert([self.tmp / "chart"], self.tmp / "out")
        self.schema = json.loads((self.tmp / "out/example.io/widget_v1alpha1.json").read_text())

    def test_helm_templated_crd_is_found_and_laid_out_for_kubeconform(self):
        self.assertEqual([p.relative_to(self.tmp / "out").as_posix() for p in self.written],
                         ["example.io/widget_v1alpha1.json"])

    def test_unknown_fields_rejected_where_properties_are_declared(self):
        self.assertIs(self.schema["additionalProperties"], False)
        self.assertIs(self.schema["properties"]["spec"]["additionalProperties"], False)

    def test_free_form_objects_stay_open(self):
        spec = self.schema["properties"]["spec"]["properties"]
        self.assertNotIn("additionalProperties", spec["config"])           # preserve-unknown-fields
        self.assertEqual(spec["labels"]["additionalProperties"], {"type": "string"})  # a map stays a map

    def test_kubernetes_extensions_become_json_schema(self):
        spec = self.schema["properties"]["spec"]["properties"]
        self.assertEqual(spec["size"], {"anyOf": [{"type": "integer"}, {"type": "string"}]})
        self.assertEqual(spec["note"]["type"], ["string", "null"])

    def test_required_fields_with_defaults_stay_required(self):
        # Offline validation applies no defaults, so a defaulted-but-required
        # field (Kargo's strictSemvers) must still be reported when omitted.
        self.assertEqual(self.schema["properties"]["spec"]["required"], ["mode"])

    def test_strictify_does_not_mutate_input(self):
        src = {"type": "object", "properties": {"a": {"type": "string"}}}
        strictify(src)
        self.assertNotIn("additionalProperties", src)


if __name__ == "__main__":
    unittest.main()
