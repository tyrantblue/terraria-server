# Terraria Server

Terraria Dedicated Server deployment and management files.

This repository contains the **server application, Docker configuration, and backend API**.

Runtime data such as worlds, backups, logs, and server secrets are intentionally excluded from Git.

---

English | [简体中文](README_zh-CN.md) 

## 1. Server Structure

The server is deployed under:

```text
/opt/terraria/
├── api/                       # FastAPI backend
├── config/
│   └── serverconfig.txt      # Terraria server configuration
├── worlds/                    # Terraria world files
├── backup/                    # World backups
├── control/                   # Runtime FIFO and logs
├── data/                      # Runtime data
├── guard/                      # Connection guard (anti port-scan) -- see section 28
├── docs/                       # Analysis and runbooks
├── Dockerfile                 # Terraria server image
├── docker-compose.yml         # Docker Compose configuration
├── start.sh                   # Terraria startup script
└── README.md
```

### Files managed by Git

```text
api/
guard/
docs/
Dockerfile
docker-compose.yml
start.sh
README.md
.gitignore
```

### Files intentionally NOT managed by Git

```text
worlds/
backup/
control/
data/
config/serverconfig.txt
docker-compose.yml.bak
```

Reasons:

* `worlds/` contains actual game saves.
* `backup/` contains backups.
* `control/` contains runtime files and logs.
* `data/` contains runtime data.
* `serverconfig.txt` may contain server secrets such as passwords.
* `.bak` files are local backups and are not part of the deployment.

---

# 2. Requirements

Recommended environment:

* Ubuntu 24.04 LTS
* Docker
* Docker Compose
* Git
* GitHub CLI (`gh`)

Check versions:

```bash
docker --version
docker compose version
git --version
gh --version
```

---

# 3. GitHub Repository

Repository:

```text
https://github.com/tyrantblue/terraria-server
```

The repository should remain **private** because server configuration and deployment information may be sensitive.

---

# 4. First Deployment

Clone the repository:

```bash
cd /opt
git clone https://github.com/tyrantblue/terraria-server.git terraria
cd /opt/terraria
```

Create the required runtime directories:

```bash
mkdir -p worlds
mkdir -p backup
mkdir -p control
mkdir -p data
mkdir -p config
```

Restore the following files separately:

```text
config/serverconfig.txt
worlds/*.wld
```

Then build and start the containers:

```bash
docker compose up -d --build
```

Check the containers:

```bash
docker compose ps
```

Check Terraria logs:

```bash
docker compose logs -f terraria
```

Check API logs:

```bash
docker compose logs -f terraria-api
```

---

# 5. Normal Server Startup

Start the server:

```bash
cd /opt/terraria
docker compose up -d
```

Check status:

```bash
docker compose ps
```

---

# 6. Stop the Server

Preferably save the world from the Terraria console/API before stopping the container.

Then:

```bash
docker compose down
```

---

# 7. Restart the Server

```bash
docker compose restart
```

If the Docker image or source code has changed:

```bash
docker compose up -d --build
```

---

# 8. Update Server Code from GitHub

When the repository has new code:

```bash
cd /opt/terraria
git pull
```

Then rebuild the containers:

```bash
docker compose up -d --build
```

Check:

```bash
docker compose ps
```

---

# 9. Updating the Terraria Server Version

The Terraria version is defined in:

```text
Dockerfile
```

For example:

```dockerfile
https://terraria.org/api/download/pc-dedicated-server/terraria-server-1458.zip
```

After changing the version:

```bash
docker compose build --no-cache
docker compose up -d
```

Check the server version:

```bash
docker compose logs terraria
```

Or use the API:

```text
GET /api/server/status
```

---

# 10. World Data

World files are stored in:

```text
/opt/terraria/worlds/
```

Example:

```text
/opt/terraria/worlds/
├── WSD.wld
└── 幻想乡.wld
```

The active world is configured by:

