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
GET /api/v1/server
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

### 第四步（续）：复制守卫白名单

`guard/allow.txt`（手工维护的玩家白名单）**也不在 Git 中**——它里面是真实玩家 IP，
仓库里只保留 `guard/allow.txt.example`。迁移时必须单独复制，否则新机器上的守卫会把
所有玩家都当成陌生连接：

```bash
scp root@OLD_SERVER_IP:/opt/terraria/guard/allow.txt /opt/terraria/guard/
# 或者在新机器上从示例开始手工填写：
# cp guard/allow.txt.example guard/allow.txt
```

`guard/learned_allow.txt` 由守护进程自动重建（玩家重新登录后会再学到），
不复制也没关系，但复制过去可以少一轮“第一次连接被限流”。

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

### 接口版本

**2.0.0（2026-09-19）删除了旧的 `/api/*` 路由**：面板（`tyrantblue/terrWeb` 1.4.0）
已全量迁移，契约快照里也不再有任何旧路径。`/api/meta` 仍会返回 `min_client_version`
（现在是 `1.4.1`），所以旧面板会在握手上拿到明确的「请升级」提示，而不是运行到一半 404；
并且 **2.1.0 起服务端会强制它**：带更低 `X-Client-Version` 的请求直接返回 426
`client_outdated`（见 §29.6）。2.1.0 还新增了结构化 operation 文案
（`message_code` / `message_params`）、带 `capability_since` 的完整能力清单、
写操作限流与可选的写 token。

| 文档 | 内容 |
| --- | --- |
| `docs/api/v1.md` | **给前端的 `/api/v1` 参考**（端点、请求响应、示例） |
| `docs/api/CHANGELOG.md` | 每次契约变更，带迁移示例 |
| `docs/roadmap.md` | 后续功能规划与已知缺口 |

变更原则：**先加不删** —— 新增用 `minor`（前端用 `capabilities` 判断新 UI 是否可用），
删除/语义变更用 `major`。更新快照与跑测试：

```bash
cd api
uv run python scripts/export_openapi.py          # 更新 api/openapi.json
uv run python scripts/export_openapi.py --check  # CI 用：快照过期就失败
uv run pytest -q                                 # 测试（假 FIFO/假日志，不需真服务端）
```

重构方案与设计说明见 `docs/api-refactor-plan.md`。

---
# 15. 主要 API

## 系统

```text
GET  /api/health
GET  /api/meta          # 给前端做 API 版本握手
```

## 服务器

```text
GET  /api/v1/server              # 一次拿全 + log_stalled / log_age
POST /api/v1/server/actions      # save | settle
POST /api/v1/server/restart      # 202 + operation_id
POST /api/v1/server/time         # dawn | noon | dusk | midnight
GET  /api/v1/config              # 敏感键已掩码：password -> "••••••" + password_set
PUT  /api/v1/config
GET  /api/v1/metrics             # CPU / 内存 / 磁盘 / 在线人数曲线（?minutes=60）
```

## 玩家、控制台与审计

```text
GET    /api/v1/players
POST   /api/v1/players/{name}/kick
POST   /api/v1/players/{name}/ban
DELETE /api/v1/players/{name}/ban
GET    /api/v1/bans
POST   /api/v1/broadcast
GET    /api/v1/console?tail=200
GET    /api/v1/console/audit?tail=200   # 落盘在 control/audit.log
POST   /api/v1/console/commands         # 白名单 + 审计
```

## 控制台 WebSocket

```text
wss://terraria-api.tyrantblue.xyz/api/v1/console/stream
```

## 世界与备份

```text
GET    /api/v1/worlds                    # 带 .wld 元数据（尺寸档位 / 难度）
POST   /api/v1/worlds                    # 上传（超限 413、余量不足 507）
DELETE /api/v1/worlds/{file}
POST   /api/v1/worlds/{file}/activate    # 202
POST   /api/v1/worlds/{file}/backup      # 202
GET    /api/v1/backups
POST   /api/v1/backups/{name}/restore    # 202
```

## 长任务、定时任务、通知、守卫

```text
GET  /api/v1/operations/{id}     # 202 长任务进度
GET  /api/v1/scheduler
POST /api/v1/scheduler/{name}/run
GET  /api/v1/notifications
POST /api/v1/notifications/test
GET  /api/v1/guard               # 白名单 / 封禁 / 计数器
POST /api/v1/guard/bans
POST /api/v1/guard/allow
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
             ▼                             ├── Terraria :7777  ← 由 terraria-guard
      React + Vite                         │     在 DOCKER-USER 链上过滤
      管理面板                              └── FastAPI :8080
             │                                  │
             │ HTTPS                            │
             └──────────────────────────────────┘
```

