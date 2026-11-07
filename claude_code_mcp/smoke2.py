"""Second zero-token pass: the image conversion, the clipboard pair, the
confirm-pattern policy and the blocked-command gate."""
import asyncio, base64, os, sys, tempfile
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
HERE = os.path.dirname(os.path.abspath(__file__))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a cp1252 console cannot print "⚠" (see smoke.py)
SERVER_ENV = dict(os.environ)  # the whole environment, as Claude Code passes it (see smoke.py)

async def main():
    tmp = tempfile.mkdtemp(prefix="myagent-mcp-smoke2-")
    os.makedirs(os.path.join(tmp, "sub"))
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(HERE, "server.py")],
                                   env=SERVER_ENV)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("screenshot", {})
            kinds = [c.type for c in res.content]
            print("screenshot content types:", kinds)
            print("texts:", [c.text[:300] for c in res.content if c.type == "text"])
            img = next((c for c in res.content if c.type == "image"), None)
            if img is None:
                print("NO IMAGE — see text above"); return
            raw = base64.b64decode(img.data)
            print("image:", img.mimeType, len(raw), "bytes; text:", next(c.text for c in res.content if c.type == "text")[:120])
            assert "image" in kinds and len(raw) > 1000
            res = await s.call_tool("clipboard_write", {"text": "myagent-mcp round trip"})
            print(res.content[0].text)
            res = await s.call_tool("clipboard_read", {})
            print(res.content[0].text); assert "round trip" in res.content[0].text
            res = await s.call_tool("run_command", {"command": f"rm -r {tmp}/sub && echo removed"})
            txt = "\n".join(c.text for c in res.content); print(txt)
            assert os.path.exists(os.path.join(tmp, "sub")), "default policy must refuse a confirm-pattern command"
            assert "MYAGENT_MCP_CONFIRM" in txt
            res = await s.call_tool("manage_instructions", {"action": "list"})
            print("instructions listed:", res.content[0].text.count("\n•") + res.content[0].text.count("\n- "), "rows,", len(res.content[0].text), "chars")
            res = await s.call_tool("user_prompt", {"message": "hi?"})
            print(res.content[0].text)
    print("\nsmoke2 passed")
asyncio.run(main())
