# Terraria Server

Terraria 专用服务器及其管理后端的部署文件。

本仓库保存 **服务器程序、Docker 配置以及 FastAPI 后端代码**。

世界存档、备份、日志以及服务器密码等运行时数据不会提交到 Git。

[English](README.md) | 简体中文

---

## 1. 服务器目录结构

服务器部署目录：

```text
/opt/terraria/
├── api/                       # FastAPI 后端
├── config/
│   └── serverconfig.txt      # Terraria 服务器配置
├── worlds/                    # Terraria 世界存档
├── backup/                    # 世界备份
├── control/                   # 运行时 FIFO 和日志
├── data/                      # 运行时数据
├── guard/                      # 连接守卫（防端口扫描）—— 见第 28 节
├── docs/                       # 分析与运维文档
├── Dockerfile                 # Terraria Docker 镜像
├── docker-compose.yml         # Docker Compose 配置
├── start.sh                   # Terraria 启动脚本
└── README.md
```

### Git 管理的文件

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

### 不提交到 Git 的文件

```text
worlds/
backup/
control/
data/
config/serverconfig.txt
docker-compose.yml.bak
```

原因：

* `worlds/`：实际游戏世界存档。
* `backup/`：世界备份。
* `control/`：运行时文件和日志。
* `data/`：运行时数据。
* `serverconfig.txt`：可能包含服务器密码等敏感配置。
* `.bak`：本地备份文件。

---

# 2. 环境要求

推荐环境：

* Ubuntu 24.04 LTS
* Docker
* Docker Compose
* Git
* GitHub CLI（`gh`）

检查版本：

```bash
docker --version
docker compose version
git --version
gh --version
```

---

# 3. GitHub 仓库

仓库：

```text
https://github.com/tyrantblue/terraria-server
```

建议保持仓库为 **Private（私有）**。

---

# 4. 第一次部署

克隆仓库：

```bash
cd /opt
git clone https://github.com/tyrantblue/terraria-server.git terraria
cd /opt/terraria
```

创建运行时目录：

```bash
mkdir -p worlds
mkdir -p backup
mkdir -p control
mkdir -p data
mkdir -p config
```

然后恢复以下文件：

```text
config/serverconfig.txt
worlds/*.wld
```

启动并构建 Docker：

```bash
docker compose up -d --build
```

检查容器：

```bash
docker compose ps
```

查看 Terraria 日志：

```bash
docker compose logs -f terraria
```

查看 API 日志：

```bash
docker compose logs -f terraria-api
```

---

# 5. 正常启动服务器

```bash
cd /opt/terraria
docker compose up -d
```

检查：

```bash
docker compose ps
```

---

# 6. 停止服务器

在停止前，建议先通过 Terraria 控制台/API 保存世界。

然后：

```bash
docker compose down
```

---

# 7. 重启服务器

普通重启：

```bash
docker compose restart
```

如果代码或 Docker 配置发生变化：

```bash
docker compose up -d --build
```

---

# 8. 从 GitHub 更新服务器代码

当 GitHub 仓库有新的后端代码：

```bash
cd /opt/terraria
git pull
```

然后重新构建：

```bash
docker compose up -d --build
```

检查：

```bash
docker compose ps
```

---

# 9. 更新 Terraria 服务器版本

Terraria 版本由：

```text
Dockerfile
```

控制。

例如：

```dockerfile
https://terraria.org/api/download/pc-dedicated-server/terraria-server-1458.zip
```

修改版本后：

```bash
docker compose build --no-cache
docker compose up -d
```

查看日志：

```bash
docker compose logs terraria
```

也可以通过 API：

```text
GET /api/server/status
```

查看服务器版本。

---

# 10. 世界存档

世界文件位于：

```text
/opt/terraria/worlds/
```

例如：

```text
/opt/terraria/worlds/
├── WSD.wld
└── 幻想乡.wld
```

当前世界由：

```text
/opt/terraria/config/serverconfig.txt
```

中的：

```text
world=/worlds/WSD.wld
```

指定。

不要把 `.wld` 世界文件提交到 Git。

---

# 11. 备份世界

进行服务器升级或迁移之前，建议先保存世界。

例如：

```bash
docker compose exec terraria save
```

然后创建备份：

```bash
cp -a /opt/terraria/worlds /opt/terraria/backup/worlds-$(date +%Y%m%d-%H%M%S)
```

查看备份：

```bash
ls -lh /opt/terraria/backup/
```

---

# 12. 迁移服务器

如果以后需要更换 VPS，可以按照以下流程。

## 第一步：准备新服务器

安装：

* Docker
* Docker Compose
* Git

然后：

```bash
cd /opt
git clone https://github.com/tyrantblue/terraria-server.git terraria
cd /opt/terraria
```

创建运行时目录：

```bash
mkdir -p worlds
mkdir -p backup
mkdir -p control
mkdir -p data
mkdir -p config
```

---

