"""MCP server exposing UEFN editor automation as typed tools.

Wraps the in-editor Python listener (see bridge.py) and packages the recipes
proven on a real UEFN 41.10 project: clone-and-patch prefab authoring,
programmatic Verse tags, texture export for vision identification, digest
search, and safe asset renames.

Configuration (environment variables):
  UEFN_CONTENT_DIR     on-disk Content folder of the UEFN project
                       (e.g. C:\\...\\MyProject\\Content) - required for
                       byte-patch and Verse-reference tools.
  UEFN_PROJECT_MOUNT   the project's content mount, e.g. /MyProject (first
                       segment of package paths) - used for Verse tag paths.
  UEFN_DIGEST_DIR      the project's VerseProject folder holding
                       Fortnite.digest.verse etc. (e.g.
                       %LOCALAPPDATA%\\UnrealEditorFortnite\\Saved\\VerseProject\\MyProject)
  UEFN_LISTENER_PORT   listener port (default: scan 8765-8770)
  UEFN_BRIDGE_TIMEOUT  per-command HTTP timeout seconds (default 170)

Run standalone:  uefn-agent-mcp   (stdio transport)
"""

from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .bridge import BridgeError, UefnBridge
from .safety import (
    SafetyViolation,
    find_verse_references,
    validate_patch,
)

mcp = FastMCP("uefn")
_bridge = UefnBridge()


def _content_dir() -> Path:
    d = os.environ.get("UEFN_CONTENT_DIR")
    if not d or not Path(d).is_dir():
        raise RuntimeError(
            "UEFN_CONTENT_DIR is not set (or does not exist). Point it at the "
            "project's on-disk Content folder to enable disk-level tools."
        )
    return Path(d)


def _asset_file(package_path: str) -> Path:
    """Map '/MountPoint/Sub/Asset' to '<ContentDir>/Sub/Asset.uasset'."""
    parts = package_path.strip("/").split("/")
    if len(parts) < 2:
        raise ValueError(f"Not a package path: {package_path}")
    return _content_dir().joinpath(*parts[1:]).with_suffix(".uasset")


def _py(code: str) -> dict:
    res = _bridge.execute_python(code)
    return {"result": res.result, "stdout": res.stdout.strip()[-2000:]}


def _finite(**floats) -> None:
    for name, v in floats.items():
        if v is not None and not math.isfinite(v):
            raise ValueError(f"{name} must be a finite number, got {v!r}")


@mcp.tool()
def uefn_status() -> dict:
    """Check the in-editor listener: reachable, queue depth, project loaded.

    Call this first in a session, and after any timeout before retrying a
    mutating operation (queued commands keep executing after client timeouts).
    """
    st = _bridge.status()
    out = {"port": _bridge.port, "status": st}
    try:
        out["project"] = _bridge.command("get_project_info")
    except BridgeError:
        pass
    return out


@mcp.tool()
def uefn_wait_for_idle(max_wait_seconds: float = 600) -> dict:
    """Block until the editor's command queue is empty.

    Use after a timeout error: the editor keeps working through queued
    commands even though the client request died. Never blind-retry a
    mutating command - wait for idle, then re-inventory state.
    """
    return _bridge.wait_for_idle(max_wait_s=max_wait_seconds)


@mcp.tool()
def uefn_execute_python(code: str) -> dict:
    """Run arbitrary Python inside the UEFN editor (escape hatch).

    Pre-populated globals: `unreal`, `actor_sub` (EditorActorSubsystem),
    `asset_sub` (EditorAssetSubsystem), `level_sub` (LevelEditorSubsystem).
    Assign to `result` to return a value (must be JSON-serializable).

    RULES (violations have crashed the editor):
    - ONE heavy op (asset duplicate/save/compile) per call - each Blueprint
      save/compile takes 3-8s and stacking them blows the command timeout.
    - Summarize large data in-editor; never return full actor/asset dumps.
    - Python is editor-time only; gameplay logic must stay in Verse.
    """
    return _py(code)