```text
/opt/terraria/config/serverconfig.txt
```

Example:

```text
world=/worlds/WSD.wld
```

Do NOT put `.wld` files into Git.

---

# 11. Backing Up Worlds

Before major changes or server migration:

```bash
docker compose exec terraria save
```

Then create a backup:

```bash
cp -a /opt/terraria/worlds /opt/terraria/backup/worlds-$(date +%Y%m%d-%H%M%S)
```

Check backups:

```bash
ls -lh /opt/terraria/backup/
```

---

# 12. Server Migration

When moving to a new VPS:

## Step 1 — Prepare the new server

Install:

* Docker
* Docker Compose
* Git

Then:

```bash
cd /opt
git clone https://github.com/tyrantblue/terraria-server.git terraria
cd /opt/terraria
```

Create runtime directories:

```bash
mkdir -p worlds
mkdir -p backup
mkdir -p control
mkdir -p data
mkdir -p config
```

---

## Step 2 — Stop the old server

On the old server:

```bash
cd /opt/terraria
docker compose down
```

Make sure the Terraria server has saved the world before copying files.

---

## Step 3 — Copy world data

From the new server:

```bash
rsync -avz root@OLD_SERVER_IP:/opt/terraria/worlds/ /opt/terraria/worlds/
```

Copy backups if required:

```bash
rsync -avz root@OLD_SERVER_IP:/opt/terraria/backup/ /opt/terraria/backup/
```

---

## Step 4 — Copy server configuration

`serverconfig.txt` is intentionally not stored in Git.

Copy it from the old server:

```bash
scp root@OLD_SERVER_IP:/opt/terraria/config/serverconfig.txt /opt/terraria/config/
```

Check the world path:

```bash
grep '^world=' /opt/terraria/config/serverconfig.txt
```

It should point to:

```text
world=/worlds/WSD.wld
```

or whichever world should be active.

---

## Step 5 — Start the new server

```bash
cd /opt/terraria
docker compose up -d --build
```

Check:

```bash
docker compose ps
```

Then:

```bash
docker compose logs -f terraria
```

---

# 13. Important Migration Rule

Do NOT copy a world while Terraria is actively writing to it.

Recommended migration sequence:

```text
Old Server
    ↓
Save world
    ↓
Stop Terraria
    ↓
Copy worlds/
    ↓
Copy serverconfig.txt
    ↓
New Server
    ↓
docker compose up -d --build
```

---

# 14. API

FastAPI backend:

```text
https://terraria-api.tyrantblue.xyz
```

Health check:

```text
GET /api/health
```

Example:

```bash
curl https://terraria-api.tyrantblue.xyz/api/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "terraria-api"
}
```

### API versions

`/api/v1` is the current surface (resource-oriented, long operations return `202` with an
`operation_id`, structured console lines, persistent config). The legacy `/api/*` routes
still work and now advertise `Deprecation`/`Sunset` headers.

| Document | Contents |
| --- | --- |
| `docs/api/v1.md` | **Frontend reference for `/api/v1`** (endpoints, payloads, migration table) |
| `docs/api/CHANGELOG.md` | Every contract change, with migration examples and Sunset dates |
| `docs/roadmap.md` | Planned features and known gaps |

Contract changes are additive first: old routes keep working while new ones are added, and
removal only happens in a major version after `GET /api/meta/usage` shows the old routes are
unused. To regenerate the snapshot:

```bash
cd api
uv run python scripts/export_openapi.py          # update api/openapi.json
uv run python scripts/export_openapi.py --check  # CI: fail if the snapshot is stale
uv run pytest -q                                 # tests (fake FIFO/fake log, no real server)
```

Refactor roadmap and design notes: `docs/api-refactor-plan.md`.

---
# 15. Important API Endpoints

## System

```text
GET  /api/health
GET  /api/meta          # API version handshake for the frontend
```

## Server

