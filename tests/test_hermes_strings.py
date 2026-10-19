"""The injected blocks, built by Hermes' own code at the tested commit, are removed before capture.

Skipped when Hermes is not importable (unless ``PROVENANCE_GATE_REQUIRE_E2E=1``)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from provenance_gate.capture import typed_text

TYPED = "I decided we ship on Friday"

if importlib.util.find_spec("run_agent") is None:
    if os.environ.get("PROVENANCE_GATE_REQUIRE_E2E") == "1":
        raise RuntimeError("PROVENANCE_GATE_REQUIRE_E2E=1 but Hermes (run_agent) is not importable")
    pytest.skip("Hermes is not importable; real-string tests skipped", allow_module_level=True)

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def built():
    proc = subprocess.run([sys.executable, str(Path(__file__).parent / "hermes_strings_worker.py")],
                          capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


EXPECTED = ["discord_note", "reply_own_True", "reply_own_False", "document_text/plain_True",
            "document_text/plain_False", "document_application/pdf_True", "media_notes", "vision", "voice_note",
            "skill_single", "skill_auto_load"]


@pytest.mark.parametrize("name", EXPECTED)
def test_real_injected_blocks_are_removed(built, name):
    message = built[name]
    assert message != TYPED and TYPED in message  # Hermes really added something
    assert typed_text(message) == TYPED


def test_bare_skill_invocation_stores_nothing(built):
    assert built["skill_bare"].startswith("[IMPORTANT: The user has invoked the ")
    assert typed_text(built["skill_bare"]) == ""


def test_context_reference_block_is_removed(built):
    # The @file token is what the user typed; the attached file content is not.
    assert "--- Attached Context ---" in built["context_refs"]
    assert typed_text(built["context_refs"]) == f"{TYPED} @file:notes.md"
