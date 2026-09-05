"""'Day in the life' benchmark: a realistic 20-step ops session.
Compares v1, v2, v3 and the real ssh-mcp v2.8.0 on identical work."""
import asyncio, importlib.util, json, os, sys, time
import tiktoken
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

E = tiktoken.get_encoding("o200k_base"); tok = lambda s: len(E.encode(s))
A = lambda d: tok(json.dumps(d))
sys.path.insert(0, "/opt/mcp-fleet")
import server as v2X          # currently deployed (v3 code) -- import v2 from backup instead
import server3 as v3
_s = importlib.util.spec_from_file_location("v1", "/opt/mcp-fleet/server-v1.py")
v1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(v1)
_s2 = importlib.util.spec_from_file_location("v2", "/opt/mcp-fleet/server-v2.py")
v2 = importlib.util.module_from_spec(_s2); _s2.loader.exec_module(v2)

H = ["local", "micro-1", "micro-2"]
CFG = "# managed by fleet\nrefresh=30\n"

# (label, command, fleetwide?, quiet?)
SESSION = [
 ("health snapshot",      "#status", True, False),
 ("keepalive active",     "systemctl is-active oci-keepalive", True, False),
 ("fail2ban active",      "systemctl is-active fail2ban", True, False),
 ("sshd startups ceiling","pgrep -a sshd | grep -o 'of [0-9-]*' | head -1", True, False),
 ("disk usage",           "df -h / | awk 'NR==2{print $5}'", True, False),
 ("memory pct",           "free -m | awk '/Mem:/{printf \"%d\", $3*100/$2}'", True, False),
 ("load average",         "cut -d' ' -f1-3 /proc/loadavg", True, False),
 ("failed units",         "systemctl list-units --failed --no-legend | wc -l", True, False),
 ("bruteforce count",     "sudo journalctl -u ssh --since '1 hour ago' --no-pager 2>/dev/null | grep -ci 'invalid user'", True, False),
 ("kernel version",       "uname -r", True, False),
 ("listening ports",      "ss -ltn | wc -l", True, False),
 ("ssh hardening grep",   "grep MaxAuthTries /etc/ssh/sshd_config.d/90-hardening.conf", True, False),
 ("timezone",             "timedatectl show -p Timezone --value", True, False),
 ("python version",       "python3 -V", True, False),
 ("cleanup tmp",          "rm -f /tmp/fleet-scratch 2>/dev/null; true", True, True),
 ("verify cleanup",       "test ! -f /tmp/fleet-scratch", True, True),
 ("read local config",    "cat /etc/mcp-fleet/hosts.json", False, False),
 ("tail a log",           "sudo tail -3 /var/log/mcp-fleet.log", False, False),
]

def run_v1():
    c = t = 0
    for label, cmd, fleet, quiet in SESSION:
        if label == "health snapshot":
            c += 1; t += A({}) + tok(json.dumps(v1.fleet_status(), indent=2)); continue
        hosts = H if fleet else ["local"]
        for h in hosts:
            c += 1; t += A({"command": cmd, "host": h}) + tok(json.dumps(v1.run(cmd, h), indent=2))
    # write config to all 3
    for h in H:
        c += 1; t += A({"path": "/tmp/f.conf", "content": CFG, "host": h}) \
                   + tok(json.dumps(v1.write_file("/tmp/f.conf", CFG, h), indent=2))
    return c, t

def run_mod(mod, wrap):
    c = t = 0
    for label, cmd, fleet, quiet in SESSION:
        on = "all" if fleet else "local"
        args = {"cmd": cmd, "on": on}
        if quiet: args["quiet"] = True
        c += 1; t += A(args) + wrap(mod.sh(cmd, on, quiet=quiet))
    if mod is v3:
        c += 1; t += A({"cmd": CFG, "write": "/tmp/f.conf", "on": "all"}) + wrap(v3.sh(CFG, "all", write="/tmp/f.conf"))
    else:
        c += 1; t += A({"path": "/tmp/f.conf", "content": CFG, "on": "all"}) + wrap(mod.put("/tmp/f.conf", CFG, "all"))
    return c, t

async def run_rival():
    """ssh-mcp handles one host per process -> per-host-operation cost x3."""
    p = StdioServerParameters(command="node",
        args=["/tmp/rivals/node_modules/ssh-mcp/build/index.js"], env=dict(os.environ))
    c = t = 0
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for label, cmd, fleet, quiet in SESSION:
                real = "hostname; uptime; free -m|head -2" if cmd == "#status" else cmd
                mult = 3 if fleet else 1
                res = await s.call_tool("run-command", {"command": real})
                body = "\n".join(getattr(x, "text", "") for x in res.content)
                st = getattr(res, "structuredContent", None)
                per = A({"command": real}) + tok(body) + (tok(json.dumps(st)) if st else 0)
                c += mult; t += per * mult
            res = await s.call_tool("run-command", {"command": "printf %s " + json.dumps(CFG) + " > /tmp/f.conf"})
            body = "\n".join(getattr(x, "text", "") for x in res.content)
            c += 3; t += (A({"command": "write"}) + tok(body)) * 3
    return c, t

async def main():
    c1, t1 = run_v1()
    c2, t2 = run_mod(v2, lambda s: tok(json.dumps({"result": s})))
    c3, t3 = run_mod(v3, tok)
    try:
        cr, tr = await asyncio.wait_for(run_rival(), timeout=200)
    except Exception as e:
        cr, tr = None, repr(e)[:60]
    S = {"v1": 401, "v2": 326, "v3": 212, "rival": 1631}
    print("%-22s %7s %9s %11s" % ("implementation", "calls", "payload", "with schema"))
    print("-" * 54)
    rows = [("ssh-mcp v2.8.0", cr, tr, S["rival"]), ("ours v1", c1, t1, S["v1"]),
            ("ours v2", c2, t2, S["v2"]), ("ours v3", c3, t3, S["v3"])]
    base = None
    for n, c, t, s in rows:
        if c is None: print("%-22s   FAILED %s" % (n, t)); continue
        tot = t + s * c
        if base is None: base = tot
        print("%-22s %7d %9d %11d" % (n, c, t, tot))
    print()
    if cr:
        print("v3 vs ssh-mcp : calls -%d%%  payload -%d%%  total -%d%%"
              % (round((cr-c3)*100.0/cr), round((tr-t3)*100.0/tr),
                 round(((tr+S['rival']*cr)-(t3+S['v3']*c3))*100.0/(tr+S['rival']*cr))))
    print("v3 vs ours v1 : calls -%d%%  payload -%d%%  total -%d%%"
          % (round((c1-c3)*100.0/c1), round((t1-t3)*100.0/t1),
             round(((t1+S['v1']*c1)-(t3+S['v3']*c3))*100.0/(t1+S['v1']*c1))))

asyncio.run(main())