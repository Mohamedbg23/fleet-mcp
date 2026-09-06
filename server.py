#!/usr/bin/env python3
"""
Ops MCP server for a small VM fleet -- v3, aggressively token-optimised.

Every design choice below was chosen by MEASUREMENT (tiktoken o200k_base
against real fleet output), not by taste. See bench.py.

  1. ROUND TRIPS dominate: every call re-sends the whole conversation, so
     `on` takes a group and fans out in PARALLEL. 3 calls -> 1.
  2. NO RETURN ANNOTATION on the tool. A `-> str` makes the SDK advertise an
     `outputSchema` (+46% schema) and wrap every reply in {"result": ...}.
     Dropping it removes both.
  3. SILENCE IS SUCCESS. The host list is already in the CALLER's arguments,
     so echoing it back is pure redundancy. A bare reply means: every target
     agreed and succeeded. Only disagreement and failure get labelled.
  4. POSITIONAL when every host emits one line -- results come back in target
     order, so host names are redundant there too. Falls back to explicit
     `host>` labels the moment any output is multi-line or any host fails,
     because positional output is genuinely ambiguous then.
  5. ONE TOOL, not two. Merging `put` into `sh` via `write=` cut schema 30%.
  6. Hard output cap; one stray `cat` used to cost ~5k tokens.
  7. `bg=True` detaches long jobs so polling doesn't burn a call per check.

Hosts live in /etc/mcp-fleet/hosts.json and are hot-reloaded, so adding a
server never means editing this file or restarting the service.
"""
import base64
import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp.server.fastmcp import FastMCP

AUDIT = Path("/var/log/mcp-fleet.log")
CONF = Path(os.environ.get("MCP_FLEET_HOSTS", "/etc/mcp-fleet/hosts.json"))
KEY = os.environ.get("MCP_FLEET_KEY", "/home/ubuntu/.ssh/fleet")
USER = os.environ.get("MCP_FLEET_USER", "ubuntu")
CAP = int(os.environ.get("MCP_FLEET_CAP", "4000"))
PORT = int(os.environ.get("MCP_PORT", "8765"))

# One compact line per host. Prefixes are dropped and units implied:
# c=cpu%, m=mem%, d=disk%, then keep-alive and fail2ban as +/-.
STATUS_CMD = (
    r"""printf '%s %s c%s m%s d%s ka%s f2b%s\n' """
    r""""$(hostname)" """
    r""""$(cut -d. -f1 /proc/uptime | awk '{printf "%dd%dh", $1/86400, ($1%86400)/3600}')" """
    r""""$(awk '/^cpu /{i=$5+$6;t=0;for(j=2;j<=NF;j++)t+=$j;printf "%d",100-(100*i/t)}' /proc/stat)" """
    r""""$(free -m | awk '/Mem:/{printf "%d", $3*100/$2}')" """
    r""""$(df -h / | awk 'NR==2{sub(/%/,"",$5); print $5}')" """
    r""""$(systemctl is-active oci-keepalive | sed 's/^active$/+/;s/^[a-z].*/-/')" """
    r""""$(systemctl is-active fail2ban | sed 's/^active$/+/;s/^[a-z].*/-/')" """
)

JOBS = {}
_conf_cache = {"mtime": None, "hosts": {}}

mcp = FastMCP("fleet")
mcp.settings.host = "127.0.0.1"
mcp.settings.port = PORT


def _hosts():
    """Hot-reload the host map so adding a server needs no restart."""
    try:
        st = CONF.stat().st_mtime
        if _conf_cache["mtime"] != st:
            _conf_cache["hosts"] = json.loads(CONF.read_text())
            _conf_cache["mtime"] = st
    except Exception:
        pass
    h = {"local": None}
    h.update(_conf_cache["hosts"])
    return h


def _groups(hosts):
    remote = [k for k in hosts if k != "local"]
    return {"all": ["local"] + remote, "remote": remote}


