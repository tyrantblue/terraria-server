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
├── Dockerfile                 # Terraria server image
├── docker-compose.yml         # Docker Compose configuration
├── start.sh                   # Terraria startup script
└── README.md
```

### Files managed by Git

```text
api/
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

---

# 15. Important API Endpoints

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
             ▼                             ├── Terraria :7777
      React + Vite                         │
      Management Panel                     └── FastAPI :8080
             │                                  │
             │ HTTPS                            │
             └──────────────────────────────────┘
```

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