```text
GET  /api/server/status
GET  /api/server/players
GET  /api/server/console

POST /api/server/command
POST /api/server/playing
POST /api/server/version
POST /api/server/port
POST /api/server/maxplayers
POST /api/server/save
POST /api/server/settle
POST /api/server/say
POST /api/server/kick
POST /api/server/ban
POST /api/server/motd
POST /api/server/password
```

## World

```text
GET  /api/world/list
POST /api/world/upload
POST /api/world/switch
```

## Console WebSocket

```text
wss://terraria-api.tyrantblue.xyz/api/server/ws
```

---

# 16. Docker Ports

Terraria:

```text
7777/tcp
7777/udp
```

FastAPI:

```text
8080/tcp
```

The Docker Compose configuration maps:

```text
7777 → Terraria
8080 → FastAPI
```

---

# 17. Checking Logs

Terraria:

```bash
docker compose logs -f terraria
```

API:

```bash
docker compose logs -f terraria-api
```

Runtime Terraria output is also stored in:

```text
/opt/terraria/control/output.log
```

This file is intentionally excluded from Git.

---

# 18. Checking Running Containers

```bash
docker compose ps
```

More detailed:

```bash
docker ps
```

---

# 19. Rebuilding Everything

If Docker configuration or dependencies have changed:

```bash
docker compose down
docker compose up -d --build
```

For a completely fresh image rebuild:

```bash
docker compose build --no-cache
docker compose up -d
```

---

# 20. Git Workflow

After changing backend code:

```bash
cd /opt/terraria

git status
git add .
git commit -m "Describe the change"
git push
```

Example:

```bash
git add .
git commit -m "Fix player list parsing"
git push
```

Then update the running server:

```bash
git pull
docker compose up -d --build
```

---

# 21. Files That Must Never Be Committed

Before running:

```bash
git add .
```

always check:

```bash
git status
```

Never commit:

```text
worlds/
backup/
control/
data/
config/serverconfig.txt
.env
```

If a password or other secret is accidentally committed, changing the file is NOT enough. The secret should be considered exposed and replaced.

---

# 22. Cloudflare Frontend

Frontend project is separate from this repository.

Frontend:

```text
terraria-panel
```

Cloudflare Worker:

```text
https://terraria-panel.tyrantblue32.workers.dev
```

The frontend is built using:

```bash
pnpm build
```

and deployed with:

```bash
pnpm wrangler deploy
```

The Cloudflare configuration uses SPA fallback so that React Router paths such as:

```text
/players
/worlds
/console
```

can be refreshed directly.

---

# 23. Common Deployment Commands

### Pull code and rebuild

```bash
cd /opt/terraria
git pull
docker compose up -d --build
```

### Restart

```bash
docker compose restart
```

### Stop

```bash
docker compose down
```

### Start

```bash
docker compose up -d
```

### Check status

```bash
docker compose ps
```

### View Terraria logs

```bash
docker compose logs -f terraria
```

### View API logs

```bash
docker compose logs -f terraria-api
```

---

# 24. Emergency Recovery

If the server fails after an update:

```bash
cd /opt/terraria
git log --oneline
```

Find the previous working commit:

```bash
git checkout <COMMIT>
```

Then rebuild:

```bash
docker compose up -d --build
```

Once the problem is resolved, return to the main branch:

```bash
git checkout main
git pull
```

---

# 25. Basic Migration Checklist

Before replacing the VPS:

```text
[ ] Save Terraria world
[ ] Stop Terraria
[ ] Verify worlds/*.wld
[ ] Verify serverconfig.txt
[ ] Verify backup/
[ ] Clone GitHub repository on new server
[ ] Copy worlds/
[ ] Copy serverconfig.txt
[ ] Copy backup/ if needed
[ ] docker compose up -d --build
[ ] Check docker compose ps
[ ] Check Terraria logs
[ ] Check API health
[ ] Test Terraria connection
[ ] Test web management panel
```

---

# 26. Current Architecture

