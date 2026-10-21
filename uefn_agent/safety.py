"""Pre-flight validators for asset operations.

These encode failure modes discovered empirically in UEFN 41.10. Every rule
here corresponds to a real incident that silently corrupted assets or broke a
Verse build.
"""

from __future__ import annotations

import re
from pathlib import Path


class SafetyViolation(ValueError):
    """A requested operation would corrupt assets or break the Verse build."""


def check_patch_lengths(old: str, new: str) -> None:
    """Byte-patching .uasset string references only works with EQUAL-length
    ASCII strings.

    The uasset format stores serialized string lengths; replacing a name with
    one of a different byte length shifts every offset after it and corrupts
    the package. Non-ASCII names are rejected outright: they may serialize as
    UTF-16 in the name table, where an ASCII-level patch is meaningless.
    """
    for label, s in (("old", old), ("new", new)):
        if not s:
            raise SafetyViolation(f"Byte-patch {label} string must not be empty.")
        try:
            s.encode("ascii")
        except UnicodeEncodeError as e:
            raise SafetyViolation(
                f"Byte-patch {label} string {s!r} contains non-ASCII "
                f"characters - only plain-ASCII asset names can be patched."
            ) from e
    if len(old) != len(new):
        raise SafetyViolation(
            f"Byte-patch strings must be the same length: '{old}' "
            f"({len(old)} bytes) vs '{new}' ({len(new)} bytes). Pad the new "
            f"name with leading zeros in its digit run (e.g. first_099, "
            f"FoodPack_001) or pick an equal-length name."
        )


def check_fname_digit_split(name: str, role: str = "target") -> None:
    """Reject patch names that Unreal's FName system will mangle.

    FNames with a trailing digit run WITHOUT a leading zero (e.g. 'first_100')
    serialize as base+number, not as a literal string. A byte-patched literal
    'first_100' therefore creates a DIFFERENT FName than the editor would, and
    components referencing it are silently dropped on load. Names whose digit
    run starts with '0' ('first_099', 'first0100', 'gltf_0001') or that don't
    end in digits serialize literally and are safe.
    """
    m = re.search(r"(\d+)$", name)
    if m and not m.group(1).startswith("0"):
        detail = (
            "the byte-patched literal will NOT match and components silently "
            "drop on load"
            if role == "target"
            else "the literal string does not exist in the asset bytes, so "
            "the patch silently changes nothing"
        )
        raise SafetyViolation(
            f"Unsafe patch {role} name '{name}': trailing digit run "
            f"'{m.group(1)}' has no leading zero, so Unreal serializes it as "
            f"base+number and {detail}. Use a leading zero (e.g. "
            f"'{name[:m.start(1)]}0{m.group(1)}') or a non-digit suffix."
        )


def find_verse_references(content_dir: str | Path, name: str) -> list[str]:
    """Find .verse files that reference `name` (asset classes are exposed to
    Verse by name - renaming the asset breaks these at the next build).

    Returns 'relative\\path.verse:lineno: line' strings.
    """
    root = Path(content_dir)
    if not root.is_dir() or not name.strip():
        return []
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    hits: list[str] = []
    for f in root.rglob("*.verse"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{f.relative_to(root)}:{i}: {line.strip()[:120]}")
    return hits


def validate_patch(old: str, new: str, content_dir: str | Path | None = None) -> list[str]:
    """Run all byte-patch validators. Raises SafetyViolation on hard failures;
    returns non-fatal warnings (e.g. Verse files that reference the old name).
    """
    check_patch_lengths(old, new)
    check_fname_digit_split(new, role="target")
    check_fname_digit_split(old, role="source")
    warnings: list[str] = []
    if content_dir:
        refs = find_verse_references(content_dir, old)
        if refs:
            warnings.append(
                f"'{old}' is referenced in {len(refs)} Verse location(s) - "
                "these must be updated or the next Verse build fails:\n"
                + "\n".join(refs[:20])
            )
    return warnings