Docker Compose 服务：`terraria`（游戏服务端）、`terraria-api`（面板后端）、
`terraria-guard`（连接守卫，host 网络 —— 见第 28 节）。

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

即可重新建立服务器（连接守卫也已放进 compose，见第 28 节）。

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
| `guard/terraria-watchd.py` | 守护进程：自动封禁扫描类 IP（连上就掉且从未 join、发畸形包）、在「假满员」时用 `save` + `exit` 自动恢复，并给成功登录的玩家自动加白名单。 |
| `guard/allow.txt` | 手工维护的白名单：可信玩家 IP（不受限流、不会被封）+ Docker 内部网段 `172.18.0.0/16`。**命令 `add` 会写这个文件，仓库里只保留 `allow.txt.example`，真实文件不进 Git（含真实玩家 IP）——迁移时要单独 `scp`，见「迁移服务器」一节。** |
| `guard/learned_allow.txt` | 守护进程自动生成：成功登录并在线满时长的玩家 IP。不纳入 Git。 |
| `guard/Dockerfile` + `guard/docker-entrypoint.sh` | 侧车容器镜像与入口：应用规则 → 运行守护进程 → `docker compose down` 时移除规则。 |
| `guard/systemd/*.service` | 备用的“非 Docker”部署方式（开机应用规则 + 常驻守护）。侧车在跑时请保持它们 **disabled**。 |
| `config/serverconfig.txt` | `maxplayers` 由 `8` 提升到 `255`（缓冲扩大 30 倍）。该文件不纳入 Git。 |

### 运行方式：Compose 侧车容器

`docker-compose.yml` 里多了一个 `terraria-guard` 服务。它使用 `network_mode: host` 加
`NET_ADMIN`/`NET_RAW`（不是 `privileged`），因此容器里的 `iptables`/`ipset` 直接作用于
**宿主机**的 netfilter——也就是其他容器发布端口所经过的同一条 `DOCKER-USER` 链。
它同时挂载 `./control`（FIFO + 日志）和 `./guard`（脚本、`allow.txt`、`learned_allow.txt`）。

带来的好处：

* **迁移时一条 `docker compose up -d --build` 就够了**，不需要在宿主机上额外装软件、
  也不需要装 systemd 单元。
* 守卫参数（`MAX_CONN_PER_IP`、`NEW_CONN_RATE`、`ALLOWLIST_ONLY`、`LEARN_*` 等）都写在
  `docker-compose.yml` 里，跟着仓库一起走。
* 规则应用失败时容器会以非 0 退出，`docker compose ps` 会显示它在重启，
  不会出现“服务在跑但其实没防护”的静默状态。
* 代价：这个容器持有宿主机网络命名空间和 `NET_ADMIN`，也就是有能力改写宿主机防火墙。
  这正是它需要做的事，但权限确实比 systemd 方案大。如果不接受，可以停掉
  `terraria-guard` 服务改用 `guard/systemd/` 里的单元（**不要同时开两套**）。

## 28.3 登录成功自动加白名单（学习型）

玩家 `has joined.` 之后，守护进程会等 `LEARN_DWELL` 秒，再用 `playing` 的输出精确解析出
该玩家名对应的 IP，写入 `guard/learned_allow.txt` 并即时加入 `tg_allow` 集合。
进入白名单的 IP 不受连接限流，也不会被自动封禁。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `LEARN_ALLOW` | `1` | 设 `0` 彻底关闭自动加白名单 |
| `LEARN_DWELL` | `60` | 至少在线多少秒才学习（`0` = 登录即学习） |
| `LEARN_TTL` | `604800` | 条目有效期（秒，`0` = 永久） |
| `LEARN_MAX` | `200` | 最多保留条数，超出时淘汰最早过期的 |

> ⚠️ **安全取舍**：`tg_allow` 是**完全绕过** `connlimit`/`hashlimit` 的，所以这个功能等价于
> “能登录 = 可信”。谁知道服务器密码，谁就能登录一次、然后无限开连接。
> **依赖这个功能之前务必先把弱口令换掉**；或者设 `LEARN_ALLOW=0`，回到手工维护 `allow.txt`。

撤销单个条目：删掉 `guard/learned_allow.txt` 里对应行，再 `sudo ipset del tg_allow <IP>`。
全部撤销：停掉守卫容器、删掉该文件，再 `docker compose up -d terraria-guard`。

## 28.4 日常操作

