# In-editor listener

These two files go into the UEFN project's `Content/Python/` folder - run
`uefn-agent --project "C:\path\to\MyProject" --install-listener` to do it
for you:

- `uefn_listener.py` - HTTP server running inside the UEFN editor's embedded
  Python. Serves on 127.0.0.1:8765 (falls back through 8770), marshals all
  `unreal.*` calls onto the editor main thread, and exposes ~29 commands
  including the `execute_python` escape hatch. Binds localhost only.
- `init_unreal.py` - auto-starts the listener whenever the project opens
  (UEFN's Python plugin runs this file automatically).

Provenance: `uefn_listener.py` derives from KirChuvakov's uefn-mcp-server
(MIT license), audited line-by-line before adoption, with modifications:
`HTTP_TIMEOUT_SEC` raised 30→180 for long-running editor operations.

Requirements: UEFN 40.00+ with **Python Editor Scripting** enabled in
Project Settings. Reopen the project after installing; the Output Log shows
`[MCP] Listener running on http://127.0.0.1:8765` on success.

Operational notes:
- Never hot-swap `uefn_listener.py` through its own `execute_python` - that
  kills the server mid-request. Edit on disk, then restart UEFN.
- Queued commands keep executing after a client-side timeout; clients must
  poll `status` (queue_size) before retrying mutations.
