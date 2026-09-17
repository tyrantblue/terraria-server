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

### 接口契约与版本

后端采用「契约先行」的流程，让前端始终知道接口变成了什么样：

| 产物 | 作用 |
| --- | --- |
| `api/openapi.json` | 机器可读的契约快照，提交进仓库并由 CI 校验；前端据此用 `openapi-typescript` 生成 TS 类型 |
| `docs/api/CHANGELOG.md` | 人读的变更说明，带迁移示例与 Sunset 日期 |
| `GET /api/meta` | 运行时握手：`api_version`、`min_client_version`、`server_version`、`capabilities`、`deprecations`。面板启动时调用一次，版本不匹配就提示用户刷新面板 |
| `/docs`、`/openapi.json` | FastAPI 自带的交互式文档与实时 schema |

变更原则：**先加不删** —— 旧路由继续可用，新增路由并存；被弃用的路由返回
`Deprecation`/`Sunset` 响应头；删除只在大版本做。更新快照：

```bash
cd api
python scripts/export_openapi.py          # 更新 api/openapi.json
python scripts/export_openapi.py --check  # CI 用：快照过期就失败
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
| `guard/allow.txt` | 手工维护的白名单：可信玩家 IP（不受限流、不会被封）+ Docker 内部网段 `172.18.0.0/16`。 |
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
