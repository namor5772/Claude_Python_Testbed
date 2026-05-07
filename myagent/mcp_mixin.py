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

* All server connections live inside one long-lived **runner coroutine**
  (``_mcp_runner``) that owns the ``AsyncExitStack`` and parks on a shutdown
  event for the entire app lifetime. The runner pattern is required because
  ``stdio_client`` and ``ClientSession`` use anyio task groups internally,
  whose cancel scopes are bound to the originating task. Splitting the
  lifecycle across multiple ``run_coroutine_threadsafe`` calls would let the
  task that opened the stack end, taking the anyio reader/writer pumps with
  it — connection succeeds, then the next operation fails with
  ``Connection closed``. Keeping one task alive for the whole session avoids
  that structured-concurrency mismatch.

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
import gc
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
        self._mcp_runner_future = None      # concurrent.Future of the runner task
        self._mcp_shutdown_event = None     # asyncio.Event signalling runner exit
        self._mcp_ready_event = None        # threading.Event signalling startup done

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
        """Spawn the dedicated asyncio loop thread and a long-lived runner
        coroutine that owns every MCP server connection. The runner connects
        all configured servers, lists their tools, then parks on a shutdown
        event until app close.

        Safe to call when ``_HAS_MCP`` is False or no servers are configured —
        returns immediately in either case. Per-server connection failures are
        logged via the queue but never raise."""
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

        # Launch the runner — one long-lived task that owns every connection.
        # Block this thread until the runner has finished connecting + listing
        # tools (signalled via the threading.Event), so callers see a fully
        # populated MCP_TOOLS list when this method returns.
        self._mcp_ready_event = threading.Event()
        self._mcp_runner_future = asyncio.run_coroutine_threadsafe(
            self._mcp_runner(config), self._mcp_loop
        )
        if self._mcp_ready_event.wait(timeout=30):
            self._mcp_connected = bool(self._mcp_sessions)
        else:
            self._mcp_log("⚠ MCP startup timed out\n")

    async def _mcp_runner(self, config):
        """Long-lived task owning all MCP connections for the app's lifetime.

        Sequence: open AsyncExitStack → connect all servers → list their tools
        → signal ready → park on shutdown event → unwind on shutdown. Every
        operation that touches anyio task groups happens inside this single
        task, so the cancel scopes survive for the whole MyAgent session and
        subsequent ``run_coroutine_threadsafe`` calls (``call_tool`` from
        ``do_mcp_call``) see a live connection."""
        self._mcp_shutdown_event = asyncio.Event()
        self._mcp_exit_stack = AsyncExitStack()
        try:
            async with self._mcp_exit_stack:
                await self._connect_all(config)
                await self._refresh_mcp_tools_async()
                self._mcp_ready_event.set()
                await self._mcp_shutdown_event.wait()
            # Drain pending Windows ProactorEventLoop pipe-close callbacks
            # while the loop is still alive. Without this, asyncio subprocess
            # transports inside `stdio_client` get GC'd at interpreter exit
            # (after the loop has closed) and emit harmless-but-ugly
            # `RuntimeError: Event loop is closed` finalizer tracebacks.
            gc.collect()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        except Exception as e:
            self._mcp_log(f"⚠ MCP runner error: {e}\n")
        finally:
            if self._mcp_ready_event is not None and not self._mcp_ready_event.is_set():
                self._mcp_ready_event.set()

    async def _connect_all(self, config):
        """Open every configured stdio server **sequentially in the runner
        task** and store their sessions in ``self._mcp_sessions``. Must be
        called from inside the runner task — it relies on
        ``self._mcp_exit_stack`` being open AND on every ``enter_async_context``
        call running in the same task that will eventually unwind the stack.

        Why sequential and not ``asyncio.gather``: ``stdio_client`` and
        ``ClientSession`` use anyio task groups internally whose cancel scopes
        bind to the calling task. ``asyncio.gather(_connect_one(...))`` would
        spawn each connect as a child task; the cancel scopes would bind to
        those child tasks, which finish as soon as ``_connect_one`` returns —
        leaving the contexts orphaned in ``_mcp_exit_stack``. On macOS this
        triggers ``Attempted to exit cancel scope in a different task than it
        was entered in`` at exit time; on Windows ProactorEventLoop the
        stricter binding doesn't fire but the same orphan-scope hazard
        exists. Sequential connects keep every scope bound to the runner
        task itself, so the lifecycle is consistent across platforms."""
        for name, spec in config.items():
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

    def _disconnect_mcp_servers(self):
        """Signal the runner to exit, which unwinds the AsyncExitStack
        (closing every session and terminating spawned subprocess servers),
        then stop the loop thread. Safe to call more than once."""
        if not self._mcp_loop or not self._mcp_loop.is_running():
            return
        if self._mcp_shutdown_event is not None:
            self._mcp_loop.call_soon_threadsafe(self._mcp_shutdown_event.set)
        if self._mcp_runner_future is not None:
            try:
                self._mcp_runner_future.result(timeout=10)
            except Exception:
                pass
        self._mcp_sessions.clear()
        self._mcp_tools_by_name.clear()
        MCP_TOOLS.clear()
        self._mcp_loop.call_soon_threadsafe(self._mcp_loop.stop)
        if self._mcp_thread:
            self._mcp_thread.join(timeout=5)
        self._mcp_loop = None
        self._mcp_thread = None
        self._mcp_exit_stack = None
        self._mcp_runner_future = None
        self._mcp_shutdown_event = None
        self._mcp_connected = False

    # ── Tool discovery ───────────────────────────────────────────────────────

    async def _refresh_mcp_tools_async(self):
        """Re-fetch tools from every connected server and rebuild MCP_TOOLS
        + the lookup table. Async version — called inline from the runner
        task during startup, so each ``session.list_tools()`` runs while the
        owning task is still alive. Mutates the module-level MCP_TOOLS list
        in place so existing references in streaming_mixin keep pointing to
        the same object."""
        MCP_TOOLS.clear()
        self._mcp_tools_by_name.clear()
        if not self._mcp_sessions:
            return
        for server_name, session in self._mcp_sessions.items():
            try:
                result = await session.list_tools()
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

    def _refresh_mcp_tools(self):
        """Sync wrapper around ``_refresh_mcp_tools_async``. Schedules onto
        the MCP event loop. Safe to call from any thread once the runner is
        up — ``list_tools`` reuses the existing session connection (no new
        anyio task groups)."""
        if not self._mcp_loop or not self._mcp_loop.is_running():
            MCP_TOOLS.clear()
            self._mcp_tools_by_name.clear()
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._refresh_mcp_tools_async(), self._mcp_loop
            ).result(timeout=10)
        except Exception as e:
            self._mcp_log(f"⚠ MCP refresh failed: {e}\n")

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
        """Surface MCP messages through MyAgent's queue (visible in the GUI
        activity widget) AND mirror to stderr (visible when launching from
        a console or with stderr captured). The dual-sink mirror is free on
        ``pythonw.exe`` where stderr is normally discarded, but invaluable
        for diagnostic launches that capture stderr to a file."""
        import sys
        try:
            sys.stderr.write(message)
            sys.stderr.flush()
        except Exception:
            pass
        queue = getattr(self, "queue", None)
        if queue is not None:
            try:
                queue.put({"type": "tool_info", "content": message})
            except Exception:
                pass
