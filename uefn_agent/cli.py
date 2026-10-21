"""uefn-agent CLI - a Claude Agent SDK harness specialized for UEFN.

Usage:
  uefn-agent --project "C:\\path\\to\\MyUefnProject" "wire all product meshes to entities"
  uefn-agent --project ...            (no prompt = interactive REPL)

The harness:
- registers the UEFN MCP server (uefn_agent.mcp_server) over stdio, pointed
  at the project's Content dir / digests via env;
- builds a system prompt from knowledge/*.md + skills/*/SKILL.md (the
  hard-won UEFN recipes);
- auto-approves only the TYPED, validated uefn tools; the arbitrary-code
  escape hatch (uefn_execute_python) always requires per-call approval, and
  a PreToolUse hook blocks text-tool writes to editor-owned binary files;
- runs on the user's ANTHROPIC_API_KEY (or an existing Claude Code login).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*can_use_tool will not be invoked.*")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    TextBlock,
)
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

PKG_ROOT = Path(__file__).resolve().parent

BASE_PROMPT = """\
You are a UEFN (Unreal Editor for Fortnite) map-development copilot. You write
Verse code and automate the editor through the `uefn` MCP tools, which drive a
Python listener running inside the user's open UEFN editor.

Core workflow rules:
- Verify listener health (uefn_status) before editor work.
- You cannot compile Verse or launch sessions: after writing .verse files,
  ask the user to build (Ctrl+Shift+B) and report errors back.
- Search the project digests (search_digest) for API signatures instead of
  trusting memory - the Verse API changes between Fortnite releases.
- Never edit binary editor-owned files (.uasset/.umap/__ExternalActors__) as
  text; asset mutations go through the uefn tools only.
- Before renaming any asset, run check_verse_references.
- Prefer the typed uefn tools; uefn_execute_python requires the user's
  approval on every call - when you must use it, keep the code minimal and
  explain what it does.
- Pilot-verify one item with the user before running any batch.