## 第二步：停止旧服务器

在旧服务器执行：

```bash
cd /opt/terraria
docker compose down
```

确认 Terraria 已经保存世界。

---

## 第三步：复制世界存档

在新服务器执行：

```bash
rsync -avz root@OLD_SERVER_IP:/opt/terraria/worlds/ /opt/terraria/worlds/
```

如果需要保留备份：

```bash
rsync -avz root@OLD_SERVER_IP:/opt/terraria/backup/ /opt/terraria/backup/
```

---

## 第四步：复制服务器配置

`serverconfig.txt` 不在 Git 中，因此需要单独复制：

```bash
scp root@OLD_SERVER_IP:/opt/terraria/config/serverconfig.txt /opt/terraria/config/
```

检查世界配置：

```bash
grep '^world=' /opt/terraria/config/serverconfig.txt
```

例如：

```text
world=/worlds/WSD.wld
```

---

## 第五步：启动新服务器

```bash
cd /opt/terraria
docker compose up -d --build
```

检查：

```bash
docker compose ps
```

查看 Terraria：

```bash
docker compose logs -f terraria
```

---

# 13. 迁移时的重要规则

**不要在 Terraria 正在运行并写入世界时直接复制 `.wld` 文件。**

推荐流程：

```text
旧服务器
   ↓
保存世界
   ↓
停止 Terraria
   ↓
复制 worlds/
   ↓
复制 serverconfig.txt
   ↓
新服务器
   ↓
docker compose up -d --build
```

---

# 14. API

FastAPI 后端：

```text
https://terraria-api.tyrantblue.xyz
```

健康检查：

```text
GET /api/health
```

例如：

```bash
curl https://terraria-api.tyrantblue.xyz/api/health
```

正常返回：

```json
{
  "status": "ok",
  "service": "terraria-api"
}
```

---

# 15. 主要 API

## 服务器

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

## 世界

```text
GET  /api/world/list
POST /api/world/upload
POST /api/world/switch
```

## 控制台 WebSocket

```text
wss://terraria-api.tyrantblue.xyz/api/server/ws
```

---

# 16. Docker 端口

Terraria：

```text
7777/tcp
7777/udp
```

FastAPI：

```text
8080/tcp
```

Docker Compose 映射：

```text
7777 → Terraria
8080 → FastAPI
```

---

# 17. 查看日志

Terraria：

```bash
docker compose logs -f terraria
```

API：

```bash
docker compose logs -f terraria-api
```

Terraria 运行日志同时保存在：

```text
/opt/terraria/control/output.log
```

该文件不会提交到 Git。

---

# 18. 查看容器状态

```bash
docker compose ps
```

也可以：

```bash
docker ps
```

---

# 19. 完整重建

如果 Docker 配置或依赖发生变化：

```bash
docker compose down
docker compose up -d --build
```

如果需要完全重新构建镜像：

```bash
docker compose build --no-cache
docker compose up -d
```

---

# 20. Git 工作流程

修改后端代码后：

```bash
cd /opt/terraria

git status
git add .
git commit -m "描述本次修改"
git push
```

例如：

```bash
git add .
git commit -m "Fix player list parsing"
git push
```

其他服务器更新：

```bash
git pull
docker compose up -d --build
```

---

# 21. 绝对不要提交的文件

执行：

```bash
git add .
```

之前，先检查：

```bash
git status
```

以下内容不要提交：

```text
worlds/
backup/
control/
data/
config/serverconfig.txt
.env
```

如果密码或其他密钥已经意外提交到 Git，即使之后删除文件，也应该认为密钥已经泄露，并立即更换。

---

# 22. Cloudflare 前端

前端项目是独立的 Git 仓库。

Cloudflare Worker：

```text
https://terraria-panel.tyrantblue32.workers.dev
```

前端使用 React + Vite。

构建：

```bash
pnpm build
```

部署：

```bash
pnpm wrangler deploy
```

Cloudflare 使用 SPA fallback，因此 React Router 的：

```text
/players
/worlds
/console
```

等路径可以直接刷新。

---

# 23. 常用命令速查

### 更新代码并重新部署

```bash
cd /opt/terraria
git pull
docker compose up -d --build
```

### 启动

```bash
docker compose up -d
```

### 停止

```bash
docker compose down
```

### 重启

```bash
docker compose restart
```

### 查看状态

```bash
docker compose ps
```

### 查看 Terraria 日志

```bash
docker compose logs -f terraria
```

### 查看 API 日志

```bash
docker compose logs -f terraria-api
```

---

# 24. 紧急回滚

如果更新后服务器出现问题：

```bash
cd /opt/terraria
git log --oneline
```

找到之前正常工作的 commit：

```bash
git checkout <COMMIT>
```

重新构建：

```bash
docker compose up -d --build
```

问题解决后：

```bash
git checkout main
git pull
```

---

# 25. VPS 迁移检查清单

