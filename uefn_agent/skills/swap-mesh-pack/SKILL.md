---
name: swap-mesh-pack
description: Replace an imported mesh pack with a re-imported/fixed pack - texture-vision identification first (numbering never matches), then material carry-over and entity re-pointing.
---

# Mesh pack replacement

Use when the user re-imports a fixed/updated version of a mesh pack and
entities must move to the new meshes.

## Step 1 - identify every new mesh by its texture (mandatory)

Import order is NEVER stable and material names repeat across packs. For each
new mesh: `export_texture_png` its diffuse texture, then READ the exported
PNGs (vision) and match each label to the product catalog. Build an explicit
old↔new mesh mapping table and show it to the user before mutating anything.

## Step 2 - carry the old pack's materials over

For each pair, apply the OLD mesh's material to the NEW mesh (slot names are
material names, so downstream Verse identify/tint chains keep working):
set the new mesh's slot-0 `material_interface` to the old material, remap all
sections to slot 0, truncate `static_materials` to one slot, save.

## Step 3 - re-point entities

Swap each entity prefab's mesh reference old→new. If old and new names are
equal length and digit-safe, byte-patch in place (`clone_and_patch_asset`
semantics on the existing prefab file: unload → patch → reload). Otherwise
re-clone from a hand-wired template that already references a new-pack mesh.

## Step 4 - extend Verse chains

Generate/extend the pack's MeshIdentify + GhostTint (or equivalent) `.verse`
chains for the new mesh classes, ask the user to build Verse, and fix any
compile errors they report.

## Do NOT

- Do not substitute an in-place geometry transplant when the user asked for
  the new meshes to be used - equivalent end states are not equivalent
  requests. Ask first.
- Do not delete the old pack's Materials/Textures folders - live meshes
  typically still use them.
