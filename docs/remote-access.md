# Remote access: Tailscale Funnel, Cloudflare Tunnel, Cloudflare Access and Twingate

The server only ever makes outbound connections. A tunnel or a Twingate
connector therefore fits the design: it dials out, and nothing publishes the
server or the nodes directly.

Three ways to reach the dashboard and the node WebSocket:

| Path | Public hostname | Outbound | Encrypted by |
| --- | --- | --- | --- |
| Tailscale Funnel (`tailscaled` on the host) | yes, one `*.ts.net` name | host `tailscaled` | Tailscale (public TLS) |
| Cloudflare Tunnel (`cloudflared` profile) | yes, two hostnames | cloudflared container | Cloudflare edge; the tunnel itself is encrypted |
| Twingate (`twingate` profile) | no | connector container | Twingate client ↔ connector |

**Recommended topology for Cloudflare:** `cloudflared → server:8765` directly,
with no Caddy in the data path. It is one hop, one trust decision, and it avoids
having to teach Caddy about a second proxy. If you already run the `proxy`
profile for LAN access, you can instead point the tunnel at `caddy:80`; Caddy
then needs the `trusted_proxies` block that ships in `deploy/Caddyfile.compose`
(see §5).

All commands below assume the repository root and a stack that already has an
owner and generated secrets (`docs/docker.md`, steps 1–3).

## 1. Cloudflare Tunnel (compose profile `cloudflared`)

### Create the tunnel and its token secret

In the Cloudflare Zero Trust dashboard create a tunnel, choose the Docker
connector, and copy the token. Store it as a Docker secret — never in
`docker-compose.yml`, `.env`, or a command line that ends up in shell history:

```bash
install -d -m 700 deploy/secrets
read -rs CF_TUNNEL_TOKEN
printf '%s' "$CF_TUNNEL_TOKEN" | install -m 0444 /dev/stdin deploy/secrets/cf_tunnel_token
unset CF_TUNNEL_TOKEN
```

`deploy/generate-secrets.sh` also writes this file when `CF_TUNNEL_TOKEN` is set
once, and `deploy/secrets/` is git-ignored.

### Two public hostnames

In the tunnel's **Public Hostnames** tab add both, each pointing at the same
origin (no Caddy):

| Hostname | Service | Purpose |
| --- | --- | --- |
| `agent.example.com` | `http://server:8765` | dashboard, REST API, `/ws` |
| `nodes.example.com` | `http://server:8765` | node WebSocket `/ws/node` |

If you keep Caddy in front, use `http://caddy:80` for both instead, and apply
the Caddy `trusted_proxies` setting described in §5.

Set in `.env`:

```dotenv
PUBLIC_BASE_URL=https://agent.example.com
TRUSTED_PROXIES=172.28.0.0/24      # cloudflared's compose-network peer
SIGNUP_MODE=open                   # closed to disable signups
```

Start only the tunnel (not the `proxy` profile):

```bash
docker compose --profile cloudflared up -d
docker compose logs -f cloudflared
curl -s https://agent.example.com/api/health
```

WebSocket upgrades work through Cloudflare by default. The node heartbeat is
15 s (§6), far below Cloudflare's ~100 s idle limit.

> **Do not expose Caddy's localhost port** when the tunnel is the only entry
> point. Either leave the `proxy` profile stopped, or keep
> `PROXY_BIND=127.0.0.1` so the port is host-local only.

## 2. Cloudflare Access

Access runs at Cloudflare's edge and is an **outer** layer. The application does
not verify Access JWTs; it still enforces its own session cookie, CSRF token,
login throttling, node token and per-user ownership. Never bypass Access by
exposing the origin.

### UI hostname: user identity

On `agent.example.com` add an Access application with a policy such as:

- **Allow** → Emails ending in `@yourdomain` or specific addresses, via your
  IdP (Google/GitHub/Okta) or **One-time PIN** email;
- **Deny** → everyone else (default deny, no bypass);
- short session duration (e.g. 24 h) and re-authentication for sensitive changes.

Every member who uses the dashboard needs an Access identity in addition to
their application account. With a handful of users this is the same SSO you
already manage.

### Node hostname: service token, or no Access

A node is a machine, not a person, so `nodes.example.com` cannot use interactive
SSO. There are two workable policies.

**A. Service token on the node hostname (small, trusted fleets).**

