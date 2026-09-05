"""Introspect real competing MCP servers and measure their advertised schemas."""
import asyncio, json, os, sys
import tiktoken
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

E = tiktoken.get_encoding("o200k_base")
tok = lambda s: len(E.encode(s))
ENV = dict(os.environ, SSH_HOST="127.0.0.1", SSH_USER="ubuntu", SSH_PORT="22",
           SSH_KEY="/home/ubuntu/.ssh/fleet", SSH_PRIVATE_KEY="/home/ubuntu/.ssh/fleet")

async def probe(cmd, args):
    p = StdioServerParameters(command=cmd, args=args, env=ENV)
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t = await s.list_tools()
            blob = json.dumps([x.model_dump(exclude_none=True) for x in t.tools])
            per = sorted(((tok(json.dumps(x.model_dump(exclude_none=True))), x.name)
                          for x in t.tools), reverse=True)
            return len(t.tools), tok(blob), per

async def ours():
    sys.path.insert(0, "/opt/mcp-fleet")
    import server3 as v3
    ts = await v3.mcp.list_tools()
    blob = json.dumps([x.model_dump(exclude_none=True) for x in ts])
    per = sorted(((tok(json.dumps(x.model_dump(exclude_none=True))), x.name) for x in ts), reverse=True)
    return len(ts), tok(blob), per

async def main():
    B = "/tmp/rivals/node_modules"
    targets = [
        ("ssh-mcp v2.8.0",            "node", [B + "/ssh-mcp/build/index.js"]),
        ("@mcpcn/ssh-mcp-server 1.0", "node", [B + "/@mcpcn/ssh-mcp-server/build/index.js"]),
        ("mcp-ssh-manager v3.8.5",    "node", [B + "/mcp-ssh-manager/src/index.js"]),
    ]
    rows = []
    for name, cmd, args in targets:
        try:
            rows.append((name,) + await asyncio.wait_for(probe(cmd, args), timeout=45))
        except Exception as e:
            rows.append((name, None, repr(e)[:80], []))
    try:
        rows.append(("OURS fleet v3",) + await ours())
    except Exception as e:
        rows.append(("OURS fleet v3", None, repr(e)[:80], []))

    print("%-28s %6s %11s %9s" % ("server", "tools", "schema tok", "tok/tool"))
    print("-" * 60)
    for name, n, t, per in rows:
        if n is None:
            print("%-28s %6s  %s" % (name, "-", t)); continue
        print("%-28s %6d %11d %9d" % (name, n, t, t // max(1, n)))
    print()
    for name, n, t, per in rows:
        if per:
            print("%-28s biggest: %s" % (name, ", ".join("%s(%d)" % (b, a) for a, b in per[:3])))

asyncio.run(main())