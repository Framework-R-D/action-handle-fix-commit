"""Verify that coupled CodeQL actions stay on one immutable release."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


def references_from_yaml(content: str) -> list[tuple[str, str]]:
    """Read every CodeQL action reference from parsed workflow steps."""
    workflow = yaml.safe_load(content) or {}
    references = []
    for job in workflow.get("jobs", {}).values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []):
            if isinstance(step, dict):
                uses = step.get("uses", "")
                if isinstance(uses, str) and uses.startswith("github/codeql-action/"):
                    name, separator, ref = uses.removeprefix("github/codeql-action/").partition(
                        "@"
                    )
                    references.append((name, ref if separator else ""))
    return references


class CodeQLPinTests(unittest.TestCase):
    """Reject independent component updates and mutable CodeQL references."""

    def test_one_immutable_release(self) -> None:
        """Require init, analyze, and any other CodeQL actions to share a commit."""
        directory = Path(__file__).resolve().parents[1] / ".github/workflows"
        files = [*directory.glob("*.yaml"), *directory.glob("*.yml")]
        references = [
            reference for path in files for reference in references_from_yaml(path.read_text())
        ]
        self.assert_valid_pins(references)

    def test_mutable_flow_mapping_reference_is_rejected(self) -> None:
        """Parse flow-style steps so added mutable actions cannot evade the check."""
        content = (
            "jobs:\n  codeql:\n    steps:\n"
            "      - uses: github/codeql-action/init@" + "a" * 40 + "\n"
            "      - uses: github/codeql-action/analyze@" + "a" * 40 + "\n"
            '      - { uses: "github/codeql-action/upload-sarif@v4" }\n'
        )
        with self.assertRaises(AssertionError):
            self.assert_valid_pins(references_from_yaml(content))

    def test_mutable_folded_reference_is_rejected(self) -> None:
        """Check folded YAML scalars after parsing their resolved action value."""
        content = (
            "jobs:\n  codeql:\n    steps:\n"
            "      - uses: github/codeql-action/init@" + "a" * 40 + "\n"
            "      - uses: github/codeql-action/analyze@" + "a" * 40 + "\n"
            "      - uses: >-\n          github/codeql-action/upload-sarif@v4\n"
        )
        with self.assertRaises(AssertionError):
            self.assert_valid_pins(references_from_yaml(content))

    def assert_valid_pins(self, references: list[tuple[str, str]]) -> None:
        """Require core actions and a shared immutable commit for all CodeQL steps."""
        self.assertTrue({"init", "analyze"} <= {name for name, _ in references})
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for _, ref in references))
        self.assertEqual(len({ref for _, ref in references}), 1)
