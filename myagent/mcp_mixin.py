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
    from mcp import ClientSession, StdioServerParameters, types as mcp_types
    from mcp.client.stdio import stdio_client


    async def _list_roots_callback(context):
        """Return an empty ListRootsResult instead of the SDK's default ErrorData.
        Some servers (e.g. @modelcontextprotocol/server-filesystem) interact
        poorly when the client returns an error response to roots/list — the
        anyio cancel scope around the response handling can close the
        underlying stream, breaking the next real RPC with ClosedResourceError.
        Returning a valid empty ListRootsResult keeps the channel healthy."""
        return mcp_types.ListRootsResult(roots=[])


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
        self._mcp_errlog = None             # file handle passed as subprocess stderr

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

        # Open a stable stderr sink for child stdio servers. Must be a real
        # file handle, not sys.stderr — under pythonw.exe sys.stderr can be
        # None or a corrupted NUL stub, and asyncio's Windows ProactorEventLoop
        # subprocess transport mishandles it, producing ClosedResourceError on
        # the first real RPC. os.devnull works on every platform.
        if self._mcp_errlog is None:
            self._mcp_errlog = open(os.devnull, "w")

        # Spin up the dedicated event loop on a background thread.
        # CRITICAL: the loop must be CREATED inside the runner thread, not
        # on the main thread. On Windows, ProactorEventLoop's IOCP (I/O
        # completion port) is owned by the thread that constructs the
        # loop. Subprocess transports register stdio pipe handles to that
        # IOCP. If we create the loop on the main thread but run it on a
        # background thread, IOCP completion events are queued to the
        # original thread but drained from the runner thread — most
        # operations survive but bursty subprocess stdio (initialize →
        # list_tools in rapid succession against
        # @modelcontextprotocol/server-filesystem) hits spurious EOF and
        # surfaces ``ClosedResourceError`` on the client side. Creating
        # the loop inside the runner is what ``asyncio.run()`` does
        # implicitly, which is why standalone async scripts don't hit
        # this bug while MyAgent's threaded setup does.
        loop_ready = threading.Event()
        loop_holder = []

        def _loop_runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_holder.append(loop)
            loop_ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._mcp_thread = threading.Thread(
            target=_loop_runner, daemon=True, name="MCPLoop"
        )
        self._mcp_thread.start()
        loop_ready.wait()
        self._mcp_loop = loop_holder[0]

        # Launch the runner — one long-lived task that owns every connection.
        # Block this thread until the runner has finished connecting + listing
        # tools (signalled via the threading.Event), so callers see a fully
        # populated MCP_TOOLS list when this method returns. The 5-minute
        # ceiling is generous on purpose: first-run cold-cache `npx -y`
        # downloads of fat packages (gmail-mcp pulls in `googleapis` ~100MB)
        # can take 30-90s on broadband and longer on slower links. Warm-cache
        # launches complete in 1-3s, so the long ceiling costs nothing in
        # practice and only fires when a server is genuinely stuck.
        self._mcp_ready_event = threading.Event()
        self._mcp_runner_future = asyncio.run_coroutine_threadsafe(
            self._mcp_runner(config), self._mcp_loop
        )
        if self._mcp_ready_event.wait(timeout=300):
            self._mcp_connected = bool(self._mcp_sessions)
        else:
            self._mcp_log("⚠ MCP startup timed out after 5 minutes\n")

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
                # _connect_all now lists tools inline per-server immediately
                # after each successful initialize(), populating MCP_TOOLS
                # within the same anyio scope as the connect. Calling
                # _refresh_mcp_tools_async after the loop is no longer needed
                # and was the source of the ClosedResourceError from listing
                # against a session whose scope had been disturbed by later
                # sessions' setup.
                await self._connect_all(config)
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
            session = await self._open_server_session(name, spec)
            if session is None:
                continue
            self._mcp_sessions[name] = session
            self._mcp_log(f"✓ MCP server '{name}' connected\n")
            # List this server's tools IMMEDIATELY before any other server's
            # setup runs. With multiple stdio_client contexts entered on the
            # same AsyncExitStack, the next ``enter_async_context`` can
            # nudge the previous session's anyio scope into a partial-close
            # state, manifesting as ``ClosedResourceError`` on the older
            # session's first use. By listing tools while this session is
            # still the most-recently-set-up resource, we capture them
            # before any interference window opens. Errors here don't
            # abort the connect loop — the server still gets added to
            # ``_mcp_sessions`` and ``do_mcp_call`` can recover later.
            await self._list_tools_for_server(name, spec)

    async def _open_server_session(self, name, spec):
        """Spawn a single stdio MCP server and return an initialized
        ``ClientSession``, or ``None`` on failure. Pulled out of
        ``_connect_all`` so the same setup can be reused by
        ``_list_tools_for_server`` to reconnect a server whose stdio stream
        closed between ``initialize()`` and the first real RPC (a known
        intermittent failure mode for ``@modelcontextprotocol/server-filesystem``
        on Windows). The new session enters the same ``_mcp_exit_stack`` so
        its resources unwind correctly at app shutdown alongside the dead
        session's stale entries."""
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
            # We pass an explicit ``errlog`` (sink for the subprocess's stderr)
            # because the mcp SDK's default is ``sys.stderr`` — under
            # ``pythonw.exe`` (no console attached, e.g. desktop shortcut or
            # .bat without redirect) sys.stderr is None / a NUL stub, and
            # passing it as the subprocess's stderr handle corrupts asyncio's
            # subprocess pipe setup on Windows ProactorEventLoop, producing
            # ``ClosedResourceError`` on the first real RPC after initialize.
            # Routing to ``os.devnull`` always gives a valid handle.
            streams = await self._mcp_exit_stack.enter_async_context(
                stdio_client(params, errlog=self._mcp_errlog)
            )
            session = await self._mcp_exit_stack.enter_async_context(
                ClientSession(*streams, list_roots_callback=_list_roots_callback)
            )
            await session.initialize()
            return session
        except Exception as e:
            self._mcp_log(f"⚠ MCP server '{name}' failed to connect: {e}\n")
            return None

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
        if self._mcp_errlog is not None:
            try:
                self._mcp_errlog.close()
            except Exception:
                pass
            self._mcp_errlog = None

    # ── Tool discovery ───────────────────────────────────────────────────────

    async def _list_tools_for_server(self, server_name, spec=None):
        """List a single server's tools and append them to MCP_TOOLS. Called
        inline from ``_connect_all`` immediately after each ``session.initialize()``
        succeeds — listing right then catches the tools while the session is
        still the most-recently-set-up resource and avoids the inter-server
        interference window that produces ``ClosedResourceError`` if listing
        is deferred to a later batch step.

        Errors are logged with exception type and message, but never raised —
        a list_tools failure for one server should not abort the connect loop
        for the others, and ``do_mcp_call`` can still attempt to use the
        session if the model invokes a tool that someone happens to know about
        (e.g. cached schema from a previous session).

        Reconnect-on-close: if the session's stdio stream closed between
        ``initialize()`` and the first ``list_tools`` call (an intermittent
        race with ``@modelcontextprotocol/server-filesystem`` on Windows),
        anyio raises ``ClosedResourceError``. Retrying against the same
        session is futile — the resource is terminal-closed. Instead, we
        spawn a fresh session via ``_open_server_session``, swap it into
        ``_mcp_sessions``, and try once more. The dead session's stack
        entries linger until app shutdown but LIFO unwind tolerates them.
        ``spec`` is required for the reconnect path; pass it from callers
        that have it, or omit and we'll re-read the config from disk."""
        session = self._mcp_sessions.get(server_name)
        if session is None:
            return
        await asyncio.sleep(0.05)
        result = None
        for attempt in range(2):
            try:
                result = await session.list_tools()
                break
            except BaseException as e:
                msg = f"{type(e).__name__}: {e}".lower()
                stream_closed = "connection closed" in msg or "closedresource" in msg
                if stream_closed and attempt == 0:
                    self._mcp_log(
                        f"⚠ '{server_name}' stream closed between init and "
                        f"list_tools — reconnecting and retrying\n"
                    )
                    if spec is None:
                        spec = self._load_mcp_config().get(server_name)
                    if spec is None:
                        self._mcp_log(
                            f"⚠ '{server_name}' reconnect skipped: no config\n"
                        )
                        return
                    new_session = await self._open_server_session(server_name, spec)
                    if new_session is None:
                        return
                    self._mcp_sessions[server_name] = new_session
                    session = new_session
                    await asyncio.sleep(0.1)
                    continue
                self._mcp_log(
                    f"⚠ MCP server '{server_name}' list_tools failed: "
                    f"{type(e).__name__}: {e}\n"
                )
                return
        if result is None:
            return
        for tool in getattr(result, "tools", []) or []:
            full_name = f"{server_name}{MCP_NAME_SEP}{tool.name}"
            MCP_TOOLS.append({
                "name": full_name,
                "description": (tool.description or "")[:1024],
                "input_schema": getattr(tool, "inputSchema", None)
                                or {"type": "object", "properties": {}},
            })
            self._mcp_tools_by_name[full_name] = (server_name, tool.name)

    async def _refresh_mcp_tools_async(self):
        """Re-fetch tools from every connected server and rebuild MCP_TOOLS.
        Used by the sync ``_refresh_mcp_tools`` wrapper for callers that want
        to refresh the catalog after startup (e.g., adding a server at
        runtime). The startup path uses ``_list_tools_for_server`` inline
        from ``_connect_all`` instead, to avoid the ``ClosedResourceError``
        that hits when listing is deferred until after every server has set
        up its anyio scope."""
        MCP_TOOLS.clear()
        self._mcp_tools_by_name.clear()
        for server_name in list(self._mcp_sessions.keys()):
            await self._list_tools_for_server(server_name)

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