The following field-tested knowledge governs all editor automation:
"""

AUTO_APPROVED_UEFN_TOOLS = [
    "mcp__uefn__uefn_status",
    "mcp__uefn__uefn_wait_for_idle",
    "mcp__uefn__list_level_actors",
    "mcp__uefn__duplicate_level_actor",
    "mcp__uefn__set_actor_transform",
    "mcp__uefn__set_actor_verse_tags",
    "mcp__uefn__list_assets",
    "mcp__uefn__rename_asset",
    "mcp__uefn__clone_and_patch_asset",
    "mcp__uefn__export_texture_png",
    "mcp__uefn__save_dirty_packages",
    "mcp__uefn__unload_packages",
    "mcp__uefn__search_digest",
    "mcp__uefn__check_verse_references",
]

PROTECTED_MARKERS = (".lore", "__ExternalActors__", ".uasset", ".umap")


async def _approve_tool(
    tool_name: str, input_data: dict, context: ToolPermissionContext
):
    """Interactive terminal approval for tools that aren't auto-approved.

    The typed uefn tools are allowlisted and never reach this; in practice
    this gates uefn_execute_python (arbitrary code in the editor) and any
    other unlisted tool. Shows the exact input, asks y/n.
    """
    print(f"\n[approval needed] {tool_name}")
    if tool_name.endswith("uefn_execute_python"):
        print("--- code to run inside the UEFN editor ---")
        print(str(input_data.get("code", "")).strip())
        print("-------------------------------------------")
    else:
        print("input:", json.dumps(input_data, indent=2, default=str)[:2000])
    try:
        answer = await asyncio.to_thread(input, "Allow? (y/n): ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        return PermissionResultAllow(updated_input=input_data)
    return PermissionResultDeny(
        message="User denied this tool call.", interrupt=False
    )


async def _block_protected_writes(input_data, tool_use_id, context):
    path = str(input_data.get("tool_input", {}).get("file_path", ""))
    if any(m.lower() in path.lower() for m in PROTECTED_MARKERS):
        return {
            "hookSpecificOutput": {
                "hookEventName": input_data["hook_event_name"],
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"'{path}' is editor-owned binary/revision-control data - "
                    "never write it as text. Use the uefn MCP tools for asset "
                    "mutations."
                ),
            }
        }
    return {}


def _load_knowledge() -> str:
    chunks: list[str] = []
    for f in sorted((PKG_ROOT / "knowledge").glob("*.md")):
        chunks.append(f.read_text(encoding="utf-8"))
    for f in sorted(PKG_ROOT.glob("skills/*/SKILL.md")):
        chunks.append(f"## Recipe: {f.parent.name}\n\n" + f.read_text(encoding="utf-8"))
    return "\n\n---\n\n".join(chunks)


def install_listener(project_root: Path) -> None:
    """Copy the in-editor listener into <project>/Content/Python/."""
    content = project_root / "Content"
    if not content.is_dir():
        sys.exit(f"error: {content} not found - --project must be the UEFN "
                 "project root (the folder containing Content/).")
    src_dir = PKG_ROOT / "listener"
    dest_dir = content / "Python"
    dest_dir.mkdir(exist_ok=True)
    for name in ("uefn_listener.py", "init_unreal.py"):
        src, dest = src_dir / name, dest_dir / name
        if dest.exists() and dest.read_bytes() != src.read_bytes():
            backup = dest.with_suffix(".py.bak")
            backup.write_bytes(dest.read_bytes())
            print(f"  existing {name} differed - backed up to {backup.name}")
        dest.write_bytes(src.read_bytes())
        print(f"  installed {dest}")
    print(
        "\nListener installed. Next steps:\n"
        "  1. In UEFN: Project Settings -> enable 'Python Editor Scripting'\n"
        "     (UEFN 40.00+; if the toggle is missing, the feature may not be\n"
        "     enabled for your Epic account yet).\n"
        "  2. Reopen the project. The Output Log should show:\n"
        "     [MCP] Listener running on http://127.0.0.1:8765\n"
        "  3. Run: uefn-agent --project \"" + str(project_root) + "\""
    )


def _default_digest_dir(project_name: str) -> str:
    local = os.environ.get("LOCALAPPDATA", "")
    cand = Path(local) / "UnrealEditorFortnite" / "Saved" / "VerseProject" / project_name
    return str(cand) if cand.is_dir() else ""


def build_options(args: argparse.Namespace) -> ClaudeAgentOptions:
    project = Path(args.project).resolve()
    content_dir = project / "Content"
    if not content_dir.is_dir():
        sys.exit(f"error: {content_dir} not found - --project must be the UEFN "
                 "project root (the folder containing Content/).")

    server_env = {
        **os.environ,
        "UEFN_CONTENT_DIR": str(content_dir),
        "UEFN_PROJECT_MOUNT": args.mount or f"/{project.name}",
        "UEFN_DIGEST_DIR": args.digest_dir or _default_digest_dir(project.name),
    }

    return ClaudeAgentOptions(
        system_prompt=BASE_PROMPT + "\n" + _load_knowledge(),
        mcp_servers={
            "uefn": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "uefn_agent.mcp_server"],
                "env": server_env,
            }
        },
        allowed_tools=AUTO_APPROVED_UEFN_TOOLS + ["Read", "Glob", "Grep", "Write", "Edit"],
        can_use_tool=_approve_tool,
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Write|Edit", hooks=[_block_protected_writes])
            ]
        },
        permission_mode="acceptEdits",
        cwd=str(project),
        setting_sources=["project"] if args.use_project_settings else [],
        model=args.model,
        max_turns=args.max_turns,
    )


async def _print_response(client: ClaudeSDKClient) -> None:
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif hasattr(message, "total_cost_usd") and message.total_cost_usd:
            print(f"\n[turn cost: ${message.total_cost_usd:.4f}]")


async def run(args: argparse.Namespace) -> None:
    options = build_options(args)
    async with ClaudeSDKClient(options=options) as client:
        if args.prompt:
            await client.query(" ".join(args.prompt))
            await _print_response(client)
            return
        print("uefn-agent interactive session (blank line or Ctrl+C to exit)")
        while True:
            try:
                user = (await asyncio.to_thread(input, "\nyou> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user:
                break
            await client.query(user)
            await _print_response(client)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="uefn-agent",
        description="Claude-powered UEFN map development copilot.",
    )
    p.add_argument("--project", required=True,
                   help="UEFN project root (folder containing Content/)")
    p.add_argument("--mount", default="",
                   help="Verse content mount, e.g. /MyProject (default: /<project folder name>)")
    p.add_argument("--digest-dir", default="",
                   help="VerseProject digest folder (default: auto-detect under %%LOCALAPPDATA%%)")
    p.add_argument("--model", default=None,
                   help="model override (default: Claude Code's default model)")
    p.add_argument("--max-turns", type=int, default=None)
    p.add_argument("--install-listener", action="store_true",
                   help="copy the in-editor listener into <project>/Content/Python and exit")
    p.add_argument("--use-project-settings", action="store_true",
                   help="load the target project's .claude settings/CLAUDE.md "
                        "(also imports its permission allowlists - only for "
                        "projects you trust)")
    p.add_argument("prompt", nargs="*", help="one-shot prompt (omit for REPL)")
    args = p.parse_args()
    if args.install_listener:
        install_listener(Path(args.project).resolve())
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
