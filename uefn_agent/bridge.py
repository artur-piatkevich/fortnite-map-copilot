"""HTTP client for the in-editor UEFN listener.

The listener (uefn_listener.py) runs inside the UEFN editor's embedded Python
and serves on 127.0.0.1:8765-8770. All unreal.* work is marshaled onto the
editor main thread by the listener; this client just speaks its JSON protocol.

Hard-won operational rules baked in:
- Commands KEEP EXECUTING server-side after a client timeout. Never blind-retry
  a mutating command; wait for the queue to drain and re-inventory instead.
- Keep individual commands small (a few seconds of editor work). Batches of
  heavy ops (save/compile per item) must be split across commands, not looped
  inside one giant command.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests

DEFAULT_PORTS = range(8765, 8771)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_int(name: str) -> int | None:
    try:
        return int(os.environ.get(name, "")) if os.environ.get(name) else None
    except ValueError:
        return None


DEFAULT_TIMEOUT = _env_float("UEFN_BRIDGE_TIMEOUT", 170.0)


class BridgeError(RuntimeError):
    """Raised when the listener is unreachable or a command fails."""


class EditorPythonError(BridgeError):
    """Raised when in-editor Python raised; carries the editor traceback."""

    def __init__(self, message: str, traceback_text: str = ""):
        super().__init__(message)
        self.traceback_text = traceback_text


@dataclass
class BridgeResult:
    result: Any
    stdout: str = ""
    stderr: str = ""


class UefnBridge:
    def __init__(self, port: int | None = None, timeout: float = DEFAULT_TIMEOUT):
        self._port = port
        self.timeout = timeout


    @property
    def port(self) -> int:
        if self._port is None:
            self._port = self._discover_port()
        return self._port

    def _discover_port(self) -> int:
        env_port = _env_int("UEFN_LISTENER_PORT")
        candidates = [env_port] if env_port else list(DEFAULT_PORTS)
        for p in candidates:
            try:
                r = requests.get(f"http://127.0.0.1:{p}/", timeout=2)
                if r.ok and "commands" in r.json():
                    return p
            except requests.RequestException:
                continue
        raise BridgeError(
            "UEFN listener not reachable on 127.0.0.1:8765-8770. Is the UEFN "
            "editor open with the project loaded? The listener auto-starts via "
            "Content/Python/init_unreal.py, or run it manually via "
            "Tools > Execute Python Script > uefn_listener.py."
        )

    def _url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


    def command(self, name: str, params: dict | None = None) -> Any:
        """Send one listener command; return its result payload."""
        try:
            r = requests.post(
                self._url(),
                json={"command": name, "params": params or {}},
                timeout=self.timeout,
            )
        except requests.Timeout as e:
            raise BridgeError(
                f"Command '{name}' timed out client-side after {self.timeout}s. "
                "IMPORTANT: the command may still be executing in the editor. "
                "Call wait_for_idle() and re-check state before retrying."
            ) from e
        except requests.RequestException as e:
            self._port = None
            raise BridgeError(
                f"Listener connection failed for '{name}': {e}. NOTE: if the "
                "connection dropped after the request was delivered, the "
                "command may still run - for mutating commands, wait_for_idle "
                "and re-check state instead of retrying blindly."
            ) from e

        try:
            body = r.json()
        except ValueError as e:
            raise BridgeError(
                f"Listener returned non-JSON for '{name}' "
                f"(HTTP {r.status_code}): {r.text[:200]!r}"
            ) from e
        if not body.get("success", False):
            raise BridgeError(f"Command '{name}' failed: {body.get('error')}")
        return body.get("result")

    def execute_python(self, code: str) -> BridgeResult:
        """Run Python inside the editor. Assign to `result` to return a value.

        Pre-populated globals in the editor: unreal, actor_sub, asset_sub,
        level_sub, tk, get_tk_root.
        """
        payload = self.command("execute_python", {"code": code})
        res = BridgeResult(
            result=payload.get("result"),
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
        )
        if "Traceback (most recent call last)" in res.stderr:
            raise EditorPythonError(
                f"In-editor Python raised:\n{res.stderr.strip()[-1500:]}",
                traceback_text=res.stderr,
            )
        return res


    def status(self) -> dict:
        return self.command("status")

    def wait_for_idle(self, max_wait_s: float = 600, poll_s: float = 10) -> dict:
        """Block until the listener's command queue is empty.

        Use after a client-side timeout: the editor keeps working through the
        queue even though our request died.
        """
        deadline = time.time() + max_wait_s
        last: dict = {}
        while time.time() < deadline:
            try:
                last = self.status()
                if last.get("queue_size", 0) == 0:
                    return last
            except BridgeError:
                pass
            time.sleep(poll_s)
        raise BridgeError(f"Listener queue not idle after {max_wait_s}s: {last}")