1. Access → Service Auth → create a service token; copy the Client ID and
   Client Secret.
2. On `nodes.example.com` add an application with a **Service Auth** policy that
   allows only that token.
3. Install the two values on every node as files (mode 600), and point the node
   at them — never on the command line, never in the saved `connect` config:

```bash
install -d -m 700 ~/.config/occ
printf '%s' '<client-id>'     | install -m 600 /dev/stdin ~/.config/occ/access-id
printf '%s' '<client-secret>' | install -m 600 /dev/stdin ~/.config/occ/access-secret

export OCC_NODE_CF_ACCESS_CLIENT_ID_FILE=~/.config/occ/access-id
export OCC_NODE_CF_ACCESS_CLIENT_SECRET_FILE=~/.config/occ/access-secret
python -m node.cli run
```

Environment variables `OCC_NODE_CF_ACCESS_CLIENT_ID` and
`OCC_NODE_CF_ACCESS_CLIENT_SECRET` work too. Both halves are required; the node
refuses to start with only one. The values are attached to the WebSocket
handshake as `CF-Access-Client-Id` / `CF-Access-Client-Secret`, are excluded
from the config's `repr()`, and are never written to the audit log, status page
or node output. The per-node token inside the `hello` frame is still required:
Access only decides whether the connection reaches the server at all.

**B. Skip Access on the node hostname (recommended for many users).**

Giving every user their own Access service token is impractical: tokens must be
created by an administrator, distributed per node, rotated per machine, and one
shared token cannot be revoked for a single user. Instead:

- leave `nodes.example.com` without an Access application;
- rely on the per-node token (random, stored hashed, shown once, rotated and
  revoked per user in **My computers**);
- add Cloudflare **WAF rate limiting** for `nodes.example.com/ws/node`
  (e.g. 30 connection attempts per minute per IP, block for 10 minutes) and a
  managed WAF ruleset.

Trade-off: the node endpoint is reachable from the internet without an
identity check. An attacker can open a WebSocket and send a `hello`, but cannot
guess a 256-bit token; the rate limit and WAF reduce brute-force and connection
flooding. Access would add edge authentication and bot filtering, at the cost of
per-user token administration. For a family or small team, option B is the
practical default; use option A for a single admin machine or a small fleet.

## 3. Twingate (compose profile `twingate`)

Twingate keeps the server private: there is no public hostname and no published
port.

1. In Twingate admin create a **Remote Network** and a **Connector**; copy the
   connector's access and refresh tokens.
2. Store them as secrets and set the network name (not secret) in `.env`:

```bash
install -d -m 700 deploy/secrets
read -rs TWINGATE_ACCESS_TOKEN
printf '%s' "$TWINGATE_ACCESS_TOKEN"  | install -m 0444 /dev/stdin deploy/secrets/twingate_access_token
read -rs TWINGATE_REFRESH_TOKEN
printf '%s' "$TWINGATE_REFRESH_TOKEN" | install -m 0444 /dev/stdin deploy/secrets/twingate_refresh_token
unset TWINGATE_ACCESS_TOKEN TWINGATE_REFRESH_TOKEN

# .env
TWINGATE_NETWORK=your-network
```

```bash
docker compose --profile twingate up -d twingate
docker compose logs -f twingate
```

3. Create a **Resource** in Twingate for the server. The connector resolves
   names on the compose network, so the address can be the service name `server`
   on port `8765` (or `caddy` on port `80` if you keep Caddy). If single-label
   names are inconvenient for your clients, add a private DNS alias in Twingate
   and use that name consistently.
4. Every machine that runs a node needs the **Twingate client**, signed in to
   your network. The browser on a user's laptop and the node on their desktop
   both reach the same private address:

```bash
python -m node.cli connect --server http://server:8765 --token <token>
python -m node.cli run
```

Set `.env` to exactly what the browser shows:

```dotenv
PUBLIC_BASE_URL=http://server:8765
```

The Origin check compares the browser's `Origin` with the request host and
`PUBLIC_BASE_URL`; Twingate does not add `X-Forwarded-*`, so leave
`TRUSTED_PROXIES` as the compose subnet. Because the private address is plain
HTTP inside Twingate, the session cookie is not marked `Secure`; Twingate
encrypts client ↔ connector, but browsers treat it as an HTTP origin. If you
need `Secure` cookies and a proper certificate, put Caddy in front with a
certificate your clients trust and use `https://ocu.internal` as both the
resource and `PUBLIC_BASE_URL` (the scheme must match or Origin checks fail).

