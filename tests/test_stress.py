"""Round 4 stress suite: property tests + adversarial edge cases.

The dangerous claim v3 makes is 'a bare reply means everything succeeded'.
If that can EVER be false, the token saving is not worth having. So we
fuzz it rather than hand-picking cases.
"""
import itertools, random, sys, time
sys.path.insert(0, "/opt/mcp-fleet")
import server3 as v3

random.seed(7)
FRAGS = ["", "ok", "active", "line1\nline2", "  padded  ", "!rc9 fake", "a>b",
         "unicode: éàü 中文 🚀", "x" * 500, "tab\there", "quote\"'`$(x)",
         "\n\n", "FAIL b:rc1", "ok\n", "-", "0"]
HOSTS = ["local", "micro-1", "micro-2", "micro-9"]

fails = []
def bad(msg, *a):
    fails.append(msg % a)

# ---------- property fuzz: 8000 random result sets ----------
for _ in range(8000):
    n = random.randint(1, 4)
    rows = [(HOSTS[i], random.choice([0, 0, 0, 1, 2, 127, 255]), random.choice(FRAGS))
            for i in range(n)]
    anyfail = any(rc for _, rc, _ in rows)
    allok = not anyfail
    same = len({(rc, t) for _, rc, t in rows}) == 1

    out = v3._render(rows, False)
    q = v3._render(rows, True)

    # P1: a failure can NEVER render as a bare success
    if anyfail and "!rc" not in out:
        bad("P1 loud: failure hidden -> %r from %r", out, rows)
    if anyfail and not q.startswith("FAIL"):
        bad("P1 quiet: failure hidden -> %r from %r", q, rows)
    # P2: quiet success is exactly 'ok'
    if allok and q != "ok":
        bad("P2: quiet ok wrong -> %r", q)
    # P3: unanimous success returns the payload itself (or 'ok' when empty)
    if allok and same:
        want = rows[0][2].strip() and rows[0][2] or "ok"
        if out != (rows[0][2] if rows[0][2] else "ok"):
            bad("P3: unanimous payload altered -> %r vs %r", out, rows[0][2])
    # P4: divergence must remain attributable
    if allok and not same:
        singles = all("\n" not in t for _, _, t in rows)
        if singles:
            if out.split("\n") != [t for _, _, t in rows]:
                bad("P4a: positional lost order/content -> %r", out)
        else:
            for h, _, _ in rows:
                if h + ">" not in out:
                    bad("P4b: host %s unlabelled in %r", h, out)

print("property fuzz: 8000 cases, %d violations" % len(fails))
for f in fails[:5]:
    print("   ", f)

# ---------- adversarial live cases ----------
P = F = 0
def ck(name, got, want):
    global P, F
    ok = want(got) if callable(want) else got == want
    print("%s %-38s %s" % ("PASS" if ok else "FAIL", name, repr(got)[:70]))
    P, F = (P + 1, F) if ok else (P, F + 1)

print("\n-- adversarial live --")
ck("unicode survives", v3.sh("printf 'éàü 中文 🚀'", "all"), "éàü 中文 🚀")
ck("stderr-only captured", v3.sh("echo oops >&2; true", "local"), "oops")
ck("no output -> ok", v3.sh("true", "all"), "ok")
ck("nonzero + output labelled", v3.sh("echo bad; exit 7", "local"),
   lambda g: g.startswith("!rc7"))
ck("timeout reported", v3.sh("sleep 30", "local", timeout=3),
   lambda g: "timed out" in g and g.startswith("!rc124"))
ck("very long single line capped", v3.sh("head -c 20000 /dev/zero | tr '\\0' 'a'", "local"),
   lambda g: "...cut" in g)
ck("multi-host cap splits budget", v3.sh("seq 1 4000", "all"),
   lambda g: len(g) < 5000 and g.count("...cut") >= 1)
ck("binary is not fatal", v3.sh("head -c 200 /dev/urandom | base64 | head -1", "local"),
   lambda g: len(g) > 10 and "!rc" not in g)
ck("quotes/newlines via write",
   v3.sh("a\"b'c`d$(echo x)\nsecond\n", "all", write="/tmp/q.txt"), "24")
ck("write readback exact", v3.sh("cat /tmp/q.txt", "all"),
   lambda g: g == "a\"b'c`d$(echo x)\nsecond")
ck("dead host mixed with live", v3.sh("echo alive", "local,micro-1", quiet=True), "ok")

print("\n-- concurrency: 3 bg jobs at once --")
js = [v3.sh("sleep 2; echo j%d" % i, "all", bg=True) for i in range(3)]
time.sleep(6)
res = [v3.sh("#job " + j.split()[1]) for j in js]
ck("3 concurrent bg jobs distinct", res, ["j0", "j1", "j2"])

print("\n-- hosts.json robustness --")
import pathlib, json as J
c = pathlib.Path("/etc/mcp-fleet/hosts.json"); orig = c.read_text()
try:
    c.write_text("{ this is not json")
    time.sleep(0.05)
    ck("corrupt config falls back safely", str(list(v3._hosts())),
       lambda g: "local" in g)
    ck("still runs locally", v3.sh("echo survived", "local"), "survived")
finally:
    c.write_text(orig); time.sleep(0.05); v3._hosts()
ck("config restored", str(v3._targets("all", v3._hosts())),
   "['local', 'micro-1', 'micro-2']")

print("\n%d passed, %d failed, %d property violations" % (P, F, len(fails)))