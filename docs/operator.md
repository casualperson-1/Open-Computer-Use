# Operator guide (homelab)

This is the short runbook for the person who owns the box. Deployment details
are in [docker.md](docker.md); tunnels in [remote-access.md](remote-access.md).

## Ports

| Port | Who binds it | Exposure |
| --- | --- | --- |
| `8080` | Caddy (`proxy` profile) | `127.0.0.1` by default; the only intended entry point on the host. |
| `8090` | `tunnel-sim` (test profile) | Loopback only. Never expose it. |
| `8765` | server | Container network only; no host publish. |
| `6080`, `8765` | legacy `desktop` profile | Loopback only, separate profile. |

If a tunnel is the public entry point, stop the `proxy` profile (or keep
`PROXY_BIND=127.0.0.1`). Do not port-forward 8080/8765 on the router.

## Volumes

| Volume | Contents | Back up? |
| --- | --- | --- |
| `ocu-data` | `cowork.db`, browser profile | Yes, with the Fernet key. |
| `ocu-backups` | `server.cli backup` archives (DB + key) | Copy off-box. |
| `caddy-data`, `caddy-config` | Caddy state | Optional. |
| `ocu-desktop-data` | legacy desktop profile | Only if used. |

## Secrets

`deploy/secrets/` (mode 700, files 0444, git-ignored):

| File | Purpose | Rotation |
| --- | --- | --- |
| `fernet.key` | provider/MCP secrets at rest | Not rotatable in place: decrypting old rows needs the old key. Plan a re-encrypt/restore, or keep it for the life of the data. |
| `secret_key` | CSRF/session hashing | Replace and restart; everyone signs in again. |
| `cf_tunnel_token` | Cloudflare Tunnel | Create a new tunnel/token, update the file, restart cloudflared. |
| `twingate_access_token`, `twingate_refresh_token` | Twingate connector | Reissue in Twingate, update files, restart connector. |

Order for a fresh install:

1. `./deploy/generate-secrets.sh`
2. `docker compose run --rm -it server create-owner`
3. `docker compose --profile proxy up -d` (LAN) **or** install the tunnel
   token and start `--profile cloudflared`
4. Set `PUBLIC_BASE_URL`, restart the server, verify `/api/health`
5. Create invites (`create-invite` or Admin → Invites) and add computers

## Monitoring

- **Health**: `GET /api/health` is anonymous and returns
  `{"status":"ok","checks":{"database":"ok","encryption_key":"ok"}}`.
  A database or key problem returns `503` with `status: "degraded"`; the body
  never contains paths, key material or exception text. Compose health checks
  use it.
- **Containers**: `docker compose ps`; the server is `healthy` only when the
  health endpoint is 200. `restart: unless-stopped` is set on long-running
  services.
- **Logs**: json-file driver, `10m × 3` per container (compose anchor). Read with
  `docker compose logs -f server`; a node reconnect loop or repeated `401`/`429`
  is worth investigating.
- **Audit**: `GET /api/audit` (admin session) shows node commands, connects,
  rotations and revocations. Node-side `node-data/audit.jsonl` rotates at 5 MB.
- **Disk**: watch `ocu-data`, `ocu-backups` and Docker's image/volume usage.
- **Node presence**: My computers shows live/offline; `/api/nodes` reports the
  same for scripts.

## Scheduled backups

One-shot (writes DB + Fernet key together):

```bash
docker compose run --rm -T server backup
```

Host cron, 03:15 daily:

```cron
15 3 * * * cd /opt/open-computer-use && /usr/bin/docker compose run --rm -T server backup >> /var/log/ocu-backup.log 2>&1
```

Or the sidecar (every `BACKUP_INTERVAL` seconds, default 24h):

```bash
docker compose --profile backup up -d backup
```

Copy archives off the host. An archive without its key is useless, and the key
without the archive is not a backup.

## Restore drill (do this quarterly)

Never restore over the live volume. Use a scratch stack/volume and verify
counts and key decryption before trusting the backup.

```bash
# 1. Restore into a scratch project so the live volume is untouched.
docker volume create ocu-drill-data
docker run --rm -v ocu-drill-data:/data -v "$PWD/backups:/backups:ro" \
  --entrypoint python open-computer-use-server:local - <<'PY'
import shutil, tarfile
with tarfile.open("/backups/ocu-backup-YYYYMMDD-HHMMSS.tar.gz") as tar:
    tar.extractall("/data")
print("restored into /data")
PY

# 2. Start a throwaway stack against that volume and check health + data.
#    (Copy compose to a scratch project or override the volume name.)
docker run --rm -v ocu-drill-data:/data --entrypoint python \
  open-computer-use-server:local - <<'PY'
import sqlite3
con = sqlite3.connect("/data/cowork.db")
for table in ("users", "cowork_chats", "provider_settings", "audit_events"):
    print(table, con.execute(f"select count(*) from {table}").fetchone()[0])
print("schema", con.execute("pragma user_version").fetchone()[0])
PY

# 3. Verify the key decrypts a stored provider key (prints true/false only).
docker run --rm -v ocu-drill-data:/data \
  -e ENCRYPTION_KEY_FILE=/data/.fernet.key \
  --entrypoint python open-computer-use-server:local - <<'PY'
import os, sqlite3
from cryptography.fernet import Fernet
key = open(os.environ["ENCRYPTION_KEY_FILE"], "rb").read().strip()
fernet = Fernet(key)
con = sqlite3.connect("/data/cowork.db")
rows = con.execute("select provider, encrypted_key from provider_settings").fetchall()
print([(p, bool(v) and bool(fernet.decrypt(v.encode()))) for p, v in rows])
PY

# 4. Remove the drill volume.
docker volume rm ocu-drill-data
```

Only after the drill succeeds should you restore the live stack:

```bash
docker compose down
docker compose run --rm server restore /backups/ocu-backup-YYYYMMDD-HHMMSS.tar.gz
docker compose --profile proxy up -d
```

`restore` refuses a database whose stored secrets the mounted key cannot
decrypt, so a mismatched pair fails before replacing the live database.
