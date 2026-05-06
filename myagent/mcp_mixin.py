"""MCP (Model Context Protocol) client integration.

MyAgent's existing tool families (TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS, META_TOOLS)
are static dicts compiled into the codebase. MCP adds a dynamic family: external
servers (Gmail, GitHub, Slack, etc.) speak the MCP JSON-RPC protocol over stdio
or HTTP and advertise their tools at runtime. This mixin connects to configured
servers, enumerates their tools, exposes them through MyAgent's normal tool
pipeline, and proxies invocations back to the right server.

Architecture notes:

* MCP's Python SDK is async-only. MyAgent's main thread is Tk (sync) and the
  streaming worker is a sync daemon thread, so we run a dedicated asyncio
  event loop in its own background thread (``self._mcp_thread``) and submit
  coroutines via ``asyncio.run_coroutine_threadsafe``. This keeps MyAgent's
  existing threading model unchanged — callers see synchronous ``do_mcp_call``.

* All server connections (each layered: ``stdio_client`` then ``ClientSession``)
  are held open inside one ``AsyncExitStack`` for the entire app lifetime.
  ``_disconnect_mcp_servers`` unwinds the stack on close, terminating the
  spawned MCP server subprocesses cleanly.

* Tool names are namespaced as ``<server>__<tool>`` so a server collision
  (two servers both named ``send``) is impossible and dispatch is unambiguous.
  Double underscore is allowed by all four providers' tool-name regexes
  (Anthropic, OpenAI, Gemini, Ollama) and is unlikely to appear inside a real
  tool name.

* When ``_HAS_MCP`` is False (the ``mcp`` package isn't installed), every
  method here is a graceful no-op. The user sees no MCP UI and behaviour is
  identical to before this mixin existed.
"""

import asyncio
import json
import os
import socket
import threading
from contextlib import AsyncExitStack


