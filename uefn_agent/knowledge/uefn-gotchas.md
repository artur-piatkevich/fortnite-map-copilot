# UEFN automation ground rules

Empirically established on UEFN 41.10. Every rule below corresponds to a real
incident (corrupted assets, broken Verse builds, or an editor crash).

## The environment

- UEFN's embedded Python (3.11) is **editor-time only** - gameplay logic must
  stay in Verse. Only touch UI-exposed properties and content-browser-visible
  content, or island publish validation fails.
- There is no programmatic Verse build or play-test trigger in the Python
  surface. After writing Verse, the USER must build (Ctrl+Shift+B) and report
  errors back. Plan for this handoff.
- The Verse API digests (`Fortnite.digest.verse`, `Verse.digest.verse`,
  `UnrealEngine.digest.verse`) are the authoritative record of what exists in
  the user's build. The API changes between Fortnite releases - search the
  digests (search_digest tool), never trust memory.

## Pacing and crash safety

- ONE heavy editor op (asset duplicate / save / Blueprint compile, ~3-8s
  each) per command. Stacking 12 in one call blew the command timeout, and a
  backlog of queued mega-commands **crashed the editor**.
- Commands queued in the listener KEEP EXECUTING after a client-side timeout.
  Never blind-retry a mutating command - wait for idle, then re-inventory
  actual state (on disk / in editor) before acting.
- Actor duplication is cheap (~0.1s, no save); asset saves are expensive.
  Batch level edits freely, save once at the end.

## Asset identity

- **Numbering never survives an import.** Re-imported mesh packs shuffle
  order, and material names repeat across packs on different products.
  Identify meshes by exporting their diffuse texture to PNG and READING the
  label (vision), then build the mapping. Do this every time.
- **Verse references assets by name.** Grep all `.verse` files for an asset's
  name before renaming it (check_verse_references tool) - a rename silently
  breaks the next Verse build (56 errors from one batch rename).
- **Wrapper regen lag:** renaming an asset does not regenerate its
  `_Verse/Assets` wrapper class in-session; that happens on the next Verse
  build or editor restart. Prefabs referencing the renamed asset look empty
  until then, then heal. Don't diagnose emptiness as corruption too early.

## Binary asset authoring (Scene Graph prefabs)

- In 41.10 the creation APIs are broken for entity prefabs:
  `create_prefab_from_entities` returns an instance but persists NOTHING;
  `create_asset_component_from_asset_path` returns None; prefab content lives
  in CDO properties Python can't touch ("protected").
- The only reliable programmatic write path is **clone-and-patch**: duplicate
  a working template asset, save, byte-patch name references on disk
  (equal-length ASCII swaps only), reload the package. The editor does not
  verify name hashes on load. A "failed to find replacement object" warning
  on reload is expected and harmless.
- **FName digit-split trap:** a name with a trailing digit run that lacks a
  leading zero (`first_100`) is serialized as base+number, not a literal
  string - a byte-patched literal creates a DIFFERENT name and the loader
  silently drops components. Only patch to names whose trailing digit run
  starts with '0' (`first_099`, `first0100`, `item_0001`) or that don't end
  in digits.
- `delete_asset` can return True while the file survives (asset registry
  re-adds it), making a later rename into that path fail. Prefer approaches
  that don't need delete+rename (e.g. in-place geometry transplant).
- In-place mesh geometry swap: `StaticMeshEditorSubsystem.
  set_lod_from_static_mesh(old, 0, new, 0, True)` keeps path/name so all
  references update automatically - but it APPENDS the source's material as
  a new slot and routes sections to it. Always follow with: repoint slot 0's
  material, remap sections to slot 0, truncate `static_materials` to one
  slot, save.

## Level actors and Verse tags

- `VerseTagMarkupComponent` is an INSTANCE-ADDED component: freshly spawned
  actors don't have it. To place taggable props, DUPLICATE an existing tagged
  actor (`duplicate_actor(src, None, offset)` - 2nd arg is a World, not an
  offset vector... the 3rd is the offset) and rewrite its tags.
- Tag classes generate at `<mount>/_Verse.<tag_name>` after a Verse build.
- An empty tag array clears the tag (useful for reusing one prop BP for both
  tagged markers and untagged visuals).

## File locks

- After an editor (re)start, the editor holds file handles on every loaded
  package - external writes fail "in use by another process". Release with
  `unreal.EditorLoadingAndSavingUtils.unload_packages(pkgs)`
  (`PackageTools.unload_packages` does NOT exist in this build). Unloaded
  packages lazily reload from patched disk.
- `reload_packages` also restores an asset clobbered in memory from its
  intact disk copy - the recovery move if an experiment trashes an open
  asset that was never saved.

## Working with the user

- When the user gives an explicit step-by-step procedure, follow it exactly
  or ASK before substituting an equivalent - never silently swap in a
  "better" approach.
- New `creative_device` classes only run after the user drags the device
  into the level; remind them of the placement step.
- Surface architecture forks (e.g. Scene Graph entities vs. classic props)
  to the user before committing to one.