@mcp.tool()
def list_level_actors(label_filter: str = "", with_tags: bool = False) -> list[dict]:
    """List level actors (label, class, location), optionally filtered by a
    case-insensitive substring of the actor label. Prefer a filter - big
    levels have hundreds of actors.

    with_tags=True also reads each actor's Verse gameplay tags (from its
    VerseTagMarkupComponent, if present) - use for tag census/verification.
    """
    code = f"""
flt = {label_filter.lower()!r}
with_tags = {with_tags!r}
out = []
for a in actor_sub.get_all_level_actors():
    lbl = a.get_actor_label()
    if flt and flt not in lbl.lower():
        continue
    loc = a.get_actor_location()
    row = {{"label": lbl, "class": a.get_class().get_name(),
            "x": round(loc.x, 1), "y": round(loc.y, 1), "z": round(loc.z, 1)}}
    if with_tags:
        tags = []
        comp = next((c for c in a.get_components_by_class(unreal.ActorComponent)
                     if c.get_class().get_name() == "VerseTagMarkupComponent"), None)
        if comp is not None:
            try:
                cont = comp.get_editor_property("internal_tags")
                for ti in cont.get_editor_property("internal_tags"):
                    tag_obj = ti.get_editor_property("internal_tag")
                    if tag_obj:
                        tags.append(tag_obj.get_name())
            except Exception:
                tags = ["<unreadable>"]
        row["tags"] = tags
    out.append(row)
result = out
"""
    return _py(code)["result"]


@mcp.tool()
def duplicate_level_actor(
    source_label: str,
    new_label: str,
    offset_x: float = 0,
    offset_y: float = 0,
    offset_z: float = 0,
) -> dict:
    """Duplicate an existing level actor (clone-don't-create).

    This is THE way to place props programmatically: duplication carries
    instance-added components that fresh spawns lack - critically
    VerseTagMarkupComponent, so the clone stays taggable. duplicate_actor is
    fast (~0.1s, no per-op save); save the level once at the end with
    save_dirty_packages.
    """
    _finite(offset_x=offset_x, offset_y=offset_y, offset_z=offset_z)
    code = f"""
src_label = {source_label!r}
src = next((a for a in actor_sub.get_all_level_actors()
            if a.get_actor_label() == src_label), None)
if src is None:
    raise RuntimeError("source actor not found: " + src_label)
dup = actor_sub.duplicate_actor(src, None, unreal.Vector({offset_x}, {offset_y}, {offset_z}))
dup.set_actor_label({new_label!r})
loc = dup.get_actor_location()
result = {{"label": dup.get_actor_label(), "x": loc.x, "y": loc.y, "z": loc.z}}
"""
    return _py(code)["result"]


@mcp.tool()
def set_actor_transform(
    label: str,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    yaw: float | None = None,
    scale_x: float | None = None,
    scale_y: float | None = None,
    scale_z: float | None = None,
) -> dict:
    """Set an actor's location / yaw / scale (only the fields you pass)."""
    _finite(x=x, y=y, z=z, yaw=yaw,
            scale_x=scale_x, scale_y=scale_y, scale_z=scale_z)
    code = f"""
lbl = {label!r}
a = next((a for a in actor_sub.get_all_level_actors()
          if a.get_actor_label() == lbl), None)
if a is None:
    raise RuntimeError("actor not found: " + lbl)
loc = a.get_actor_location()
new_loc = unreal.Vector(
    {x if x is not None else 'loc.x'},
    {y if y is not None else 'loc.y'},
    {z if z is not None else 'loc.z'})
a.set_actor_location(new_loc, False, False)
if {(yaw is not None)!r}:
    rot = a.get_actor_rotation()
    a.set_actor_rotation(unreal.Rotator(rot.roll, rot.pitch, {yaw if yaw is not None else 0}), False)
if {any(s is not None for s in (scale_x, scale_y, scale_z))!r}:
    s = a.get_actor_scale3d()
    a.set_actor_scale3d(unreal.Vector(
        {scale_x if scale_x is not None else 's.x'},
        {scale_y if scale_y is not None else 's.y'},
        {scale_z if scale_z is not None else 's.z'}))
loc = a.get_actor_location(); s = a.get_actor_scale3d()
result = {{"label": lbl, "x": loc.x, "y": loc.y, "z": loc.z,
           "scale": [s.x, s.y, s.z]}}
"""
    return _py(code)["result"]