```text
                         Internet
                            │
             ┌──────────────┴──────────────┐
             │                             │
             ▼                             ▼
       Cloudflare                     Terraria VPS
       Frontend                       Ubuntu 24.04
             │                             │
             │ HTTPS                       │
             ▼                             ├── Terraria :7777  ← filtered by
      React + Vite                         │     terraria-guard (DOCKER-USER)
      Management Panel                     └── FastAPI :8080
             │                                  │
             │ HTTPS                            │
             └──────────────────────────────────┘
```

Docker Compose services: `terraria` (game server), `terraria-api` (panel backend) and
`terraria-guard` (connection guard, host network — see section 28).

Frontend:

```text
terraria-panel.tyrantblue32.workers.dev
```

Backend:

```text
terraria-api.tyrantblue.xyz
```

Terraria:

```text
VPS:7777
```

---

# 27. Main Principle

The deployment follows this rule:

```text
GitHub
    │
    └── Code / Docker / Deployment configuration

Server
    │
    ├── World saves
    ├── Backups
    ├── Secrets
    └── Runtime data
```

**Code can be recreated from Git.**

**Game data must be backed up and migrated separately.**

This makes VPS replacement or recovery much easier.

---

# 28. Connection Guard (anti port-scan / "server is full" fix)

## 28.1 The problem

The vanilla Linux dedicated server counts **every TCP connection to port 7777** against
`maxplayers` — including port scanners, Censys/Rapid7 probes and cloud-hosted bots that
connect and immediately drop. Those slots are often never released, so the server reports
`No players connected.` while every new player is rejected with
`This server is full right now`, until the container is restarted.

Worse, a connection that opens without completing the handshake can **crash** the server:

```text
Unhandled Exception
Exception: System.ObjectDisposedException: Cannot access a disposed object.
Object name: 'System.Net.Sockets.NetworkStream'.
  at Terraria.Net.Sockets.TcpSocket...IsConnected ()
  at Terraria.RemoteClient.IsConnected ()
  at Terraria.Netplay.UpdateConnectedClients ()
  at Terraria.Netplay.ServerLoop ()
[ERROR] FATAL UNHANDLED EXCEPTION: ...
```

The password does not help: it is checked *after* the TCP connection has already taken a
slot. Only blocking non-game connections at the network layer fixes this.

Full analysis, log evidence and upstream bug references: `docs/connection-guard.md`.

## 28.2 What was deployed

| Component | Purpose |
| --- | --- |
| `guard/terraria-guard.sh` | Rules in Docker's `DOCKER-USER` chain: dynamic ban set, allow-list, per-IP concurrent connection limit (4), per-IP new-connection rate limit (10/min). Idempotent `apply` / `status` / `remove`. |
| `guard/terraria-watchd.py` | Daemon: auto-bans scan-like IPs (connect-then-drop without ever joining, malformed packets), auto-recovers from the phantom-full state with `save` + `exit`, and auto-whitelists players who log in successfully. |
| `guard/allow.txt` | Manually curated allow-list: trusted player IPs (exempt from limits and bans) plus `172.18.0.0/16` for Docker-internal traffic. |
| `guard/learned_allow.txt` | Auto-generated by the daemon: IPs of players who logged in and stayed online. Not tracked by Git. |
| `guard/Dockerfile` + `guard/docker-entrypoint.sh` | The sidecar image and its entrypoint: apply the rules, run the daemon, remove the rules on `docker compose down`. |
| `guard/systemd/*.service` | Alternative (non-Docker) deployment: apply the rules at boot and supervise the daemon. Keep these **disabled** while the sidecar runs. |
| `config/serverconfig.txt` | `maxplayers` raised from `8` to `255` (30x more headroom). Not tracked by Git. |

### How it runs: a Compose sidecar

