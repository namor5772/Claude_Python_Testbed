"""Zero-token smoke test: spawn server.py over stdio as Claude Code would,
list the tools, and call a few read-only ones. No camera, no mail, no API."""
import asyncio, csv, os, sys, tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
# A Windows console is cp1252 by default and cannot print the "•" of the skills
# list or the "⚠" of a refusal — the script died on one 2026-11-07.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# Spawn the server with THIS process's whole environment, as Claude Code does.
# The SDK's default passes a dozen variables only — on Windows not OneDrive,
# without which datapaths falls back to the empty repo-root skills folder and
# the server serves 98 tools and "No skills defined." (seen 2026-11-07).
SERVER_ENV = dict(os.environ)


async def main():
    tmp = tempfile.mkdtemp(prefix="myagent-mcp-smoke-")
    doc = os.path.join(tmp, "note.txt")
    with open(doc, "w") as f:
        f.write("hello from the smoke test\nsecond line\n")
    table = os.path.join(tmp, "t.csv")
    with open(table, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "name"]); w.writerow([1, "alpha"]); w.writerow([2, "beta"])
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "server.py")],
                                   env=SERVER_ENV)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            names = [t.name for t in tools]
            print(f"{len(names)} tools")
            fams = {}
            for n in names:
                fams.setdefault(n.split("_")[0], []).append(n)
            for k in sorted(fams):
                print(f"  {k:12s} {len(fams[k]):3d}")
            for must in ("screenshot", "excel_read", "camera_capture", "microphone_listen",
                         "gmail_search", "proton_search", "outlook_search", "read_document",
                         "run_instruction", "manage_skills", "browser_open", "web_search"):
                assert must in names, must
            for gone in ("find_element", "user_prompt", "upgrade_model"):
                assert gone not in names, gone

            async def call(name, **args):
                res = await s.call_tool(name, args)
                texts = [c.text for c in res.content if c.type == "text"]
                print(f"\n== {name} {args}\n" + "\n".join(texts)[:400])
                return res

            await call("read_document", path=doc)
            await call("read_file", path=doc)
            await call("glob_files", pattern="*.txt", path=tmp)
            await call("csv_search", file_path=table, search_value="beta")
            # ";" runs in bash and PowerShell alike; "&&" is a parse error on PowerShell 5.1.
            await call("run_command", command="echo smoke-ok; pwd")
            await call("clipboard_read")
            await call("manage_skills", action="list")
            res = await call("write_file", path=os.path.join(tmp, "out.txt"), content="x")
            assert "out.txt" in "".join(c.text for c in res.content)
    print("\nsmoke test passed")

asyncio.run(main())