@mcp.tool()
def set_actor_verse_tags(
    actor_label: str, tag_names: list[str], project_mount: str = ""
) -> dict:
    """Set (or clear, with an empty list) the Verse gameplay tags on a prop.

    Tags must exist in the project's Verse code (`my_tag := class(tag) {}`);
    their generated classes live at <project_mount>/_Verse.<tag_name> (e.g.
    '/MyProject/_Verse.shelf_tag_1a'). project_mount defaults to
    $UEFN_PROJECT_MOUNT. The actor must carry a VerseTagMarkupComponent  - 
    fresh spawns DON'T have one; clone a tagged actor with
    duplicate_level_actor instead.
    """
    mount = project_mount or os.environ.get("UEFN_PROJECT_MOUNT", "")
    if not mount:
        raise RuntimeError(
            "project_mount not given and UEFN_PROJECT_MOUNT not set "
            "(e.g. '/MyProject' - the first segment of your content paths)."
        )
    tag_paths = [f"{mount.rstrip('/')}/_Verse.{t}" for t in tag_names]
    code = f"""
lbl = {actor_label!r}
tag_paths = {tag_paths!r}
a = next((a for a in actor_sub.get_all_level_actors()
          if a.get_actor_label() == lbl), None)
if a is None:
    raise RuntimeError("actor not found: " + lbl)
comp = next((c for c in a.get_components_by_class(unreal.ActorComponent)
             if c.get_class().get_name() == "VerseTagMarkupComponent"), None)
if comp is None:
    raise RuntimeError(
        "no VerseTagMarkupComponent on " + lbl +
        " - clone a tagged actor instead of spawning fresh")
infos = []
for tp in tag_paths:
    tag_obj = unreal.load_object(None, tp)
    if tag_obj is None:
        raise RuntimeError("tag class not found: " + tp +
                           " (run a Verse build so tag classes generate)")
    ti = unreal.VerseTagTypeInfo()
    ti.set_editor_property("internal_tag", tag_obj)
    infos.append(ti)
cont = unreal.VerseTagTypeInfoContainer()
cont.set_editor_property("internal_tags", infos)
comp.set_editor_property("internal_tags", cont)
result = {{"label": lbl, "tags": {tag_names!r}}}
"""
    return _py(code)["result"]


@mcp.tool()
def list_assets(folder: str) -> list[str]:
    """List asset package paths under a content folder, e.g.
    '/MyProject/Food/Entities'."""
    code = f"""
result = list(unreal.EditorAssetLibrary.list_assets({folder!r}))
"""
    return _py(code)["result"]


@mcp.tool()
def rename_asset(old_path: str, new_path: str, force: bool = False) -> dict:
    """Rename/move an asset - with a Verse-reference pre-flight check.

    Asset classes are exposed to Verse BY NAME; renaming an asset that .verse
    files reference breaks the next build. If references are found the rename
    is refused (returning the reference list) unless force=True.

    Also beware: the Verse wrapper class regenerates only on the next Verse
    build or editor restart - prefabs referencing the renamed asset look
    empty until then, then heal.
    """
    old_name = old_path.rsplit("/", 1)[-1].split(".")[0]
    try:
        refs = find_verse_references(_content_dir(), old_name)
    except RuntimeError as e:
        if not force:
            return {
                "renamed": False,
                "reason": f"Cannot run the Verse-reference safety check: {e} "
                          "Set UEFN_CONTENT_DIR (or pass force=True to skip "
                          "the check at your own risk).",
            }
        refs = []
    if refs and not force:
        return {
            "renamed": False,
            "reason": "Verse code references this asset name - update these "
                      "first or pass force=True",
            "references": refs[:30],
        }
    code = f"""
ok = unreal.EditorAssetLibrary.rename_asset({old_path!r}, {new_path!r})
result = {{"renamed": bool(ok)}}
"""
    out = _py(code)["result"]
    out["verse_references"] = refs[:30]
    return out


