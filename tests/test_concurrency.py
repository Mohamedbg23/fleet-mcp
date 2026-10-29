"""Many conversations share one server: calls must run in parallel and never hang."""
import asyncio, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("MCP_FLEET_HOSTS", "/nonexistent")
import server  # noqa: E402

def run(*calls):
    async def go():
        return await asyncio.gather(*calls)
    return asyncio.run(go())

def test_two_calls_run_in_parallel():
    t = time.monotonic()
    out = run(server.sh("sleep 1; echo a"), server.sh("sleep 1; echo b"))
    assert out == ["a", "b"] and time.monotonic() - t < 1.8

def test_timeout_kills_children_holding_the_pipe():
    t = time.monotonic()
    out, = run(server.sh("sleep 30 & sleep 30 & wait", timeout=1))
    assert out.startswith("!rc124 timed out") and time.monotonic() - t < 8

def test_long_foreground_call_becomes_a_job():
    out, = run(server.sh("echo hi", timeout=999))
    assert out.startswith("job ") and "auto-bg" in out
    jid = out.split()[1]
    for _ in range(50):
        got, = run(server.sh("#job %s" % jid))
        if "running" not in got:
            break
        time.sleep(0.1)
    assert got == "hi"

def test_stateless_http():
    assert server.mcp.settings.stateless_http is True

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
