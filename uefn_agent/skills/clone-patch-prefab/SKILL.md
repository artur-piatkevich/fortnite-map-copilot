---
name: clone-patch-prefab
description: Author Scene Graph entity prefabs programmatically by cloning a working template and byte-patching mesh references - the only write path that persists in UEFN 41.10.
---

# Clone-and-patch prefab authoring

Use when the user wants N entity prefabs wired to N meshes (product packs,
collectibles, etc.). Direct creation APIs silently fail for prefabs in 41.10.

## Preconditions

1. ONE hand-wired working template exists (user creates the first entity in
   the editor: Add component → mesh_component → pick the mesh). Everything
   else is cloned from it.
2. All target mesh names are byte-patch safe: same length as the template's
   mesh name, and any trailing digit run starts with '0' (`item_001`…`item_099`,
   `item0100`… - NOT `item_100`). Rename meshes FIRST if needed (after
   checking Verse references).
3. A Verse build has run since any mesh renames (wrapper classes regenerate
   on build only).

## Procedure

For each target entity, call `clone_and_patch_asset`:

- `template_path`: the working template prefab (e.g. `/Proj/Entities/ItemA`)
- `dest_path`: the new prefab path
- `string_swaps`: `{ "<template_mesh_name>": "<target_mesh_name>" }` - one
  equal-length swap covers all (typically 4) internal reference strings.

Pace at one call per request with ~300ms between; verify a pilot (ask the
user to open the first clone and confirm the mesh shows) before running the
full batch. Expect ~7-16s per asset (Blueprint compile on save dominates).

## Verification

- `list_assets` on the destination folder for presence.
- Byte-level: the destination .uasset must contain the target mesh name and
  zero occurrences of the template mesh name.
- Editor visual check by the user on a sample (components silently dropping
  = FName digit-split violation; re-check name safety).