@mcp.tool()
def clone_and_patch_asset(
    template_path: str,
    dest_path: str,
    string_swaps: dict[str, str],
) -> dict:
    """Author a new binary asset by cloning a working template and
    byte-patching name references - the ONLY reliable programmatic write path
    for Scene Graph entity prefabs in UEFN 41.10 (creation APIs silently
    no-op there).

    Steps performed: duplicate_asset(template → dest) + save (fixes internal
    GUIDs), then patch every ASCII occurrence of each old→new string in the
    saved .uasset on disk, then reload the package (Blueprint recompiles; a
    'failed to find replacement object' warning on reload is expected and
    harmless).

    HARD CONSTRAINTS (validated before running):
    - old and new strings must be the SAME byte length;
    - new names must not end in a digit run without a leading zero
      ('first_100' is unsafe, 'first_099'/'first0100' are safe) - FName
      digit-splitting otherwise drops components silently on load.

    Requires UEFN_CONTENT_DIR.
    """
    warnings: list[str] = []
    for old, new in string_swaps.items():
        try:
            warnings += validate_patch(old, new, None)
        except SafetyViolation as e:
            return {"created": False, "safety_violation": str(e)}

    olds = list(string_swaps)
    for a in olds:
        for b in olds:
            if a != b and a in b:
                return {
                    "created": False,
                    "safety_violation": f"swap key '{a}' is a substring of "
                    f"swap key '{b}' - patching would corrupt '{b}' "
                    "occurrences. Split into separate calls or use "
                    "longer, non-overlapping names.",
                }

    dest_file = _asset_file(dest_path)
    folder, _, leaf = dest_path.rpartition("/")
    pkg = f"{folder}/{leaf.split('.')[0]}"

    code = f"""
src = {template_path!r}
dst = {dest_path!r}
if not unreal.EditorAssetLibrary.duplicate_asset(src, dst):
    raise RuntimeError("duplicate_asset failed: " + src + " -> " + dst)
unreal.EditorAssetLibrary.save_asset(dst, only_if_is_dirty=False)
result = {{"duplicated": True}}
"""
    _py(code)

    swaps = {o.encode("ascii"): n.encode("ascii") for o, n in string_swaps.items()}
    rx = re.compile(b"|".join(re.escape(o) for o in swaps))
    patched: dict[str, int] = {}
    for attempt in range(3):
        try:
            data = dest_file.read_bytes()
            patched = {o.decode(): data.count(o) for o in swaps}
            new_data = rx.sub(lambda m: swaps[m.group(0)], data)
            tmp = dest_file.with_suffix(".uasset.patch-tmp")
            tmp.write_bytes(new_data)
            os.replace(tmp, dest_file)
            break
        except PermissionError:
            _py(f"unreal.EditorLoadingAndSavingUtils.unload_packages("
                f"[unreal.load_package({pkg!r})])\nresult = True")
            time.sleep(0.5)
        except OSError as e:
            return {
                "created": False,
                "error": f"patch failed after clone: {e}",
                "orphaned_asset": dest_path,
                "cleanup": "the UNPATCHED clone exists in the project - "
                           "delete it in the editor or retry the patch",
            }
    else:
        return {
            "created": False,
            "error": f"file locked by editor: {dest_file}",
            "orphaned_asset": dest_path,
            "cleanup": "the UNPATCHED clone exists in the project - "
                       "delete it in the editor or retry the patch",
        }

    _py(f"""
p = unreal.load_package({pkg!r})
if p:
    unreal.EditorLoadingAndSavingUtils.reload_packages([p])
result = True
""")
    zero = [k for k, v in patched.items() if v == 0]
    if zero:
        warnings.append(f"strings not found in template bytes (0 occurrences): {zero}")
    return {"created": True, "asset": dest_path, "occurrences_patched": patched,
            "warnings": warnings}


