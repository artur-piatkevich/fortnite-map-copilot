# fortnite-map-copilot

An AI copilot for building Fortnite maps. It works with UEFN, the editor Fortnite maps are made in, writes Verse code and does editor work directly, such as placing props, tagging them for Verse, wiring meshes to entity prefabs, exporting textures for visual identification and searching the project's Verse API digests.

I built it around methods I had already used on a real project, a store with 87 shelf bays and 128 product entities, plus a full mesh-pack migration. It is about 2,390 lines of Python across 8 files (version 0.1.0, July 2026). The Python package and the command keep their original name, `uefn-agent`.

## Architecture

```
┌─────────────────────┐   stdio (MCP)   ┌──────────────────────┐   HTTP :8765   ┌──────────────────┐
│ Agent harness       │ ◄─────────────► │ MCP server           │ ◄────────────► │ UEFN editor      │
│ (Claude Agent SDK)  │                 │ (typed recipe tools  │                │ Python listener  │
│ cli.py              │                 │  + safety validators)│                │ (uefn_listener.py│
└─────────────────────┘                 └──────────────────────┘                │  in Content/     │
                                                                                │  Python/)        │
                                                                                └──────────────────┘
```

Three layers:

1. **In-editor listener** (`uefn_listener.py`, runs inside UEFN's embedded
   Python) - executes editor operations on the main thread. Auto-starts with
   the project via `init_unreal.py`.
2. **MCP server** (`uefn_agent/mcp_server.py`) - exposes typed tools with
   safety validation baked in (equal-length byte patches, FName digit-split
   guard, Verse-reference pre-flight on renames, crash-safe pacing rules).
   Usable from ANY MCP client: this repo's harness, Claude Code, or Claude
   Desktop.
3. **Agent harness** (`uefn_agent/cli.py`, Claude Agent SDK) - the full agent
   loop with the UEFN knowledge base in its system prompt, file tools for
   Verse editing, and permission gates (the arbitrary-code escape hatch
   requires per-call approval; text-tool writes to editor-owned binaries are
   blocked by a hook).

## Setup

Requirements: Python 3.10+, an open UEFN project with **Python Editor
Scripting enabled** (Project Settings; UEFN 40.00+), and an `ANTHROPIC_API_KEY`
(or an existing Claude Code login).

```powershell
pip install -e .

# one-time per UEFN project: install the in-editor listener, then follow
# the printed steps (enable Python Editor Scripting, reopen the project)
uefn-agent --project "C:\path\to\MyUefnProject" --install-listener
```

If `uefn-agent` isn't found, your Python `Scripts` folder isn't on PATH  - 
either add it or use `python -m uefn_agent.cli` instead of `uefn-agent`.

## Run

```powershell
# one-shot
uefn-agent --project "C:\path\to\MyUefnProject" "census all Verse tags on Bay_ actors"

# interactive
uefn-agent --project "C:\path\to\MyUefnProject"
```

When the agent needs the arbitrary-code escape hatch, it prints the exact
editor code and waits for your y/n - the typed tools run without prompts.

All flags:

| Flag | Meaning |
|---|---|
| `--project PATH` | UEFN project root - the folder containing `Content/` (required) |
| `--mount /Name` | Verse content mount (default: `/<project folder name>`) |
| `--digest-dir PATH` | digest folder (default: auto-detect under `%LOCALAPPDATA%\UnrealEditorFortnite`) |
| `--model NAME` | model override (default: Claude Code's default) |
| `--max-turns N` | cap agent turns per prompt |
| `--install-listener` | copy the in-editor listener into the project, print setup steps, exit |
| `--use-project-settings` | opt-in: load the target project's `.claude` settings/CLAUDE.md (imports its permission allowlists - trusted projects only) |

Or register just the MCP server with Claude Code / Claude Desktop (the
installed `uefn-agent-mcp` script is equivalent to `python -m
uefn_agent.mcp_server`):

```json
{
  "mcpServers": {
    "uefn": {
      "command": "python",
      "args": ["-m", "uefn_agent.mcp_server"],
      "env": {
        "UEFN_CONTENT_DIR": "C:\\path\\to\\MyUefnProject\\Content",
        "UEFN_PROJECT_MOUNT": "/MyUefnProject",
        "UEFN_DIGEST_DIR": "C:\\Users\\<you>\\AppData\\Local\\UnrealEditorFortnite\\Saved\\VerseProject\\MyUefnProject"
      }
    }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `uefn_status` / `uefn_wait_for_idle` | listener health; queue-drain after timeouts |
| `list_level_actors` | inventory actors (label/class/location) |
| `duplicate_level_actor` | clone-don't-create prop placement (keeps taggability) |
| `set_actor_transform` | move/rotate/scale by label |
| `set_actor_verse_tags` | rewrite Verse gameplay tags on a prop |
| `list_assets` / `rename_asset` | asset inventory; rename with Verse-reference pre-flight |
| `clone_and_patch_asset` | prefab authoring via template clone + validated byte patch |
| `export_texture_png` | export textures for vision-based identification |
| `save_dirty_packages` / `unload_packages` | batch-end save; file-handle release |
| `search_digest` | grep the build's authoritative Verse API digests |
| `check_verse_references` | grep .verse files before renames |
| `uefn_execute_python` | escape hatch (never auto-approved) |

## Knowledge base

`uefn_agent/knowledge/uefn-gotchas.md` and `uefn_agent/skills/*/SKILL.md`
hold the operational rules the agent runs under (crash-safe pacing, FName digit-split, texture
identification before trusting numbering, clone-and-patch, tagged-layout
construction). They're inlined into the system prompt - edit them to tune
agent behavior.

## Known limitations

- Editor-time only: no programmatic Verse build or play-test trigger exists;
  the human builds (Ctrl+Shift+B) and reports errors.
- Prefab authoring relies on the byte-patch recipe validated on 41.10; new
  UEFN releases may fix (or change) the underlying APIs - re-verify per
  release.
- One heavy asset op per call by design; expect ~7-16s per prefab
  (Blueprint compile dominates).

## License

MIT, see [LICENSE](LICENSE).
