"""Three-way benchmark: v1 (original) vs v2 (live) vs v3 (candidate)."""
import importlib.util, json, sys, time
import tiktoken

E = tiktoken.get_encoding("o200k_base")
tok = lambda s: len(E.encode(s))
sys.path.insert(0, "/opt/mcp-fleet")
import server as v2
import server3 as v3
_s = importlib.util.spec_from_file_location("v1", "/opt/mcp-fleet/server-v1.py")
v1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(v1)

w1 = lambda d: tok(json.dumps(d, indent=2))      # dict -> pretty JSON
w2 = lambda s: tok(json.dumps({"result": s}))    # str  -> structured wrapper
w3 = lambda s: tok(s)                            # str  -> raw text, no wrapper
A = lambda d: tok(json.dumps(d))                 # call arguments cost context too

def schema(mcp):
    import asyncio
    ts = asyncio.get_event_loop().run_until_complete(mcp.list_tools()) \
        if False else None
    return None

def sch(mcp):
    import asyncio
    async def go():
        ts = await mcp.list_tools()
        return tok(json.dumps([t.model_dump(exclude_none=True) for t in ts]))
    return asyncio.run(go())

S1, S2, S3 = sch(v1.mcp), sch(v2.mcp), sch(v3.mcp)
H = ["local", "micro-1", "micro-2"]
rows = []
def add(l, c1, t1, c2, t2, c3, t3): rows.append((l, c1, t1, c2, t2, c3, t3))

# 1 health
add("fleet health snapshot",
    1, A({}) + w1(v1.fleet_status()),
    1, A({"cmd": "#status", "on": "all"}) + w2(v2.sh("#status", "all")),
    1, A({"cmd": "#status", "on": "all"}) + w3(v3.sh("#status", "all")))
# 2 identical
C = "systemctl is-active oci-keepalive fail2ban | tr '\\n' ' '"
add("service check x3 (identical)",
    3, sum(A({"command": C, "host": h}) + w1(v1.run(C, h)) for h in H),
    1, A({"cmd": C, "on": "all"}) + w2(v2.sh(C, "all")),
    1, A({"cmd": C, "on": "all"}) + w3(v3.sh(C, "all")))
# 3 differing
add("hostname x3 (differing)",
    3, sum(A({"command": "hostname", "host": h}) + w1(v1.run("hostname", h)) for h in H),
    1, A({"cmd": "hostname", "on": "all"}) + w2(v2.sh("hostname", "all")),
    1, A({"cmd": "hostname", "on": "all"}) + w3(v3.sh("hostname", "all")))
# 4 rc only
C4 = "systemctl is-active ssh >/dev/null"
add("action x3 (rc only)",
    3, sum(A({"command": C4, "host": h}) + w1(v1.run(C4, h)) for h in H),
    1, A({"cmd": C4, "on": "all", "quiet": True}) + w2(v2.sh(C4, "all", quiet=True)),
    1, A({"cmd": C4, "on": "all", "quiet": True}) + w3(v3.sh(C4, "all", quiet=True)))
# 5 read config
P = "/etc/ssh/sshd_config.d/90-hardening.conf"
add("read config (1 host)",
    1, A({"path": P}) + w1(v1.read_file(P)),
    1, A({"cmd": "cat " + P}) + w2(v2.sh("cat " + P)),
    1, A({"cmd": "cat " + P}) + w3(v3.sh("cat " + P)))
# 6 write x3
B = "# benchmark scratch file\nkey=value\n"
add("write file x3",
    3, sum(A({"path": "/tmp/b.txt", "content": B, "host": h}) + w1(v1.write_file("/tmp/b.txt", B, h)) for h in H),
    1, A({"path": "/tmp/b.txt", "content": B, "on": "all"}) + w2(v2.put("/tmp/b.txt", B, "all")),
    1, A({"cmd": B, "write": "/tmp/b.txt", "on": "all"}) + w3(v3.sh(B, "all", write="/tmp/b.txt")))
# 7 polling
C7 = "systemctl is-active mcp-fleet"
p1 = sum(A({"command": C7, "host": "local"}) + w1(v1.run(C7, "local")) for _ in range(6))
j2 = v2.sh("sleep 1; " + C7, "local", bg=True); time.sleep(3)
p2 = A({"cmd": C7, "on": "local", "bg": True}) + w2(j2) + A({"cmd": "#job " + j2.split()[1]}) + w2(v2.sh("#job " + j2.split()[1]))
j3 = v3.sh("sleep 1; " + C7, "local", bg=True); time.sleep(3)
p3 = A({"cmd": C7, "on": "local", "bg": True}) + w3(j3) + A({"cmd": "#job " + j3.split()[1]}) + w3(v3.sh("#job " + j3.split()[1]))
add("poll state 6x -> bg job", 6, p1, 2, p2, 2, p3)
# 8 grep
C8 = "grep MaxStartups /etc/ssh/sshd_config.d/90-hardening.conf"
add("grep setting x3",
    3, sum(A({"command": C8, "host": h}) + w1(v1.run(C8, h)) for h in H),
    1, A({"cmd": C8, "on": "all"}) + w2(v2.sh(C8, "all")),
    1, A({"cmd": C8, "on": "all"}) + w3(v3.sh(C8, "all")))

pc = lambda a, b: "-%d%%" % round((a - b) * 100.0 / a) if a else "-"
print("%-28s %11s %11s %11s" % ("operation", "v1", "v2", "v3"))
print("-" * 64)
C1 = T1 = C2 = T2 = C3 = T3 = 0
for l, c1, t1, c2, t2, c3, t3 in rows:
    print("%-28s %4d/%5d %4d/%5d %4d/%5d" % (l, c1, t1, c2, t2, c3, t3))
    C1 += c1; T1 += t1; C2 += c2; T2 += t2; C3 += c3; T3 += t3
print("-" * 64)
print("%-28s %4d/%5d %4d/%5d %4d/%5d   (calls/tokens)" % ("TOTAL", C1, T1, C2, T2, C3, T3))
print()
print("schema advertised      v1 %4d   v2 %4d   v3 %4d" % (S1, S2, S3))
print("payload tokens         v1 %4d   v2 %4d   v3 %4d   v3 vs v1 %s | vs v2 %s" % (T1, T2, T3, pc(T1, T3), pc(T2, T3)))
print("round trips            v1 %4d   v2 %4d   v3 %4d   v3 vs v1 %s" % (C1, C2, C3, pc(C1, C3)))
g = lambda T, S, C: T + S * C
print()
print("GRAND TOTAL (schema re-billed per turn)")
print("  v1 %6d   v2 %6d   v3 %6d   v3 vs v1 %s | vs v2 %s"
      % (g(T1, S1, C1), g(T2, S2, C2), g(T3, S3, C3),
         pc(g(T1, S1, C1), g(T3, S3, C3)), pc(g(T2, S2, C2), g(T3, S3, C3))))
print("CONSERVATIVE (schema counted once)")
print("  v1 %6d   v2 %6d   v3 %6d   v3 vs v1 %s | vs v2 %s"
      % (T1 + S1, T2 + S2, T3 + S3, pc(T1 + S1, T3 + S3), pc(T2 + S2, T3 + S3)))
