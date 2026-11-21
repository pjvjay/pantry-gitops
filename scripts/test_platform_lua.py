"""Tests for the Argo CD Lua health checks in hub/platform/.

The CNPG Backup check decides whether the prod pre-promotion backup hook
actually gates the migration (ADR 0004), so every CNPG Backup phase is
pinned down here, including the two that must NOT leave a prod promotion
hanging in Progressing.

Run: python3 -m unittest discover -s scripts   (needs `lupa`; CI installs it)
"""
import os
import unittest
from pathlib import Path

try:
    import lupa
except ImportError:  # optional locally, required in CI
    lupa = None

LUA = Path(__file__).resolve().parent.parent / "hub/platform/argocd-health-cnpg-backup.lua"


def health(obj):
    """Evaluate the script the way Argo CD does: global `obj`, returns a table."""
    L = lupa.LuaRuntime()

    def to_lua(v):
        if isinstance(v, dict):
            t = L.table()
            for k, x in v.items():
                t[k] = to_lua(x)
            return t
        return v

    L.globals().obj = to_lua(obj)
    hs = L.execute(LUA.read_text())
    return hs["status"], hs["message"]


@unittest.skipUnless(lupa or os.environ.get("CI"), "lupa not installed")
class TestCnpgBackupHealth(unittest.TestCase):
    def test_completed_is_healthy(self):
        self.assertEqual(health({"status": {"phase": "completed"}})[0], "Healthy")

    def test_failed_is_degraded_with_the_operator_error(self):
        self.assertEqual(health({"status": {"phase": "failed", "error": "object store unreachable"}}),
                         ("Degraded", "object store unreachable"))

    def test_wal_archiving_failure_is_degraded_not_a_hang(self):
        self.assertEqual(health({"status": {"phase": "walArchivingFailing"}})[0], "Degraded")

    def test_in_flight_phases_are_progressing(self):
        for phase in ("pending", "started", "running", "finalizing"):
            self.assertEqual(health({"status": {"phase": phase}})[0], "Progressing", phase)

    def test_new_object_without_status_is_progressing(self):
        self.assertEqual(health({"spec": {"cluster": {"name": "pantry"}}}), ("Progressing", "Backup pending"))


if __name__ == "__main__":
    unittest.main()