```bash
docker compose logs -f terraria-guard                 # 观察 [strike] / [ban] / [learn] / [recover]
docker compose restart terraria-guard                 # 重新应用规则（如 Docker 升级后）
docker compose ps                                     # terraria-guard 应保持 Up

# 脚本也可以在宿主机上直接跑（操作的是同一套 netfilter）：
sudo /opt/terraria/guard/terraria-guard.sh status     # 查看规则、集合与白名单条数
sudo /opt/terraria/guard/terraria-guard.sh apply      # 修改 allow.txt 后重新加载
sudo ipset del tg_ban <IP>                            # 手动解封某个 IP
```

守卫容器跑在 host 网络上并写宿主机防火墙，所以宿主机重启或 Docker 升级之后，
确认它还活着、规则还在：

```bash
docker compose ps
sudo iptables -S DOCKER-USER | grep terraria-guard     # 应该是 4 条
```

⚠️ 不要用 `nc` 或 `/dev/tcp` 去试探 7777：不经握手的裸 TCP 连接正是让服务端崩溃的触发器，
请用真实游戏客户端验证。

## 28.5 回滚

```bash
# 停掉守卫（入口脚本的 trap 会自己把规则撤掉）
docker compose stop terraria-guard
sudo /opt/terraria/guard/terraria-guard.sh remove     # 双保险
# 再把 config/serverconfig.txt 的 maxplayers 改回 8 并重启容器
```

游戏容器本身没有被改造，回滚后行为与之前完全一致。
`guard/systemd/` 里的单元仍可作为替代方案使用，但要在 `terraria-guard` 服务停止时才启用
（两套不能同时跑）。
---

# 29. 定时任务、备份与通知

下面这些都在 `docker-compose.yml`（`terraria-api` 服务）里配置，通过 `/api/v1` 暴露。

## 29.1 定时任务

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `SCHEDULE_ENABLED` | `1` | 总开关 |
| `SCHEDULE_SAVE_MINUTES` | `15` | 保存间隔（`0` = 关闭） |
| `SCHEDULE_SAVE_SKIP_EMPTY` | `1` | 没人在线就不保存 |
| `SCHEDULE_BACKUP_HOURS` | `6` | 自动备份间隔（`0` = 关闭） |
| `SCHEDULE_BACKUP_KEEP` | `10` | 只保留最近 N 份（`pre-restore-*` 永不清理） |
| `SCHEDULE_CONSOLE_CHECK_SECONDS` | `60` | 控制台心跳：探活 FIFO→日志，日志管道停更时告警（`0` = 关闭） |
| `CONSOLE_STALL_COOLDOWN` | `1800` | 两次 `log_stalled` 告警之间的最小间隔（秒） |
| `LOG_STALL_SECONDS` | `120` | 日志超过这么久没更新（且能读到版本）→ `GET /api/v1/server` 报 `log_stalled` |
| `SCHEDULE_RESTART_AT` | `05:00` | 每天重启时间，留空则关闭 |
| `SCHEDULE_RESTART_SKIP_IF_PLAYERS` | `1` | 有人在线就跳过重启 |
| `SCHEDULE_RESTART_WARN_MINUTES` | `5` | 重启前广播提醒 |
| `SCHEDULE_TZ` | `Asia/Shanghai` | 定时重启用哪个时区（容器默认 UTC） |

```bash
curl localhost:8080/api/v1/scheduler                    # 任务、下次执行时间、上次结果
curl -X POST localhost:8080/api/v1/scheduler/save/run   # 立即执行一次
```

## 29.2 备份与恢复

自动备份在 `backup/<YYYYmmdd-HHMMSS>/`（世界文件 + `serverconfig.txt`）。
Terraria 自己写的 `.wld.bak`/`.bak2` 也会出现在列表里（`kind: "auto"`），同样可以恢复。

```bash
curl localhost:8080/api/v1/backups
curl -X POST localhost:8080/api/v1/backups/20260917-170849/restore      # 202 + operation_id
curl -X POST localhost:8080/api/v1/backups/auto:gogogo.wld.bak/restore  # 用游戏自己的备份恢复
```

恢复**当前激活的世界**会重启两次（停服 → 替换文件 → 用 `exit-nosave` 再启动，
否则退出时的自动保存会把刚恢复的文件覆盖回去），并在
`backup/pre-restore-<时间戳>/` 留一份覆盖前的安全副本。恢复其他世界只复制文件、不重启。

## 29.3 事件通知

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `NOTIFY_WEBHOOK_URL` | 空 | 留空即关闭 |
| `NOTIFY_FORMAT` | `auto` | `auto`（按 URL 猜）/ `discord` / `slack` / `json` |
| `NOTIFY_EVENTS` | 空 | 逗号分隔的白名单，空 = 全部 |