```text
[ ] 保存 Terraria 世界
[ ] 停止 Terraria
[ ] 确认 worlds/*.wld
[ ] 确认 serverconfig.txt
[ ] 确认 backup/
[ ] 新服务器安装 Docker
[ ] 新服务器 clone GitHub 仓库
[ ] 恢复 worlds/
[ ] 恢复 serverconfig.txt
[ ] 恢复 backup/（如果需要）
[ ] docker compose up -d --build
[ ] 检查 docker compose ps
[ ] 检查 Terraria 日志
[ ] 检查 API health
[ ] 测试 Terraria 连接
[ ] 测试 Web 管理面板
```

---

# 26. 当前架构

```text
                         Internet
                            │
             ┌──────────────┴──────────────┐
             │                             │
             ▼                             ▼
       Cloudflare                     Terraria VPS
       前端                            Ubuntu 24.04
             │                             │
             │ HTTPS                       │
             ▼                             ├── Terraria :7777
      React + Vite                         │
      管理面板                              └── FastAPI :8080
             │                                  │
             │ HTTPS                            │
             └──────────────────────────────────┘
```

前端：

```text
terraria-panel.tyrantblue32.workers.dev
```

后端：

```text
terraria-api.tyrantblue.xyz
```

Terraria：

```text
VPS:7777
```

---

# 27. 核心原则

整个部署遵循：

```text
GitHub
   │
   └── 代码 / Docker / 部署配置

服务器
   │
   ├── 世界存档
   ├── 备份
   ├── 密钥和密码
   └── 运行时数据
```

**代码可以从 Git 恢复。**

**游戏数据必须单独备份和迁移。**

因此更换 VPS 时，不需要把整个 `/opt/terraria` 原样复制过去。

---

# 28. 连接守卫（防端口扫描 / 「人数已满」修复）

## 28.1 问题

Terraria 原版 Linux 专用服务端会把**任何一条到达 7777 端口的 TCP 连接**都计入
`maxplayers` 名额，包括端口扫描器、Censys/Rapid7 探测器和云主机上的批量扫描——
它们连上就断，而这些名额往往不会归还。于是控制台显示 `No players connected.`，
新玩家却一律收到 `This server is full right now`，只能重启容器才能恢复。

更严重的是，连接如果没有完成握手就断开，还可能**直接把服务端打崩**：

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

密码在这里没有用：密码是在 TCP 连接已经占用名额**之后**才校验的。
只有在网络层把非游戏连接挡在容器外面才能解决。

完整的日志证据与上游 bug 链接见 `docs/connection-guard.md`。

## 28.2 已部署的内容

| 组件 | 作用 |
| --- | --- |
| `guard/terraria-guard.sh` | 在 Docker 的 `DOCKER-USER` 链上加规则：动态封禁集合、白名单、单 IP 并发连接上限（4）、单 IP 新建连接速率上限（10/min）。`apply` / `status` / `remove` 幂等。 |
| `guard/terraria-watchd.py` | 守护进程：自动封禁扫描类 IP（连上就掉且从未 join、发畸形包），并在「假满员」时用 `save` + `exit` 自动恢复。 |
| `guard/allow.txt` | 可信玩家 IP（不受限流、不会被封）+ Docker 内部网段 `172.18.0.0/16`。 |
| `guard/systemd/*.service` | `terraria-guard.service` 开机重新应用规则；`terraria-scan-watcher.service` 常驻守护进程。 |
| `config/serverconfig.txt` | `maxplayers` 由 `8` 提升到 `255`（缓冲扩大 30 倍）。该文件不纳入 Git。 |

## 28.3 日常操作

```bash
sudo /opt/terraria/guard/terraria-guard.sh status     # 查看规则与集合
sudo /opt/terraria/guard/terraria-guard.sh apply      # 修改 allow.txt 后重新加载
sudo ipset del tg_ban <IP>                            # 手动解封某个 IP
journalctl -u terraria-scan-watcher -f                # 观察 [strike] / [ban] / [recover]
```

⚠️ 不要用 `nc` 或 `/dev/tcp` 去试探 7777：不经握手的裸 TCP 连接正是让服务端崩溃的触发器，
请用真实游戏客户端验证。

玩家登录成功**不会**自动加入 `guard/allow.txt`。成功登录只会在守护进程内部获得一段时间的
「免封禁」待遇；如需永久放行，请手动编辑 `allow.txt` 并执行一次 `terraria-guard.sh apply`。

## 28.4 回滚

```bash
sudo systemctl disable --now terraria-scan-watcher.service terraria-guard.service
sudo /opt/terraria/guard/terraria-guard.sh remove
# 再把 config/serverconfig.txt 的 maxplayers 改回 8 并重启容器
```

游戏容器本身没有被改造，回滚后行为与之前完全一致。

只需要：

```text
Git clone
    ↓
恢复 worlds/
    ↓
恢复 serverconfig.txt
    ↓
docker compose up -d --build
```

即可重新建立服务器。
