"""uefn-agent test suite - no test framework needed: python tests/run_tests.py

Offline tests always run. Live tests (against an open UEFN editor with the
listener running) run only when UEFN_CONTENT_DIR is set and the listener
responds; otherwise they're skipped.
"""

from __future__ import annotations

import os
import re
import sys

passed: list[str] = []
failed: list[tuple[str, str]] = []
skipped: list[str] = []


def check(name, fn):
    try:
        fn()
        passed.append(name)
    except Exception as e:
        failed.append((name, f"{type(e).__name__}: {e}"))


def expect_violation(old, new):
    from uefn_agent.safety import SafetyViolation, validate_patch
    try:
        validate_patch(old, new)
    except SafetyViolation:
        return
    raise AssertionError(f"validate_patch({old!r}, {new!r}) should have raised")


from uefn_agent.safety import find_verse_references, validate_patch

check("length mismatch refused", lambda: expect_violation("abc", "abcd"))
check("digit-split target refused", lambda: expect_violation("first_013", "first_100"))
check("digit-split source refused", lambda: expect_violation("first_100", "first_099"))
check("non-ascii refused", lambda: expect_violation("Café_A", "Café_B"))
check("empty string refused", lambda: expect_violation("", ""))
check("valid swap passes", lambda: validate_patch("first_013", "first_099"))
check("blank name yields no refs", lambda: (_ for _ in ()).throw(AssertionError)
      if find_verse_references(".", " ") else None)

from uefn_agent.mcp_server import _finite, clone_and_patch_asset


def non_bmp_roundtrip():
    label = "Bookshelf \U0001f4da"
    assert eval(repr(label)) == label


def inf_rejected():
    try:
        _finite(v=float("inf"))
    except ValueError:
        return
    raise AssertionError("inf should be rejected")


check("non-BMP repr round-trip", non_bmp_roundtrip)
check("non-finite float rejected", inf_rejected)
check("None allowed in _finite", lambda: _finite(x=None, y=1.5))


def substring_collision_refused():
    r = clone_and_patch_asset("/T/A", "/T/B", {"Mesh_A": "Mesh_X", "Mesh_AB": "Mesh_XY"})
    assert r.get("created") is False and "substring" in r.get("safety_violation", ""), r


def simultaneous_replace():
    swaps = {b"Book_01": b"Book_02", b"Book_02": b"Book_03"}
    rx = re.compile(b"|".join(re.escape(o) for o in swaps))
    out = rx.sub(lambda m: swaps[m.group(0)], b"xx Book_01 yy Book_02 zz")
    assert out == b"xx Book_02 yy Book_03 zz", out


check("substring swap-key collision refused", substring_collision_refused)
check("simultaneous replace has no cascade", simultaneous_replace)


def env_guard():
    os.environ["UEFN_LISTENER_PORT"] = "not-a-number"
    try:
        from uefn_agent.bridge import _env_int
        assert _env_int("UEFN_LISTENER_PORT") is None
    finally:
        del os.environ["UEFN_LISTENER_PORT"]


check("bad numeric env var tolerated", env_guard)


def rename_fails_closed():
    saved = os.environ.pop("UEFN_CONTENT_DIR", None)
    try:
        from uefn_agent.mcp_server import rename_asset
        r = rename_asset("/T/DoesNotExist", "/T/AlsoNot")
        assert r.get("renamed") is False and "safety check" in r.get("reason", ""), r
    finally:
        if saved:
            os.environ["UEFN_CONTENT_DIR"] = saved


check("rename fails closed without UEFN_CONTENT_DIR", rename_fails_closed)


def packaged_data():
    from uefn_agent import cli
    k = cli._load_knowledge()
    assert "FName digit-split" in k and "Recipe: clone-patch-prefab" in k
    assert (cli.PKG_ROOT / "listener" / "uefn_listener.py").is_file()


check("knowledge + listener ship inside the package", packaged_data)

live_ready = bool(os.environ.get("UEFN_CONTENT_DIR"))
if live_ready:
    try:
        from uefn_agent.bridge import UefnBridge
        UefnBridge(timeout=5).status()
    except Exception:
        live_ready = False

if live_ready:
    from uefn_agent.mcp_server import list_level_actors, uefn_status

    check("live: uefn_status", lambda: uefn_status())
    check("live: actor listing", lambda: list_level_actors(""))
else:
    skipped += ["live: uefn_status", "live: actor listing"]

print(f"\nPASS {len(passed)}/{len(passed) + len(failed)}"
      + (f" (skipped {len(skipped)} live)" if skipped else ""))
for name in passed:
    print("  ok:", name)
for name in skipped:
    print("  skip:", name)
for name, err in failed:
    print("  FAIL:", name, "->", err[:200])
sys.exit(1 if failed else 0)