def _free_port():
    """Ask the OS for an unused TCP port, then release it. Used to substitute
    ``${RANDOM_PORT}`` placeholders in MCP server env vars so multiple MyAgent
    instances don't collide on default ports (e.g. shinzo-labs gmail-mcp's
    hardcoded 3000)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

from myagent.constants import _HAS_MCP, MCP_NAME_SEP, MCP_SERVERS_PATH, MCP_TOOLS, IS_WINDOWS as _IS_WINDOWS

if _HAS_MCP:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client


class MCPMixin:
    """Lifecycle, tool discovery, and dispatch for MCP servers."""

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def _init_mcp_state(self):
        """Initialize MCP-related instance attributes. Call from App.__init__
        before any other MCP method."""
        self._mcp_loop = None
        self._mcp_thread = None
        self._mcp_exit_stack = None
        self._mcp_sessions = {}             # server_name -> ClientSession
        self._mcp_tools_by_name = {}        # full_name -> (server_name, real_tool_name)
        self._mcp_connected = False         # True once at least one server is up

    def _load_mcp_config(self):
        """Read MCP_SERVERS_PATH. Returns the ``servers`` dict or {} on any
        problem. JSON shape mirrors Claude Desktop / Cursor:

            {"servers": {"<name>": {"command": "...", "args": [...], "env": {...}}, ...}}
        """
        if not os.path.exists(MCP_SERVERS_PATH):
            return {}
        try:
            with open(MCP_SERVERS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            servers = data.get("servers", {})
            return servers if isinstance(servers, dict) else {}
        except Exception:
            return {}

    def _connect_mcp_servers(self):
        """Spawn the dedicated asyncio loop thread, connect to all servers
        listed in ``mcp_servers.json``, and populate ``MCP_TOOLS``.

        Safe to call when ``_HAS_MCP`` is False or no servers are configured —
        returns immediately in either case. Per-server connection failures are
        logged via the queue (when available) but never raise."""
        if not _HAS_MCP:
            return
        config = self._load_mcp_config()
        if not config:
            return

        # Spin up the dedicated event loop on a background thread.
        self._mcp_loop = asyncio.new_event_loop()
        loop_ready = threading.Event()

        def _loop_runner():
            asyncio.set_event_loop(self._mcp_loop)
            loop_ready.set()
            try:
                self._mcp_loop.run_forever()
            finally:
                self._mcp_loop.close()

        self._mcp_thread = threading.Thread(
            target=_loop_runner, daemon=True, name="MCPLoop"
        )
        self._mcp_thread.start()
        loop_ready.wait()

        # Connect every server inside one AsyncExitStack so close-on-shutdown
        # is a single _aexit_ unwind. 30 s budget total — slow servers are
        # logged-and-skipped rather than blocking the rest of app startup.
        future = asyncio.run_coroutine_threadsafe(
            self._connect_all(config), self._mcp_loop
        )
        try:
            future.result(timeout=30)
            self._mcp_connected = True
        except Exception as e:
            self._mcp_log(f"⚠ MCP connection batch failed: {e}\n")

        self._refresh_mcp_tools()

    async def _connect_all(self, config):
        """Open every configured stdio server in parallel and store their
        sessions in ``self._mcp_sessions``."""
        self._mcp_exit_stack = AsyncExitStack()
        await self._mcp_exit_stack.__aenter__()

        async def _connect_one(name, spec):
            try:
                # Build subprocess env: inherit current process env, then layer
                # any user-supplied env on top, then augment PATH with the dirs
                # where Homebrew / standard local tools live. macOS GUI launches
                # (Finder double-click, Dock, .app bundles) inherit a stripped
                # PATH from launchctl that excludes /opt/homebrew/bin, so spawning
                # `npx` / `uvx` / `bunx` fails with [Errno 2]. Augmenting here
                # makes MCP work regardless of how MyAgent was launched.
                env = dict(os.environ)
                env.update(spec.get("env") or {})
                # Substitute ${RANDOM_PORT} in env values with an OS-assigned
                # free port. Lets multiple MyAgent instances run MCP servers
                # that bind a TCP port (e.g. shinzo-labs gmail-mcp listens on
                # PORT=3000 by default) without colliding on EADDRINUSE.
                for k, v in list(env.items()):
                    if isinstance(v, str) and "${RANDOM_PORT}" in v:
                        env[k] = v.replace("${RANDOM_PORT}", str(_free_port()))
                if not _IS_WINDOWS:
                    path_parts = env.get("PATH", "").split(os.pathsep)
                    for extra in ("/opt/homebrew/bin", "/usr/local/bin",
                                  "/opt/homebrew/sbin"):
                        if extra not in path_parts:
                            path_parts.append(extra)
                    env["PATH"] = os.pathsep.join(p for p in path_parts if p)
                params = StdioServerParameters(
                    command=spec.get("command", ""),
                    args=list(spec.get("args", []) or []),
                    env=env,
                )
                # stdio_client returns (read_stream, write_stream) — both must
                # stay open for the lifetime of the ClientSession layered on top.
                streams = await self._mcp_exit_stack.enter_async_context(
                    stdio_client(params)
                )
                session = await self._mcp_exit_stack.enter_async_context(
                    ClientSession(*streams)
                )
                await session.initialize()
                self._mcp_sessions[name] = session
                self._mcp_log(f"✓ MCP server '{name}' connected\n")
            except Exception as e:
                self._mcp_log(f"⚠ MCP server '{name}' failed to connect: {e}\n")

        await asyncio.gather(
            *(_connect_one(n, s) for n, s in config.items()),
            return_exceptions=True,
        )

    def _disconnect_mcp_servers(self):
        """Tear down the AsyncExitStack (closes every session and terminates
        spawned subprocess servers) and stop the loop thread. Safe to call
        more than once."""
        if not self._mcp_loop or not self._mcp_loop.is_running():
            return
        if self._mcp_exit_stack is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._mcp_exit_stack.__aexit__(None, None, None),
                self._mcp_loop,
            )
            try:
                future.result(timeout=10)
            except Exception:
                pass
            self._mcp_exit_stack = None
        self._mcp_sessions.clear()
        self._mcp_tools_by_name.clear()
        MCP_TOOLS.clear()
        self._mcp_loop.call_soon_threadsafe(self._mcp_loop.stop)
        if self._mcp_thread:
            self._mcp_thread.join(timeout=5)
        self._mcp_loop = None
        self._mcp_thread = None
        self._mcp_connected = False

    # ── Tool discovery ───────────────────────────────────────────────────────

    def _refresh_mcp_tools(self):
        """Re-fetch tools from every connected server and rebuild MCP_TOOLS
        + the lookup table. Mutates the module-level MCP_TOOLS list in place
        so existing references in streaming_mixin keep pointing to the same
        object."""
        MCP_TOOLS.clear()
        self._mcp_tools_by_name.clear()
        if not self._mcp_sessions or not self._mcp_loop:
            return
        for server_name, session in self._mcp_sessions.items():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    session.list_tools(), self._mcp_loop
                )
                result = future.result(timeout=10)
            except Exception as e:
                self._mcp_log(f"⚠ MCP server '{server_name}' list_tools failed: {e}\n")
                continue
            for tool in getattr(result, "tools", []) or []:
                full_name = f"{server_name}{MCP_NAME_SEP}{tool.name}"
                MCP_TOOLS.append({
                    "name": full_name,
                    "description": (tool.description or "")[:1024],
                    "input_schema": getattr(tool, "inputSchema", None)
                                    or {"type": "object", "properties": {}},
                })
                self._mcp_tools_by_name[full_name] = (server_name, tool.name)

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def do_mcp_call(self, tool_name, arguments):
        """Invoke an MCP tool by its namespaced name. Returns a string
        suitable for inclusion in a tool_result block. Never raises —
        all errors are returned as a string so the model can recover."""
        if not _HAS_MCP or not self._mcp_loop:
            return f"MCP is not available (install the 'mcp' package). Cannot call '{tool_name}'."
        mapping = self._mcp_tools_by_name.get(tool_name)
        if not mapping:
            return f"Unknown MCP tool '{tool_name}'."
        server_name, real_tool = mapping
        session = self._mcp_sessions.get(server_name)
        if session is None:
            return f"MCP server '{server_name}' is not connected."
        try:
            future = asyncio.run_coroutine_threadsafe(
                session.call_tool(real_tool, arguments or {}),
                self._mcp_loop,
            )
            result = future.result(timeout=120)
        except Exception as e:
            return f"MCP call '{tool_name}' failed: {e}"

        # Result content is a list of TextContent / ImageContent / EmbeddedResource.
        # Concatenate the text parts; describe binaries inline.
        parts = []
        for c in getattr(result, "content", []) or []:
            text = getattr(c, "text", None)
            if text is not None:
                parts.append(text)
                continue
            data = getattr(c, "data", None)
            if data is not None:
                parts.append(f"[binary content, {len(data)} bytes]")
                continue
            parts.append(str(c))
        return "\n".join(parts) if parts else "(empty result)"

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _mcp_log(self, message):
        """Surface MCP messages through MyAgent's queue when available; fall
        back to stderr during early init before the queue exists."""
        queue = getattr(self, "queue", None)
        if queue is not None:
            try:
                queue.put({"type": "tool_info", "content": message})
                return
            except Exception:
                pass
        import sys
        sys.stderr.write(message)