@mcp.tool()
def export_texture_png(texture_path: str, output_png: str) -> dict:
    """Export a texture asset to a PNG on disk so it can be visually read.

    Use for product/label identification: asset NUMBERING NEVER reliably
    matches content (proven shuffled on every re-import) - always identify
    meshes by exporting their diffuse texture and reading the label.
    """
    code = f"""
tex = unreal.load_asset({texture_path!r})
if tex is None:
    raise RuntimeError("texture not found: " + {texture_path!r})
task = unreal.AssetExportTask()
task.object = tex
task.filename = {output_png!r}
task.exporter = unreal.TextureExporterPNG()
task.automated = True
task.replace_identical = True
ok = unreal.Exporter.run_asset_export_task(task)
result = {{"exported": bool(ok), "file": {output_png!r}}}
"""
    return _py(code)["result"]


@mcp.tool()
def save_dirty_packages() -> dict:
    """Save all dirty packages (level + assets). Call once at the END of a
    batch of edits - per-op saves are what make automation slow."""
    return _py(
        "unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)\n"
        "result = True"
    )


@mcp.tool()
def unload_packages(package_paths: list[str]) -> dict:
    """Release the editor's file handles on packages so external tools can
    write the .uasset files (needed after a restart - the editor holds
    handles on everything it loaded). Unloaded packages lazily reload from
    disk when next referenced."""
    code = f"""
pkgs = [unreal.load_package(p) for p in {package_paths!r}]
pkgs = [p for p in pkgs if p]
unreal.EditorLoadingAndSavingUtils.unload_packages(pkgs)
result = {{"unloaded": len(pkgs)}}
"""
    return _py(code)["result"]


@mcp.tool()
def search_digest(pattern: str, digest: str = "fortnite", max_results: int = 40) -> list[str]:
    """Regex-search the project's Verse API digests - the AUTHORITATIVE record
    of which APIs exist in this Fortnite build (the API changes between
    releases; never trust memory). digest: fortnite | verse | unrealengine |
    assets | all. Requires UEFN_DIGEST_DIR."""
    d = os.environ.get("UEFN_DIGEST_DIR")
    if not d or not Path(d).is_dir():
        raise RuntimeError(
            "UEFN_DIGEST_DIR not set - point it at the project's "
            r"Saved\VerseProject\<Project> folder."
        )
    files = {
        "fortnite": ["Fortnite/Fortnite.digest.verse"],
        "verse": ["Verse/Verse.digest.verse"],
        "unrealengine": ["UnrealEngine/UnrealEngine.digest.verse"],
        "assets": [],
    }
    targets: list[Path] = []
    root = Path(d)
    if digest == "all":
        targets = list(root.rglob("*.digest.verse"))
    elif digest == "assets":
        targets = [p for p in root.rglob("Assets.digest.verse")]
    else:
        targets = [root / f for f in files.get(digest, []) if (root / f).is_file()]
        if not targets:
            targets = [p for p in root.rglob("*.digest.verse")
                       if digest in p.name.lower()]
    rx = re.compile(pattern)
    hits: list[str] = []
    for f in targets:
        try:
            for i, line in enumerate(
                f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if rx.search(line):
                    hits.append(f"{f.name}:{i}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        return hits
        except OSError:
            continue
    return hits


@mcp.tool()
def check_verse_references(name: str) -> list[str]:
    """Grep the project's .verse files for references to an asset/class name.
    ALWAYS run before renaming any asset - Verse references assets by name
    and a rename breaks the next build. Requires UEFN_CONTENT_DIR."""
    return find_verse_references(_content_dir(), name)[:50]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
