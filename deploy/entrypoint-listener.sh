#!/bin/bash
# Fargate entrypoint for the beacon-mcp CDC listener.
#
# Responsibilities:
#   1. Materialize the SSH private key from the SSH_PRIVATE_KEY env var
#      (populated by Fargate from Secrets Manager) into ~/.ssh/id_rsa
#      with 0600 perms.
#   2. Start `autossh` in the background — resilient tunnel from container
#      localhost:15433 to Beacon Postgres on the Hetzner VPS. autossh
#      re-establishes the tunnel automatically on transient failures.
#   3. Exec `python -m server.listener`. Container lifecycle follows the
#      listener process; ECS will restart the task if the listener exits.
#
# The listener has its own reconnect-with-backoff logic against
# BEACON_DATABASE_URL, so a race between tunnel-up and listener-connect
# is fine — the listener retries until the tunnel accepts connections.
#
# Env vars this entrypoint needs (in addition to what the listener
# itself needs — see server/config.py):
#
#   SSH_PRIVATE_KEY   PEM-encoded private key that authenticates to
#                     SSH_TUNNEL_HOST. Populated from Secrets Manager.
#   SSH_TUNNEL_HOST   user@host, e.g. `root@65.108.243.192`. Baked as a
#                     default in the Dockerfile; can be overridden per
#                     task definition for staging vs prod.
#   SSH_TUNNEL_FORWARD  Forward spec, e.g. `15433:127.0.0.1:15433`. The
#                       first port is the local (container) side; must
#                       match BEACON_DATABASE_URL's host:port.

set -euo pipefail

if [ -z "${SSH_PRIVATE_KEY:-}" ]; then
    echo "FATAL: SSH_PRIVATE_KEY env var is empty. Populate marquez-oci/beacon-cdc-ssh-key in Secrets Manager, or verify the task definition wires it to this container." >&2
    exit 2
fi

if ! grep -q "PRIVATE KEY" <<< "${SSH_PRIVATE_KEY}"; then
    echo "FATAL: SSH_PRIVATE_KEY does not look like a PEM-encoded private key (no 'PRIVATE KEY' marker)." >&2
    exit 2
fi

: "${SSH_TUNNEL_HOST:?SSH_TUNNEL_HOST is required}"
: "${SSH_TUNNEL_FORWARD:?SSH_TUNNEL_FORWARD is required}"

# Materialize the key. ~/.ssh already has known_hosts baked at image build time
# with strict perms; only id_rsa is created at runtime.
umask 077
mkdir -p /home/app/.ssh
printf '%s\n' "${SSH_PRIVATE_KEY}" > /home/app/.ssh/id_rsa
chmod 600 /home/app/.ssh/id_rsa
umask 022

echo "[entrypoint] SSH key materialized; starting autossh tunnel"

# autossh flags:
#   -M 0                              — disable autossh's own monitoring port; rely on SSH keepalives instead
#   -f                                — fork to background after auth
#   -N                                — no remote command; forward only
#   ServerAliveInterval=30            — keepalive probe every 30 s
#   ServerAliveCountMax=3             — declare dead after 3 missed probes (~90 s)
#   ExitOnForwardFailure=yes          — exit immediately if port bind fails at either end
#   StrictHostKeyChecking=yes         — refuse to connect if host key doesn't match known_hosts
#   UserKnownHostsFile=~/.ssh/known_hosts — pinned to the file baked at image build time
autossh -M 0 -f -N \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile=/home/app/.ssh/known_hosts \
    -L "${SSH_TUNNEL_FORWARD}" \
    "${SSH_TUNNEL_HOST}"

echo "[entrypoint] autossh forwarding ${SSH_TUNNEL_FORWARD} via ${SSH_TUNNEL_HOST}"
echo "[entrypoint] exec python -m server.listener"
exec python -u -m server.listener