## 4. Tailscale Funnel

Funnel publishes one tailnet hostname to the internet and proxies it to a
loopback port, so the app stays bound to `127.0.0.1` and needs no domain.

```bash
sudo tailscale up
sudo tailscale funnel --bg 8765      # no-Docker server
sudo tailscale funnel --bg 8080      # with the compose proxy
sudo tailscale funnel status
```

Funnel needs MagicDNS, HTTPS certificates for the tailnet and the `funnel` node
attribute; the first command opens the approval flow for the last two. The
public URL is `https://<machine>.<tailnet>.ts.net`, and only ports 443, 8443 and
10000 are exposed publicly, so keep the app on 8765/8080 and let Funnel
terminate TLS. `sudo tailscale funnel reset` removes it.

Point the app at that URL:

```dotenv
PUBLIC_BASE_URL=https://<machine>.<tailnet>.ts.net
TRUSTED_PROXIES=127.0.0.1/32
SIGNUP_MODE=open        # closed if the URL should not be public
```

Funnel connects from loopback and sets `X-Forwarded-Proto: https` plus
`X-Forwarded-For`, so `127.0.0.1/32` is the only trusted peer the server needs.
That makes session cookies `Secure`, emits HSTS and buckets login throttling per
real client. Do not publish Caddy's port as well.

Nodes enrol against the Funnel URL
(`--server https://<machine>.<tailnet>.ts.net`); `/ws/node` upgrades through
Funnel unchanged and needs no Tailscale client on the controlled machine.

## 5. Proxy trust chain

```
browser ──TLS──▶ Cloudflare edge ──encrypted tunnel──▶ cloudflared ──HTTP──▶ server
                                                                   │
                                             X-Forwarded-Proto: https
                                             X-Forwarded-For: <edge>
                                             CF-Connecting-IP: <real client>
```

- **Recommended:** tunnel targets `server:8765`. The server sees cloudflared's
  address inside `172.28.0.0/24`, which is the default `TRUSTED_PROXIES`; the
  forwarded headers are believed and the session cookie becomes `Secure`.
- **With Caddy:** tunnel targets `caddy:80`. Caddy is then the first hop and
  must trust cloudflared's forwarded headers; the `servers { trusted_proxies
  static 172.28.0.0/24 }` block in `deploy/Caddyfile.compose` does that, and
  Caddy preserves `CF-Connecting-IP` while adding its own `X-Forwarded-For`.
  Keep `TRUSTED_PROXIES` on the server as the compose subnet.
- `CF-Connecting-IP` is used **only when the socket peer is in
  `TRUSTED_PROXIES`**. A direct request that sets the header itself is ignored
  and falls back to its socket address, so per-IP login throttling cannot be
  steered by an attacker.
- `X-Forwarded-Proto` follows the same rule: a trusted peer saying `https` is
  believed; a direct `http` request is not.
- Never add `0.0.0.0/0`, public ranges, or the Docker gateway to
  `TRUSTED_PROXIES`. Only the proxy containers belong there.

If a `Secure` cookie is missing: check `PUBLIC_BASE_URL` is the exact https
origin, that the request really arrived through the trusted proxy, and that
`TRUSTED_PROXIES` matches the compose subnet (not the host's LAN subnet).

## 6. Keepalive and reconnect

| Value | Setting |
| --- | --- |
| Application heartbeat | `HEARTBEAT_INTERVAL_MS = 15000` (15 s) |
| Server drops a silent node | `STALE_AFTER_SECONDS = 60` (4 missed beats) |
| Cloudflare WebSocket idle limit | ~100 s |
| Node reconnect | exponential backoff 1 s → 60 s with jitter |
| Caddy WebSocket timeouts | `read_timeout 24h`, `write_timeout 24h` |

15 s is well below 100 s, so an idle channel survives Cloudflare's limit. The
watchdog still drops a silent node after 60 s, so a dead laptop is noticed
without waiting for TCP timeouts.

Reconnect after a tunnel or connector restart is automatic: the node sees the
socket close, reconnects with backoff, and re-authenticates with its token; the
server keeps no state that would block it. The node keeps its `node.id` and
reuses the same record.

Live check:

```bash
docker compose restart cloudflared
docker compose logs -f cloudflared
# on the node machine, the log should show "Connecting ..." then "Connected"
docker compose run --rm -T server list-users   # unrelated, just proves the server is up
```

The repository tests `tests/test_remote_access.py` exercise an idle channel
through a restartable local relay and assert the node reconnects and sends
Access headers with its token.

## 7. Local tunnel simulation (no Cloudflare account needed)

The `tunnelsim` profile runs nginx with a configuration that behaves like the
edge: it sets `X-Forwarded-Proto: https` and a fixed
`CF-Connecting-IP: 203.0.113.50`, and upgrades WebSockets.

```bash
# Replace the placeholder with your own long passphrase.
./deploy/generate-secrets.sh
docker compose run --rm -T -e OCU_OWNER_PASSWORD='choose-a-long-passphrase' \
  server create-owner --password-env OCU_OWNER_PASSWORD

