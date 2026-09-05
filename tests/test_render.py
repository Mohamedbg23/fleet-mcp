"""Correctness suite for v3 rendering. Token savings are worthless if wrong."""
import json, sys, time, pathlib
sys.path.insert(0, "/opt/mcp-fleet")
import server3 as v3

P = 0; F = 0
def ck(name, got, want):
    global P, F
    ok = (got == want) if not callable(want) else want(got)
    print("%s %-34s %r" % ("PASS" if ok else "FAIL", name, got))
    if ok: P += 1
    else:
        F += 1
        if not callable(want): print("      expected %r" % (want,))

R = lambda rows, q=False: v3._render(rows, q)
# rows are (host, rc, text)
ck("all ok, identical -> bare", R([("a",0,"x"),("b",0,"x")]), "x")
ck("all ok, empty -> ok",       R([("a",0,""),("b",0,"")]), "ok")
ck("all fail, identical",       R([("a",2,"boom"),("b",2,"boom")]), "!rc2 boom")
ck("ok, divergent 1-line -> positional",
   R([("a",0,"one"),("b",0,"two")]), "one\ntwo")
ck("ok, divergent multiline -> named",
   R([("a",0,"l1\nl2"),("b",0,"z")]), "a>l1\nl2\nb>z")
ck("one host fails -> named + !rc",
   R([("a",0,"fine"),("b",3,"bad")]), "a>fine\nb>!rc3 bad")
ck("quiet all ok",              R([("a",0,"x"),("b",0,"x")], True), "ok")
ck("quiet one fail",            R([("a",0,""),("b",3,"")], True), "FAIL b:rc3")

print("--- live fleet ---")
ck("identical across fleet", v3.sh("systemctl is-active oci-keepalive","all"), "active")
ck("divergent across fleet", v3.sh("hostname","all"), "ubuntu-a1\nmicro-1\nmicro-2")
ck("quiet ok", v3.sh("true","all",quiet=True), "ok")
ck("quiet failure names host", v3.sh("test -f /nope","micro-1",quiet=True), "FAIL micro-1:rc1")
ck("status shape", v3.sh("#status","all"),
   lambda g: len(g.splitlines())==3 and "ka+" in g and g.startswith("ubuntu-a1"))
ck("bad host rejected", v3.sh("x","bogus"), lambda g: g.startswith("bad host"))
ck("cap fires", v3.sh("seq 1 9999","local"), lambda g: "...cut" in g and len(g)<4200)
ck("write via sh", v3.sh("hello v3\n","all",write="/tmp/w3.txt"), "9")
ck("readback", v3.sh("cat /tmp/w3.txt","all"), "hello v3")
j = v3.sh("sleep 2; echo done","all",bg=True)
ck("bg dispatch", j, lambda g: g.startswith("job "))
ck("bg still running", v3.sh("#job "+j.split()[1]), lambda g: "running" in g)
time.sleep(5)
ck("bg collect", v3.sh("#job "+j.split()[1]), "done")
ck("bg gone after collect", v3.sh("#job "+j.split()[1]), lambda g: g.startswith("no such job"))

print("--- hot reload of hosts.json (no restart) ---")
c = pathlib.Path("/etc/mcp-fleet/hosts.json"); orig = c.read_text()
ck("before: 3 targets", str(v3._targets("all", v3._hosts())),
   "['local', 'micro-1', 'micro-2']")
try:
    c.write_text(json.dumps({"micro-1":"10.0.0.11","micro-2":"10.0.0.12","micro-9":"10.0.0.99"}))
    time.sleep(0.05)
    ck("after: picks up micro-9 live", str(v3._targets("all", v3._hosts())),
       "['local', 'micro-1', 'micro-2', 'micro-9']")
    ck("unreachable host reported", v3.sh("true","micro-9",timeout=12,quiet=True),
       lambda g: g.startswith("FAIL micro-9"))
finally:
    c.write_text(orig); time.sleep(0.05); v3._hosts()
ck("restored", str(v3._targets("all", v3._hosts())), "['local', 'micro-1', 'micro-2']")
print("\n%d passed, %d failed" % (P, F))