`docker-compose.yml` has a third service, `terraria-guard`. It runs with
`network_mode: host` plus `NET_ADMIN`/`NET_RAW` (not `privileged`), so the `iptables`/`ipset`
commands inside the container act on the **host's** netfilter — the same `DOCKER-USER` chain
the other containers are published through. It also bind-mounts `./control` (FIFO + log) and
`./guard` (scripts, `allow.txt`, `learned_allow.txt`).

Consequences:

* **Migration is now `docker compose up -d --build`** — the guard comes up with everything
  else, with no extra host packages or systemd units to install.
* The guard's settings (`MAX_CONN_PER_IP`, `NEW_CONN_RATE`, `ALLOWLIST_ONLY`, `LEARN_*`, …) live
  in `docker-compose.yml`, so they travel with the repo.
* If the container fails to apply the rules it exits non-zero, so `docker compose ps` shows it
  restarting instead of silently running unprotected.
* Trade-off: a container with the host network namespace and `NET_ADMIN` can rewrite the host
  firewall. That is exactly what it needs to do, but it is more privilege than the systemd
  version. If you prefer to avoid it, disable the `terraria-guard` service and use the
  systemd units instead (never both at once).

## 28.3 Automatic whitelisting on successful login

When a player joins, the daemon waits `LEARN_DWELL` seconds, reads the player list with
`playing` to resolve the exact IP behind that player name, then writes the IP to
`guard/learned_allow.txt` and adds it to the `tg_allow` ipset immediately. Whitelisted IPs
are exempt from the connection limits and can never be auto-banned.

| Env var | Default | Meaning |
| --- | --- | --- |
| `LEARN_ALLOW` | `1` | `0` disables automatic whitelisting entirely |
| `LEARN_DWELL` | `60` | seconds the player must stay online first (`0` = whitelist on join) |
| `LEARN_TTL` | `604800` | entry lifetime in seconds (`0` = permanent) |
| `LEARN_MAX` | `200` | maximum entries; the earliest expiring ones are dropped |

> ⚠️ **Security trade-off.** `tg_allow` bypasses `connlimit`/`hashlimit` completely, so this
> feature means "can log in = trusted". Anyone who learns the server password can log in once
> and then open an unlimited number of connections. **Change the password from the weak
> default before relying on this**, or set `LEARN_ALLOW=0` and keep `allow.txt` manual.

To revoke one entry: delete its line from `guard/learned_allow.txt` and run
`sudo ipset del tg_allow <IP>`. To revoke everything: stop the guard container, delete the
file, then `docker compose up -d terraria-guard`.

## 28.4 Daily operations

```bash
docker compose logs -f terraria-guard                 # watch [strike] / [ban] / [learn] / [recover]
docker compose restart terraria-guard                 # re-apply rules (e.g. after a Docker upgrade)
docker compose ps                                     # terraria-guard must stay "Up"

# The scripts also work directly on the host (same netfilter):
sudo /opt/terraria/guard/terraria-guard.sh status     # show rules, sets and allow-list counts
sudo /opt/terraria/guard/terraria-guard.sh apply      # reload after editing allow.txt
sudo ipset del tg_ban <IP>                            # unban a single IP
```

The guard container runs on the host network and adds host firewall rules, so after a host
reboot or a Docker upgrade, confirm it is up and the rules are present:

```bash
docker compose ps
sudo iptables -S DOCKER-USER | grep terraria-guard     # expect 4 rules
```

Never probe the port with `nc` or `/dev/tcp`: a raw TCP connection without a handshake is
exactly what crashes the server. Always test with a real game client.

## 28.5 Rollback

```bash
# stop the guard (it removes its own rules via its entrypoint trap)
docker compose stop terraria-guard
sudo /opt/terraria/guard/terraria-guard.sh remove     # belt and braces
# then set maxplayers back to 8 in config/serverconfig.txt and restart the container
```

The game container itself was not modified, so rollback restores the previous behaviour.
The systemd units in `guard/systemd/` remain available as an alternative deployment; enable
them only while the `terraria-guard` service is stopped (never run both at once).
---

