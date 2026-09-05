# fleet-mcp

**One MCP tool that runs a command on every server at once — built to burn as few tokens as possible.**

Most SSH MCP servers give your AI agent a dozen tools and make it talk to one host per call. That is expensive twice over: the tool schemas sit in the context window on *every* turn, and checking three servers costs three round trips, each one re-sending the whole conversation.

`fleet-mcp` is the opposite. **One tool. One call. Every host, in parallel.**

```
sh(cmd="systemctl is-active nginx", on="all")
→ active
```

Three servers checked. Four tokens back. If every host agrees and succeeds, you get the answer and nothing else — no host names, no exit codes, no JSON wrapper.

---

## Measured against real alternatives

Not estimates — the competing servers were installed and introspected, then run end-to-end on the same 20-step ops session.

| server | tools | schema tokens |
|---|---|---|
| `mcp-ssh-manager` v3.8.5 | 37 | 9,801 |
| `ssh-mcp` v2.8.0 | 11 | 1,631 |
| **fleet-mcp** | **1** | **212** |

Realistic 20-step session across 3 hosts:

| | calls | payload tokens | with schema |
|---|---|---|---|
| `ssh-mcp` v2.8.0 | 53 | 1,694 | 88,137 |
| fleet-mcp | **19** | **727** | **4,755** |

**−64% calls, −57% payload, −95% total.** Reproduce it yourself: `bench/probe.py` and `bench/bigbench.py`.

*Fairness note:* `ssh-mcp` returns lean raw text and is a well-built server. Our payload edge comes from fanning out to many hosts in one call, not from a cleverer response format.

---

## Setup

