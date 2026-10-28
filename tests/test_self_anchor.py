"""self_anchor must never overwrite bytes that a proof already covers.

The tool's only product is an integrity claim: "these exact bytes existed by this time". A run that
copies new bytes over an old body.txt leaves body.txt.ots behind as a proof of the previous content,
and the next `ots stamp` reports success for a file its proof says nothing about. That is worse than
no timestamp, because it still looks like one.

These two tests are the whole guard: different bytes must be refused, identical bytes must be a no-op.
"""

import importlib.util
import json
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "self_anchor" / "self_anchor.py"


def _module():
    spec = importlib.util.spec_from_file_location("self_anchor_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sa = _module()


def test_refuses_to_overwrite_a_different_body(tmp_path):
    out = tmp_path / "anchor"
    body = out / "body.txt"
    out.mkdir()
    body.write_bytes(b"the first body\n")
    proof = out / "body.txt.ots"
    proof.write_bytes(b"not a real proof, but it is a file that must not be left lying around")
    new = tmp_path / "second.md"
    new.write_bytes(b"a different body\n")

    with pytest.raises(SystemExit) as exc:
        sa.build(new, out, "github", "second")

    assert "different bytes" in str(exc.value)
    assert body.read_bytes() == b"the first body\n", "the anchored bytes were replaced"
    assert proof.read_bytes().startswith(b"not a real proof"), "the orphaned proof was disturbed"


def test_same_bytes_is_idempotent(tmp_path):
    out = tmp_path / "anchor"
    src = tmp_path / "post.md"
    src.write_bytes(b"one body, anchored twice\n")
    first, reused_first = sa.build(src, out, "github", "post-1")
    assert reused_first is False
    manifest_before = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    second, reused_second = sa.build(src, out, "github", "post-1")
    assert second == first and reused_second is True
    assert (out / "body.txt").read_bytes() == src.read_bytes()
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == manifest_before, \
        "a repeat run must not restate when these bytes were anchored"


def test_ots_js_env_points_stamping_at_any_install(tmp_path, monkeypatch):
    """Stamping used to hardcode one machine's ots-cli path, so nobody else could produce a proof."""
    pkg = tmp_path / "javascript-opentimestamps"
    pkg.mkdir()
    monkeypatch.setenv("OTS_JS", str(pkg))
    assert sa.resolve_ots_cli() == pkg / "ots-cli.js", "a package directory must resolve to its cli"

    cli = tmp_path / "elsewhere" / "ots-cli.js"
    monkeypatch.setenv("OTS_JS", str(cli))
    assert sa.resolve_ots_cli() == cli, "a direct path to ots-cli.js must be used as given"


def test_without_ots_js_or_a_resolvable_package_it_stops_and_says_so(tmp_path, monkeypatch):
    """No env var and nothing for Node to resolve: a stop, never a guess at some directory."""
    monkeypatch.delenv("OTS_JS", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))

    out = tmp_path / "anchor"
    out.mkdir()
    (out / "body.txt").write_bytes(b"a body that must not end up with a bogus proof\n")

    with pytest.raises(SystemExit) as exc:
        sa.stamp(out)

    assert "OTS_JS" in str(exc.value), "the error must say how to point at ots-cli"
    assert not (out / "body.txt.ots").exists(), "a failed stamp must not leave a proof file behind"