def _audit(kind, detail):
    try:
        with AUDIT.open("a") as fh:
            fh.write("%s %s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), kind, detail))
    except Exception:
        pass


def _targets(on, hosts):
    g = _groups(hosts)
    if on in g:
        return g[on]
    parts = [p.strip() for p in on.split(",") if p.strip()]
    if parts and all(p in hosts for p in parts):
        return parts
    return None


def _target(addr):
    """A host entry may be "user@address" when its SSH login is not the default."""
    return addr if "@" in addr else "%s@%s" % (USER, addr)

def _exec(command, host, timeout, hosts):
    if host == "local":
        argv = ["bash", "-lc", command]
    else:
        argv = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                _target(hosts[host]), command]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ds" % timeout
    err = "\n".join(l for l in p.stderr.splitlines() if "Permanently added" not in l)
    txt = (p.stdout.rstrip() + ("\n" + err.rstrip() if err.strip() else "")).strip()
    return p.returncode, txt


def _cap(txt, budget):
    if len(txt) <= budget:
        return txt
    more = txt[budget:].count("\n") + 1
    return txt[:budget] + "\n...cut, ~%d more lines (narrow with grep/tail)" % more


def _render(res, quiet):
    allok = all(rc == 0 for _, rc, _ in res)
    same = len({(rc, t) for _, rc, t in res}) == 1
    if quiet:
        bad = ["%s:rc%d" % (h, rc) for h, rc, _ in res if rc]
        return "ok" if not bad else "FAIL " + " ".join(bad)
    if same:
        body = _cap(res[0][2], CAP)
        if res[0][1] == 0:
            return body or "ok"          # bare == every target agreed and succeeded
        return "!rc%d %s" % (res[0][1], body)
    budget = max(800, CAP // len(res))
    if allok and all("\n" not in t for _, _, t in res):
        return "\n".join(_cap(t, budget) for _, _, t in res)   # positional, target order
    return "\n".join("%s>%s%s" % (h, "" if rc == 0 else "!rc%d " % rc, _cap(t, budget))
                     for h, rc, t in res)


def _fanout(cmd, targets, timeout, hosts):
    if len(targets) == 1:
        return [(targets[0],) + _exec(cmd, targets[0], timeout, hosts)]
    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        f = {h: ex.submit(_exec, cmd, h, timeout, hosts) for h in targets}
        return [(h,) + f[h].result() for h in targets]


@mcp.tool()
def sh(cmd: str, on: str = "local", timeout: int = 60, quiet: bool = False,
       bg: bool = False, write: str = "", sudo: bool = False):
    """Fleet shell, parallel. on: host|all|remote|comma-list. Bare reply=all
    targets agreed+succeeded; else one line per host in target order, 'host>out'
    if multiline, !rcN on failure. '#status'=health, '#job ID'=collect.
    quiet=rc only. bg=detach>170s. write=PATH writes cmd to that file."""
    hosts = _hosts()
    if cmd.startswith("#job"):
        b = cmd.split()
        jid = b[1] if len(b) > 1 else ""
        if jid not in JOBS:
            return "no such job %s" % jid
        return JOBS.pop(jid) if JOBS[jid] is not None else "job %s running" % jid

    targets = _targets(on, hosts)
    if targets is None:
        return "bad host %r; have %s" % (on, list(hosts) + list(_groups(hosts)))

    if write:
        b64 = base64.b64encode(cmd.encode()).decode()
        tee = "sudo tee" if sudo else "tee"
        real = "echo %s | base64 -d | %s -- %r >/dev/null && wc -c < %r" % (b64, tee, write, write)
        _audit("PUT", "%s :: %s (%dB)" % (",".join(targets), write, len(cmd)))
    else:
        real = STATUS_CMD if cmd.strip() == "#status" else cmd
        _audit("RUN", "%s :: %s" % (",".join(targets), real[:200]))

    if bg:
        jid = uuid.uuid4().hex[:6]
        JOBS[jid] = None

        def work():
            try:
                JOBS[jid] = _render(_fanout(real, targets, timeout, hosts), quiet)
            except Exception as e:
                JOBS[jid] = "error: %s" % e
        threading.Thread(target=work, daemon=True).start()
        return "job %s on %s" % (jid, ",".join(targets))

    return _render(_fanout(real, targets, timeout, hosts), quiet)


def _slim():
    """Strip pydantic's cosmetic 'title' keys -- pure context cost, every turn."""
    for t in mcp._tool_manager._tools.values():
        t.parameters.pop("title", None)
        for p in t.parameters.get("properties", {}).values():
            p.pop("title", None)


_slim()

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