# 29. Scheduled Tasks, Backups and Notifications

Everything below is configured in `docker-compose.yml` (the `terraria-api` service) and
surfaced under `/api/v1`.

## 29.1 Scheduled tasks

| Env var | Default | Meaning |
| --- | --- | --- |
| `SCHEDULE_ENABLED` | `1` | master switch |
| `SCHEDULE_SAVE_MINUTES` | `15` | save interval (`0` = off) |
| `SCHEDULE_SAVE_SKIP_EMPTY` | `1` | skip saving when nobody is online |
| `SCHEDULE_BACKUP_HOURS` | `6` | automatic backup interval (`0` = off) |
| `SCHEDULE_BACKUP_KEEP` | `10` | keep the newest N backups (`pre-restore-*` is never pruned) |
| `SCHEDULE_CONSOLE_CHECK_SECONDS` | `60` | console heartbeat: probe FIFO→log and alert when the log pipeline stalls (`0` = off) |
| `CONSOLE_STALL_COOLDOWN` | `1800` | minimum seconds between two `console_stalled` alerts |
| `SCHEDULE_RESTART_AT` | `05:00` | daily restart time, empty = off |
| `SCHEDULE_RESTART_SKIP_IF_PLAYERS` | `1` | skip the restart if players are online |
| `SCHEDULE_RESTART_WARN_MINUTES` | `5` | broadcast a warning before restarting |
| `SCHEDULE_TZ` | `Asia/Shanghai` | timezone for the daily restart (containers are UTC) |

```bash
curl localhost:8080/api/v1/scheduler                    # jobs, next run, last result
curl -X POST localhost:8080/api/v1/scheduler/save/run   # run one now
```

## 29.2 Backups and restore

Automatic backups land in `backup/<YYYYmmdd-HHMMSS>/` (world files + `serverconfig.txt`).
Terraria's own `.wld.bak`/`.bak2` show up as `kind: "auto"` and are restorable too.

```bash
curl localhost:8080/api/v1/backups
curl -X POST localhost:8080/api/v1/backups/20260917-170849/restore      # 202 + operation_id
curl -X POST localhost:8080/api/v1/backups/auto:gogogo.wld.bak/restore  # Terraria's own backup
```

Restoring the **active** world restarts the server twice (stop, replace the file, start again
with `exit-nosave` so the shutdown save cannot overwrite the restored file) and keeps a safety
copy in `backup/pre-restore-<timestamp>/`. Restoring an inactive world just copies the file.

## 29.3 Notifications

| Env var | Default | Meaning |
| --- | --- | --- |
| `NOTIFY_WEBHOOK_URL` | empty | empty disables notifications |
| `NOTIFY_FORMAT` | `auto` | `auto` (guess from URL) / `discord` / `slack` / `json` |
| `NOTIFY_EVENTS` | empty | comma separated allow-list, empty = all |

Events: `player_join`, `player_leave`, `player_booted`, `server_up`, `server_error`,
`backup_done`, `schedule_failed`, `restart_skipped`, `console_stalled`.

```bash
curl localhost:8080/api/v1/notifications
curl -X POST localhost:8080/api/v1/notifications/test
```

## 29.4 Log timestamps and rotation

`start.sh` prefixes every console line with `[YYYY-mm-dd HH:MM:SS]` using the container's `TZ`
(the image ships `tzdata`). `/api/v1/console` returns that as the `ts` field.
`ops/logrotate.terraria` (installed to `/etc/logrotate.d/terraria`) rotates
`control/output.log` at 20M, keeps 4 files and uses `copytruncate` (so the container's
`tee` keeps working):

```bash
sudo install -m 644 ops/logrotate.terraria /etc/logrotate.d/terraria
```

The API and the guard both accept log lines **with or without** the timestamp prefix, so
removing the `awk` stage from `start.sh` is a safe rollback.
