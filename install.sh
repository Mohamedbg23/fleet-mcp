#!/usr/bin/env bash
# fleet-mcp installer. Run ON your hub server (the one with a public IP):
#
#   sudo ./install.sh your-name.duckdns.org
#
# The secret URL path is generated for you. Prints the connector URL at the end.
set -euo pipefail

FQDN="${1:-}"
if [ -z "$FQDN" ]; then
  echo "usage: sudo ./install.sh <your-domain> [secret-path]" >&2
  echo "  e.g. sudo ./install.sh my-fleet.duckdns.org" >&2
  exit 1
fi
SECRET="${2:-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
SVC_USER="${SUDO_USER:-ubuntu}"
HOME_DIR="$(getent passwd "$SVC_USER" | cut -d: -f6)"
SRC="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv curl gnupg debian-keyring debian-archive-keyring apt-transport-https

echo "==> caddy (TLS + the secret path)"
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi

echo "==> server"
install -d -o "$SVC_USER" -g "$SVC_USER" /opt/mcp-fleet
install -o "$SVC_USER" -g "$SVC_USER" -m 640 "$SRC/server.py" /opt/mcp-fleet/server.py
sudo -u "$SVC_USER" python3 -m venv /opt/mcp-fleet/venv
# NOTE: mcp 2.x renamed FastMCP; this server targets the 1.x API.
sudo -u "$SVC_USER" /opt/mcp-fleet/venv/bin/pip install -q --upgrade pip "mcp<2"
touch /var/log/mcp-fleet.log && chown "$SVC_USER":"$SVC_USER" /var/log/mcp-fleet.log

echo "==> host list (add servers later with ./fleet-add)"
install -d -m 755 /etc/mcp-fleet
[ -f /etc/mcp-fleet/hosts.json ] || echo '{}' > /etc/mcp-fleet/hosts.json
chown "$SVC_USER":"$SVC_USER" /etc/mcp-fleet/hosts.json

echo "==> fleet ssh key"
if [ ! -f "$HOME_DIR/.ssh/fleet" ]; then
  sudo -u "$SVC_USER" ssh-keygen -t ed25519 -N "" -C fleet -f "$HOME_DIR/.ssh/fleet"
fi
install -m 755 "$SRC/fleet-add" /usr/local/bin/fleet-add
install -m 755 "$SRC/harden-ssh.sh" /usr/local/bin/harden-ssh.sh
install -m 755 "$SRC/oci-keepalive.sh" /usr/local/bin/oci-keepalive.sh

echo "==> systemd unit (NOT enabled at boot, on purpose)"
cat > /etc/systemd/system/mcp-fleet.service <<UNIT
[Unit]
Description=fleet-mcp ops server
After=network-online.target

[Service]
User=$SVC_USER
ExecStart=/opt/mcp-fleet/venv/bin/python /opt/mcp-fleet/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

echo "==> caddy site"
cat > /etc/caddy/Caddyfile <<CADDY
${FQDN} {
    handle_path /${SECRET}/* {
        reverse_proxy 127.0.0.1:8765 {
            # the MCP SDK rejects a rewritten Host (DNS-rebinding guard)
            header_up Host {upstream_hostport}
        }
    }
    handle {
        respond "not found" 404
    }
}
CADDY

echo "==> firewall (80 + 443 so Let's Encrypt and the endpoint work)"
if command -v iptables >/dev/null; then
  for p in 80 443; do
    iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || \
      iptables -I INPUT 5 -p tcp --dport $p -j ACCEPT
  done
  command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null || true
fi

systemctl daemon-reload
systemctl enable --now caddy >/dev/null 2>&1 || true
systemctl restart caddy
systemctl start mcp-fleet

cat <<DONE

================================================================
 Done. Your connector URL:

   https://${FQDN}/${SECRET}/mcp

 Add that to your MCP client as a custom/remote connector.

 Next:
   fleet-add web-1 10.0.0.11 ~/web-1.key    # add a server
   systemctl stop mcp-fleet                 # close the door when idle

 Treat that URL like a root password. Anyone holding it gets a
 shell on every host in this fleet.
================================================================
DONE