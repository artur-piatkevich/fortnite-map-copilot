"""Auto-start the UEFN MCP listener when the editor opens.

Lives in <Project>/Content/Python/ alongside uefn_listener.py. The editor's
Python plugin runs this file automatically at startup.

Note: importing uefn_listener already starts the listener (its module-level
auto-start block runs on import); the code below just reports the outcome.
The listener stores its state on the `unreal` module (e.g. _mcp_bound_port),
not as module attributes on uefn_listener itself.
"""

import unreal


def _start_mcp():
    try:
        import uefn_listener

        port = getattr(unreal, "_mcp_bound_port", 0)
        if not port:
            port = uefn_listener.start_listener()
        unreal.log(f"[MCP] Listener running on http://127.0.0.1:{port}")
    except ImportError:
        unreal.log_warning(
            "[MCP] uefn_listener.py not found in Python path. "
            "Copy it to Content/Python/ next to this file."
        )
    except Exception as e:
        unreal.log_error(f"[MCP] Auto-start failed: {e}")


_start_mcp()