**What you need:** one server with a public IP and a domain name pointing at it. A free [DuckDNS](https://duckdns.org) subdomain works fine. That box becomes the **hub**.

Your other servers stay completely private — they never need a public IP, and **you never log into them to install anything**. The hub reaches them over SSH and does the work.

Every command below says which machine to run it on. There are no hidden steps.

### Step 1 — on the HUB: install

```bash
# >>> RUN THIS ON THE HUB <<<
git clone https://github.com/Mohamedbg23/fleet-mcp.git
cd fleet-mcp
sudo ./install.sh your-name.duckdns.org
```

When it finishes it prints your connector URL:

```
https://your-name.duckdns.org/a1b2c3d4e5f6.../mcp
```

The installer also copies the `fleet-add` command to `/usr/local/bin/fleet-add` **on the hub**, so from Step 3 onward you can run `fleet-add` from any folder — but always while logged into the hub.

### Step 2 — in your MCP client: connect

Add that URL as a **custom / remote MCP connector**. That's it — you can now run commands on the hub.

### Step 3 — on the HUB: add your other servers

Run this once per server you want to manage. **On the hub, not on the server you're adding.**

```bash
# >>> RUN THIS ON THE HUB <<<
fleet-add web-1 10.0.0.11 ~/web-1.key
```

| argument | meaning |
|---|---|
| `web-1` | any name you like — this is what you'll type in `on="web-1"` |
| `10.0.0.11` | the address **the hub** uses to reach it (a private IP is ideal) |
| `~/web-1.key` | the SSH key you *already* use for that server — **a file on the hub** |

The key is only needed this one time: `fleet-add` uses it to install the shared fleet key on the new box, and everything afterwards uses the fleet key. If that key currently lives on your laptop, copy it to the hub first:

```bash
# >>> RUN THIS ON YOUR LAPTOP <<<
scp ~/.ssh/web-1.key ubuntu@your-name.duckdns.org:~/
```

Already able to SSH from the hub to that server? Then drop the key argument entirely:

```bash
# >>> ON THE HUB <<<
fleet-add web-1 10.0.0.11
```

Optional extras, same command — harden sshd + install fail2ban, and/or the idle guard:

```bash
# >>> ON THE HUB <<<
fleet-add web-1 10.0.0.11 ~/web-1.key --harden --keepalive
```

**New hosts join `on="all"` immediately.** `hosts.json` is hot-reloaded — no restart, no code to edit, nothing to redeploy.

### Where everything lives (all on the hub)

| path | what it is |
|---|---|
| `/usr/local/bin/fleet-add` | the add-a-server command |
| `/etc/mcp-fleet/hosts.json` | your host list — `fleet-add` writes it, you can hand-edit it too |
| `/opt/mcp-fleet/server.py` | the server itself |
| `~/.ssh/fleet` | the shared fleet key |
| `/etc/caddy/Caddyfile` | your domain + the secret URL path |
| `/var/log/mcp-fleet.log` | audit log of every command run |
| `/usr/local/bin/harden-ssh.sh`, `oci-keepalive.sh` | the optional extras |

### Check it worked

```bash
# >>> ON THE HUB <<<
systemctl status mcp-fleet          # should be active
cat /etc/mcp-fleet/hosts.json       # your servers
```

Then from your MCP client:

```
sh(cmd="#status", on="all")
```

You should get one line per server. If you do, you're done.

### Adding a server by hand (no fleet-add)

`fleet-add` is only a convenience. The two things that actually matter:

1. the hub's `~/.ssh/fleet.pub` is in that server's `~/.ssh/authorized_keys`
2. the server is listed in `/etc/mcp-fleet/hosts.json` as `{"name": "address"}`

Do those two things and it works.

---

## Using it

| you want | you write |
|---|---|
| one host | `sh(cmd="uptime")` |
| every host, in parallel | `sh(cmd="uptime", on="all")` |
| everything except the hub | `sh(cmd="uptime", on="remote")` |
| a couple of hosts | `sh(cmd="uptime", on="web-1,db-2")` |
| health of the whole fleet | `sh(cmd="#status", on="all")` |
| don't care about output | `sh(cmd="apt-get update", on="all", quiet=True)` |
| longer than ~3 minutes | `sh(cmd="...", on="all", bg=True)` → `sh(cmd="#job ID")` |
| write a file | `sh(cmd="<file body>", write="/etc/app.conf", on="all", sudo=True)` |

### Reading the replies

The format is terse on purpose. **The host list is already in your own request, so echoing it back is wasted tokens.**

```
active                      every target agreed and succeeded
ok                          succeeded, produced no output
!rc7 permission denied      every target failed the same way
web-1                       hosts disagree: one line each, in the order you asked
db-2
web-1>line one              hosts disagree and output is multi-line, so hosts are named
line two
db-2>something else
db-2>!rc3 failed            a specific host failed
FAIL db-2:rc3               quiet mode; success is just "ok"
```

**A bare reply always means everything worked.** That is the whole point, and it is fuzz-tested: 8,000 randomized cases — including payloads that deliberately contain strings like `!rc9 fake` and `FAIL b:rc1` — confirm a failure can never be rendered as a bare success. See `tests/test_stress.py`.

---

## Why it's cheap

Every one of these was chosen by measurement, not taste.

1. **Round trips dominate.** Each tool call re-sends the entire conversation, so one fan-out call beats three sequential ones by far more than any formatting trick.
2. **No return type annotation on the tool.** A `-> str` makes the Python SDK advertise an `outputSchema`, which costs ~46% more schema *and* wraps every reply in `{"result": ...}`. Dropping the annotation removes both.
3. **Silence is success.** Bare output when all targets agree.
4. **Positional when unambiguous.** If every host emits a single line, they come back in target order with no labels. The moment output is multi-line or a host fails, explicit `host>` labels return — positional output would be genuinely ambiguous there.
5. **One tool, not two.** Merging file-writing into `sh` via `write=` cut schema 30%.
6. **Stripped `title` keys.** Pydantic auto-generates a cosmetic `"title"` for every parameter. Pure context cost, every turn.
7. **Hard output cap** (4,000 chars, split across hosts). One stray `cat` used to cost ~5,000 tokens.
8. **Background jobs.** Polling a slow state change used to burn a call per check.

**Things that did *not* work,** so you don't waste time: shortening parameter names (`cmd`→`c`) saves **exactly zero tokens** — measured 267 both ways. And [TOON](https://github.com/toon-format/toon) doesn't apply: it compresses uniform arrays of objects, while shell output is plain text, where raw text already wins.

---

## Security — read this

This gives an AI agent a shell on every host in the fleet. That is the point, and it is dangerous.

- **The connector URL is a root password.** It is the only thing protecting the endpoint. Don't paste it anywhere.
- **The service is not enabled at boot, deliberately.** `sudo systemctl stop mcp-fleet` when you are not actively working. Start it when you need it.
- **Don't point it at a root account.** Use an unprivileged user with `sudo` where needed.
- **Prompt injection has no general fix.** Anything your agent reads — a log line, a file, a web page — could try to steer it. Keep the blast radius small.
- Every action is logged to `/var/log/mcp-fleet.log`.
- Consider firewalling port 22 on your hosts to your own network plus your admin IP.

If you want policy enforcement, command classification and human-in-the-loop approval, [`ssh-mcp`](https://github.com/kpanuragh/ssh-mcp) is built for that and is a reasonable choice. `fleet-mcp` optimizes for token efficiency instead.

---

## Extras

`harden-ssh.sh` fixes a failure mode that is easy to misdiagnose: a public port 22 gets flooded by brute-force bots, and OpenSSH's default `MaxStartups` (10:30:100) lets ~10 concurrent pre-auth connections starve the listener. Your TCP handshake completes, no banner ever arrives, and the box looks dead while being perfectly healthy. This raises the ceiling and adds a correctly-configured fail2ban (`aggressive` mode, and the right `journalmatch` for Ubuntu 24.04, where sshd runs under `ssh.service`).

`oci-keepalive.sh` is for free-tier VMs that get reclaimed when idle (Oracle Always Free reclaims only if CPU **and** network **and** memory are all under 20% over 7 days — so holding one metric up is enough). It is a feedback loop, not a fixed load: it measures real CPU every 30s and burns only the shortfall, so when your own workload is busy it contributes nothing. With `Nice=19`/`CPUWeight=1` the kernel hands cycles straight back on demand.

---

## Requirements

Python 3.10+, `mcp<2` (installed automatically), Caddy (installed automatically), Debian/Ubuntu. The hosts you manage need only SSH.

## License

MIT