PUBLIC_BASE_URL=https://agent.example.test \
  docker compose --profile tunnelsim up -d server tunnel-sim

# Secure cookie despite the local HTTP hop (XFP: https from a trusted peer):
curl -si -X POST http://127.0.0.1:8090/api/auth/login \
  -H 'Origin: https://agent.example.test' -H 'Content-Type: application/json' \
  -d '{"username":"owner","password":"choose-a-long-passphrase"}' | grep -i set-cookie

# Origin allow / deny:
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8090/api/chats \
  -H 'Origin: https://evil.example' -H 'Content-Type: application/json' -d '{}'   # 403

# The server sees 203.0.113.50, not the proxy address (per-IP throttle bucket):
# five failed logins exhaust only that simulated client's bucket.
```

The `/ws/node` path through the simulator is covered by the test suite with a
valid token (welcome) and without one (close 4401). `tests/test_remote_access.py`
also proves that headers from an untrusted peer are ignored and that the origin
check accepts only `PUBLIC_BASE_URL` and the request host.

`tunnel-sim` is test-only: it listens on `127.0.0.1` and must never be exposed.

## 8. Security checklist

- [ ] The tunnel is the only entry point: `proxy` profile stopped, or
      `PROXY_BIND=127.0.0.1`; no router port-forward to 8080/8765.
- [ ] Cloudflare Access default-denies the UI hostname and allows only the
      identities or domains you intend; session duration is short.
- [ ] Decide the node hostname policy deliberately: service token (few nodes) or
      no Access plus WAF rate limiting (many users). Document the choice.
- [ ] WAF rate limiting on `/api/auth/login`, `/api/auth/signup` and
      `/ws/node`; enable a managed WAF ruleset and bot fight mode where it does
      not break the node client.
- [ ] `SIGNUP_MODE` is deliberate: `open` on a public URL means the internet
      can register (`closed` otherwise); `REMOTE_NODE_MODE=required`; no member
      can use the server desktop.
- [ ] Node tokens and Access service tokens are rotated on a schedule and
      immediately after a lost laptop; **Revoke** in My computers disconnects
      the node.
- [ ] Secrets live in `deploy/secrets/` (0444 in a 0700 directory) or Docker
      secrets, never in compose files, `.env`, git, shell history or logs.
- [ ] `PUBLIC_BASE_URL` matches the public origin exactly; cookies are `Secure`
      and `HttpOnly`; CSRF and Origin checks are active.
- [ ] Pin container image versions (`cloudflared`, `twingate/connector`,
      `nginx`) and update them; `latest` is convenient, not a policy.
- [ ] Backups (`server.cli backup`) include the Fernet key; test a restore.
- [ ] Review `audit_events` and failed-login logs for unexpected node
      connections or throttling spikes.

## 9. Verify these live (cannot be tested here)

- Tunnel token accepted and both public hostnames reach the stack through your
  Cloudflare account.
- Access UI policy (SSO/OTP) and node service-token policy in your account;
  service token headers accepted, wrong token rejected.
- WAF rate-limit rules actually trigger on login/signup/`/ws/node`.
- Twingate connector registers, the resource resolves, and a client machine
  reaches `PUBLIC_BASE_URL`; your Twingate plan's resource addressing matches
  the single-label `server` example.
- A real desktop node reconnects after `docker compose restart cloudflared` and
  after a laptop sleep, and keeps streaming screenshots through the tunnel.
- Certificate/SSL mode choices in Cloudflare (Full/Strict) with Caddy in the
  path, if you use that topology.
