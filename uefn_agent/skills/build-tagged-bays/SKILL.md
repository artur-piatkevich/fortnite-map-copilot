---
name: build-tagged-bays
description: Place large tagged prop layouts (shelf bays, zones, spawn grids) from a plan table - duplicate a tagged seed actor, retag each clone, save once.
---

# Tagged layout construction

Use when the game's Verse code defines a spatial contract (tag names on
marker props whose transforms encode gameplay geometry) and the user wants
the layout built in the level.

## Step 1 - extract the contract from the Verse code

Read the game code for: required tag names, what the marker's box means
(which axis is length/depth/height, where the reference face is), counts per
zone, and any mandatory singleton props. Write the full placement plan to a
CSV (label, tag, x, y, z, yaw, sx, sy, sz) and review totals against the
contract BEFORE placing.

## Step 2 - seed actor

Fresh spawns lack `VerseTagMarkupComponent`, so a taggable seed must exist:
ask the user to place ONE prop of the desired type and add any Verse tag to
it in the Details panel. All placements clone from it.

## Step 3 - build

For each plan row: `duplicate_level_actor` from the seed (cheap, ~0.1s),
`set_actor_transform` to the planned pose, `set_actor_verse_tags` with the
row's tag (empty list for untagged visuals). Tag classes exist only after a
Verse build - verify one tag loads before the batch.

`save_dirty_packages` ONCE at the end, not per actor.

## Step 4 - verify

Census pass: list actors, group by tag, compare counts per tag against the
plan. Report the diff table to the user, and render a floor-plan artifact
(top-down SVG/HTML from the CSV) so they can eyeball the layout before
walking it in-editor.