事件：`player_join`、`player_leave`、`player_booted`、`server_up`、`server_error`、
`backup_done`、`schedule_failed`、`restart_skipped`、`log_stalled`。

> 2.0.0 起日志停滞告警的事件名从 `console_stalled` 改成 `log_stalled`；
> 如果你的 `NOTIFY_EVENTS` 里写死了旧名字，记得一起改。

```bash
curl localhost:8080/api/v1/notifications
curl -X POST localhost:8080/api/v1/notifications/test
```

## 29.4 日志时间戳与轮转

`start.sh` 会给控制台每一行加上 `[YYYY-mm-dd HH:MM:SS]` 前缀（时区取容器的 `TZ`，
镜像里已装 `tzdata`）。`/api/v1/console` 把它解析成 `ts` 字段。
`ops/logrotate.terraria`（装到 `/etc/logrotate.d/terraria`）在 20M 时轮转 `control/output.log`，
保留 4 份，用 `copytruncate`（容器里的 `tee` 不受影响）：

```bash
sudo install -m 644 ops/logrotate.terraria /etc/logrotate.d/terraria
```

同一个文件里还有 `control/audit.log` 的轮转（控制台命令审计，5M/保留 12 份，同样
`copytruncate`）——API 每次写入都重新 `open(O_APPEND)`，截断后继续追加到新末尾。

API 与守卫都能解析**有/无**时间戳两种格式，所以把 `start.sh` 里的 `awk` 去掉是安全的回滚方式。

## 29.5 资源曲线与上传上限

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `METRICS_INTERVAL_SECONDS` | `60` | 资源采样间隔（`0` = 关闭，`/api/v1/metrics` 返回空数组） |
| `METRICS_RETENTION_POINTS` | `1440` | 内存里保留多少个采样点（1 分钟粒度 = 24 小时） |
| `TERRARIA_WORLD_UPLOAD_MAX_BYTES` | `524288000` | 上传单个 `.wld` 的上限（约 500MB），超限 413、磁盘不足 507 |

采样内容：容器 CPU%（读 cgroup v2 的 `cpu.stat`/`cpu.max`，v1 回退到 `cpuacct`、
`memory.*`）、内存用量与上限、`worlds/` 所在卷的可用/总空间、在线人数。
数据只在内存里，API 重启后从零开始——它用于看近期曲线，不是长期监控存储。

```bash
curl -s 'localhost:8080/api/v1/metrics?minutes=60' | jq '.latest'
```

## 29.6 API 侧安全加固

API 假定鉴权在边缘完成（Cloudflare Access / VPN / 受限反向代理）。如果边缘没有，
后端提供这些可选的进程内保护（参考 `docs/api/v1.md` §0）：

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| `TERRARIA_API_TOKEN` | 空 | 非空即启用共享 token：写方法（POST/PUT/PATCH/DELETE）必须带 `Authorization: Bearer <token>` 或 `X-API-Token`，否则 **401** `unauthorized`。GET 不受影响。 |
| `TERRARIA_CORS_ORIGINS` | `*` | 逗号分隔的允许来源；生产环境应收敛为面板实际来源。无论如何都会 `expose` 回显头 `X-Client-Version`。 |
| `TERRARIA_RATE_LIMIT_ENABLED` | `1` | 是否启用写操作限流（进程内 60 秒滑动窗口）。 |
| `TERRARIA_RATE_LIMIT_WRITE_PER_MINUTE` | `120` | 普通写操作上限（每客户端）。 |
| `TERRARIA_RATE_LIMIT_CONSOLE_PER_MINUTE` | `60` | `POST /api/v1/console/commands` 的上限。 |
| `TERRARIA_RATE_LIMIT_RESTART_PER_MINUTE` | `12` | `POST /api/v1/server/restart` 的上限。 |

* 超限返回 **429** `too_many_requests`，并带 `Retry-After` 头。
* 带 `X-Client-Version` 且**低于** `min_client_version`（当前 `1.4.1`）的请求返回
  **426** `client_outdated`；`/api/meta` 与 `/api/health` 除外，好让老面板仍能通过
  握手知道自己该升级。不带头（脚本/curl）不拦。
* 限流器是**进程内**的：多副本部署需要共享存储。
* 它按 `request.client.host` 计数。如果 API 在反向代理后面而 uvicorn 没启用
  `--proxy-headers`，所有请求会共用代理的桶——请启用 `--proxy-headers` 并正确配置
  `--forwarded-allow-ips`，或把限流放到边缘。
* `POST /api/v1/server/restart` 只对正在运行的游戏服有效（要写控制台 FIFO）。
  已经退出的容器请用 `docker compose restart terraria` 拉起。

