# Terraria 服务器「人数已满但没人」问题分析 · 优化方案

> 结论先行：这不是被 DDoS，也不是配置写错，而是 **Terraria 原版 Linux 专用服务端的已知缺陷**——
> 任何一条到达 7777 端口的 TCP 连接（包括端口扫描器、Censys/Rapid7 这类探测器）都会被计入
> `maxplayers` 名额，并且连接断开后名额经常不归还。于是面板显示 `No players connected.`，
> 新玩家却收到 `This server is full right now`，**只有重启进程才能恢复**。
> 原版服务端没有开关能修掉它，必须在**网络层**把非游戏连接挡在容器外面，并加一个自动恢复兜底。
>
> 而且不止“占位”：不完成握手的连接还能**直接把服务端打崩**
> （`ObjectDisposedException` in `Netplay.ServerLoop`，见 §2.4），
> 所以 `restart: unless-stopped` 每次拉起容器，有时是在从崩溃里恢复。

本文基于 2026-09-17 的 `control/output.log`（20011 行，覆盖 8 次服务端重启）分析。
**§5 记录已上线的改动与回滚方式。**

---

## 1. 现状梳理

| 项目 | 值 |
| --- | --- |
| 游戏服务端 | Terraria Dedicated Server v1.4.5.8（Ubuntu 24.04 容器，`/terraria-server`） |
| 管理后端 | FastAPI（容器 `terraria-api`，`0.0.0.0:8080`） |
| 世界 | `/opt/terraria/worlds/gogogo.wld`（worldname=WSD） |
| 端口 | `7777/tcp` + `7777/udp` 对全网发布；`8080/tcp` 由 `DOCKER-USER` 只允许 `192.0.2.8` |
| 配置 | `maxplayers=8`、`password=******`、`difficulty=1` |
| 控制通道 | `control/command.fifo`（start.sh 用 fd3 以读写方式常开）+ `control/output.log`（`tee -a` 追加） |

`docker-compose.yml` 里 `restart: unless-stopped`，所以 `docker restart terraria` / `docker compose restart` 就是目前唯一的“解药”。

---

## 2. 日志证据

对 `control/output.log` 的统计：

| 指标 | 数量 |
| --- | --- |
| 日志总行数 | 20,011 |
| 出现过连接行为的**不同来源 IP** | **92** |
| `is connecting...` | 189 |
| `lost connection...`（连上就掉，从未 join） | 134 |
| `was booted:` 合计 | 49 |
| ├ `Invalid operation at this state.`（畸形/非游戏数据包） | **30** |
| ├ `This server is full right now`（假满员受害者） | 13 |
| ├ `You are not using the same version as this server.` | 4 |
| └ `CTQ is already on this server.`（幽灵槽位） | 2 |
| 服务端真实重启次数（`Listening on port 7777`） | 8 |
| 日志里同一时刻“已连接但未结束”的连接数峰值 | ≈ 35 |

关键矛盾（第 15033、15674、15743 行附近）：

```
: No players connected.                                     <- playing 显示 0 人
: 147.182.247.70:41432 was booted: This server is full right now, please try again later.
192.0.2.50:18753 was booted: This server is full right now, please try again later.
192.0.2.50:18754 was booted: This server is full right now, please try again later.
192.0.2.50:18755 was booted: This server is full right now, please try again later.
192.0.2.50:18756 was booted: This server is full right now, please try again later.
```

**0 人在线却说“服务器已满”** → 槽位被看不见的连接占住了。

### 2.1 谁在敲门

典型扫描源（按连接次数）：

| 来源 | 特征 |
| --- | --- |
| `85.217.149.0/24` | **同一个 /24 里 22 个不同 IP 轮着来**，一次一行，典型的分布式扫描 |
| `66.132.172.x / 66.132.195.x / 66.132.224.x / 66.132.186.x` | Censys 探测器网段 |
| `198.235.24.x`、`64.62.197.x`、`64.62.156.66` | Censys / Hurricane Electric 探测 |
| `141.98.10.205`、`80.82.77.33`、`91.196.152.x`、`31.14.254.x`、`194.50.235.x`、`74.82.47.4`、`162.216.150.76` | 常见 mass-scan 网段 |
| `47.84.137.159 / 47.245.143.108 / 47.251.105.241`（阿里云）、`45.33.14.5 / 50.116.26.161 / 69.164.217.74`（Linode）、`3.22.187.122`（AWS）、`20.163.58.236 / 172.212.106.112`（Azure）、`147.182.247.70 / 164.92.211.98`（DigitalOcean）、`152.32.206.107`（UCloud HK） | 云主机批量扫描，**单 IP 反复连接 5~13 次**，是泄漏槽位的主力 |

被 `Invalid operation at this state.` 拒绝的连接（30 次）基本都来自上面的云主机，可以确定它们不是游戏客户端——真实客户端不会发畸形包。

### 2.2 哪些是自己人（务必别误伤）

从 `is connecting...` 下一行紧跟 `has joined.` 的关系还原：

| 玩家 | IP | 备注 |
| --- | --- | --- |
| ユノの犬 | `203.0.113.10`、`203.0.113.11` | |
| C | `198.51.100.20` | 中国电信 |
| CTQ、柳如烟 | `198.51.100.30` | 电信广东，两人同一出口 IP |
| X | `192.0.2.5`、`192.0.2.6` | 中国移动 |
| test | `192.0.2.7` | 电信上海（疑似服主自测） |

⚠️ 注意 `192.0.2.50` 连续 4 次被 `full` 拒绝——它和 `X` 的 `192.0.2.x` 同属中国移动同一段，
**这是玩家反复点“加入”造成的，不是扫描器**。所以“凡是被 full 拒绝的 IP 就封掉”是错的策略；
真正的判据是“**连上来后短时间内掉线且从未 join**”或“**发畸形包**”。

### 2.3 顺带发现的问题

1. **日志 65% 是面板自己刷的**：`Terraria Server v1.4.5.8` 出现 **1870 次**。因为 `GET /api/server/status`
   每次都往控制台按顺序发 7 条命令（`version/port/maxplayers/time/seed/motd/playing`），
   每条命令都会打印一段状态行。一次 status ≈ 7 行，1870 次 ≈ 1.3 万行。
   （**已修**：静态/动态字段分开缓存，见 `api/app/services/status.py`；旧路由本身也已在 2.0.0
   删除，现在读状态走 `GET /api/v1/server`。）
2. **日志无时间戳、无轮转**：`start.sh` 直接 `tee -a`，`output.log` 只能无限增长（现 512KB），
   出事后无法定位“几点被占满”。
3. **`7777/udp` 映射是多余的**：Terraria 1.4 PC 联机只走 TCP；这个映射只多出 4 个 `docker-proxy`
   进程和一份暴露面（社区 compose 示例已把该行注释掉）。
4. **`password=******` 且 API 无鉴权**：`main.py` 里 CORS `allow_origins=["*"]`，所有 `/api/v1/*`
   都没有 token（2.0.0 起旧 `/api/*` 已删除，鉴权问题依旧）；8080 只靠 `DOCKER-USER` 里一条
   “仅允许 `192.0.2.8`”的规则保护——你的宽带 IP 一变面板就挂，
   而这个 IP 一旦被运营商回收给别人，对方就拿到了服务器完全控制权（含改密码、踢人、换世界）。
   （**已部分缓解**：`GET /api/v1/config` 与 `/api/v1/server` 不再回显明文密码，见 2.0.0 CHANGELOG。）

---

### 2.4 更严重的失败模式：不完成握手的连接还能把服务端**直接打崩**

排查过程中（2026-09-17 14:07 CST）复现了一次致命崩溃 —— 只用 3 条“连上就关”的 TCP 连接：

```
172.18.0.1:35734 is connecting...
172.18.0.1:35742 is connecting...
172.18.0.1:35756 is connecting...
================
09/17/2026 06:07:36: Unhandled Exception
Thread: 8 [Server Loop Thread]
Exception: System.ObjectDisposedException: Cannot access a disposed object.
Object name: 'System.Net.Sockets.NetworkStream'.
  at Terraria.Net.Sockets.TcpSocket...IsConnected ()
  at Terraria.RemoteClient.IsConnected ()
  at Terraria.Netplay.UpdateConnectedClients ()
  at Terraria.Netplay.ServerLoop ()
[ERROR] FATAL UNHANDLED EXCEPTION: ...
```

连接未经握手就断开时，`UpdateConnectedClients()` 会去访问已经 Dispose 的 `NetworkStream`，
把服务端主循环直接打崩（社区同类报告：
[Dedicated server crashes with unhandled ObjectDisposedException…](https://forums.terraria.org/index.php?threads/multiplayer-dedicated-server-crashes-with-unhandled-objectdisposedexception-when-a-tcp-connection-is-opened-without-completing-the-handshake.151027/)）。
本项目 `restart: unless-stopped` 会自动把它拉起来（`RestartCount` 已从 1 涨到 4），
**所以容器重启有时不只是“清槽位”，而是在从崩溃中恢复**。

> ⚠️ 因此**不要用 `nc` / `/dev/tcp` 之类的裸 TCP 连接去“测试” 7777**，那正是触发器；
> 验证请用真实游戏客户端登录。

---

## 3. 根因

### 3.1 原版服务端的槽位计数缺陷

Terraria 服务端在 TCP 连接建立时就分配 `Netplay.Clients` 槽位，而扫描器只建连接、不发游戏握手包，
槽位却已经算进去了；掉线后计数也常常不归还。

| 证据/出处 | 内容 |
| --- | --- |
| [Non-game connections to dedicated server keeps 'client' open](https://forums.terraria.org/index.php?threads/non-game-connections-to-dedicated-server-windows-and-linux-dedicated-servers-tested-keeps-client-open.93212/) | 正是本项目现象：随机 IP（DigitalOcean 等）扫描 → 活跃人数虚增 → 自己反而连不进去 → 只能重启 |
| [Server connection stuck on "Connecting to ..." after some uptime](https://forums.terraria.org/index.php?threads/server-connection-stuck-on-connecting-to-after-some-uptime-possible-workaround.148894/) | 复现步骤：`maxplayers` 设为 1 后，用 `tnc <ip> -port 7777` 反复建 TCP 连接即可复现；并指出 `maxplayers` **不设（=255）时可复现不了** |
| [When Terraria server join max player, randomly not accept new connection](https://forums.terraria.org/index.php?threads/when-terraria-server-join-max-player-randomly-not-accept-new-connection.148618/) | Linux 上更严重：达到上限后监听线程直接死掉（`System.InvalidOperationException: Not listening`），端口不再接受连接，社区有人做了第三方 patch |

### 3.2 为什么其他配置都救不了

* **密码没用**：客户端是先建 TCP 连接、分配到槽位，之后才校验 `password`。密码只挡“玩”，不挡“占位”。
* **`difficulty`/`secure`/`npcstream` 都没关系**：缺陷在连接层，不在游戏层。
* **重启有效但代价高**：`docker restart` 会直接杀进程，**世界数据可能来不及落盘**；而原版只提供
  `save` + `exit` 这条优雅路径。

---

## 4. 优化方案（按优先级）

### P0-1 网络层限流 —— 挡住绝大多数扫描连接（立即可用）

`guard/terraria-guard.sh`：在 Docker 的 `DOCKER-USER` 链里加规则（和现有 8080 规则同一位置，互不影响）：

| 规则 | 作用 |
| --- | --- |
| `-m set --match-set tg_ban src -j DROP` | 命中动态封禁集合直接丢弃 |
| `-m set --match-set tg_allow src -j RETURN` | `allow.txt` 里的自己人放行，不受限流 |
| `ALLOWLIST_ONLY=1`（可选） | **只允许白名单**，从根上杜绝扫描（推荐朋友服） |
| `--connlimit-above 4 --connlimit-mask 32` | 单 IP 同时最多 4 条连接（日志里云主机常一次开 4~13 条） |
| `hashlimit --hashlimit-above 10/min --hashlimit-mode srcip` | 单 IP 每分钟最多新建 10 条连接，压住“连上就掉”的刷量 |

```bash
sudo apt-get install -y ipset          # tg_ban / tg_allow 需要
sudo /opt/terraria/guard/terraria-guard.sh apply
sudo /opt/terraria/guard/terraria-guard.sh status
```

### P0-2 自动封禁 + 假满员自动恢复 + 学习型白名单

`guard/terraria-watchd.py` 常驻，做三件事：

1. **自动封禁**（默认 30 分钟内 5 次可疑连接 → 封 1 小时）：
   * `X is connecting...` 后 `JOIN_GRACE`（默认 10s）内 `lost connection...` 且期间没有 `has joined.`；
   * `was booted: Invalid operation at this state.`；
   * `was booted: You are not using the same version...`。
   白名单 IP、以及近期成功 `join` 过的 IP **一律不封**。
2. **假满员自动恢复**：检测到 `This server is full right now` 在窗口内出现 ≥5 次时，
   先通过 FIFO 执行 `playing`，**确认 0 人在线**，再执行 `say` → `save` →（等待落盘）→ `exit`。
   `restart: unless-stopped` 会把服务端干净地拉起来（世界已保存），
   并且 30 分钟内最多自动重启一次，有人在线时绝不重启。
   进程启动时会先补读历史日志（用于重新封禁惯犯），**补读期间禁止任何动作**（`self.warmup`），
   避免“刚上线就把服务端 save+exit 一遍”。
3. **学习型白名单**：玩家 `has joined.` 后等 `LEARN_DWELL`（默认 60s），
   再用 `playing` 的输出精确解析「玩家名 → IP」，写入 `learned_allow.txt` 并即时 `ipset add tg_allow`。
   该 IP 从此不受 `connlimit`/`hashlimit` 限制、也不会被自动封禁；条目默认 7 天过期，最多 200 条。
   ⚠️ 这意味着“能登录 = 可信”，必须配合强密码；不想要就设 `LEARN_ALLOW=0`。

用历史日志干跑一遍（本次结果：只封 8 个 IP，且全部是云主机扫描源，自己人一个没伤）：

```bash
python3 guard/terraria-watchd.py --once --no-recover --dry-run
# [ban] 152.32.206.107 / 3.22.187.122 / 45.33.14.5 / 47.245.143.108
#      47.251.105.241 / 47.84.137.159 / 50.116.26.161 / 69.164.217.74
```

### P1-1 最彻底：白名单模式

朋友服只有 7 个出口 IP，直接在 `guard/allow.txt` 维护，然后：

```bash
# /etc/systemd/system/terraria-guard.service 里改为 ALLOWLIST_ONLY=1
sudo systemctl daemon-reload && sudo systemctl restart terraria-guard
```

代价：玩家换宽带/切 4G 网段就进不来，需要改 `allow.txt` 并重跑 `terraria-guard.sh apply`。
如果不想维护，就用 P0-1 + P0-2 的组合。

### P1-2 给 `maxplayers` 留缓冲（✅ 已按“=255”上线）

`maxplayers=8` 意味着只要泄漏 8 个槽位就满。按日志流量（约 60 条陌生连接/天）：

| 方案 | 效果 | 代价 |
| --- | --- | --- |
| 保持 `maxplayers=8` + P0/P1 | 扫描被挡，泄漏大幅下降，但真人异常掉线仍会缓慢泄漏 | 依赖自动恢复 |
| 改 `maxplayers=20` | 槽位缓冲区 12 个，泄漏要攒更久才满 | 面板显示上限 20 |
| **`maxplayers=255`（已采用）** | 缓冲拉大 30 倍，社区实测可基本规避该 bug | 玩家上限形同取消，务必配合强密码 + 白名单 |

实测补充：**1.4.5.8 上把 `maxplayers` 这一行注释掉并不会变成 255，而是回落到 8**
（第一版改成注释后 `maxplayers` 查询仍返回 8），所以最终写成显式 `maxplayers=255`。


### P1-3 去掉没用的 UDP 映射

```yaml
ports:
  - "7777:7777/tcp"
  # - "7777:7777/udp"   # Terraria 1.4 PC 只用 TCP
```

### P1-4 换成非默认端口

`serverconfig.txt` 的 `port=` 与 compose 的端口映射一起改成比如 `27777`。
通用端口扫描器仍会扫到，但能过滤掉“只扫 7777”的低成本爬虫，成本几乎为零。

### P2 日志与 API 优化

1. **给日志加时间戳**（`start.sh` 里 `tee` 前接一个 `awk` 前缀）——**同时要改 API 的解析**：
   `app/routers/server.py::parse_players` 用的是 `re.match(r"^(.+?) \(.+:\d+\)$")`，
   加了时间戳前缀会把时间戳算进玩家名，必须改成 `re.search`。
2. **日志轮转**：`/etc/logrotate.d/terraria`（`control/output.log`，`copytruncate`，保留 7 天）。
   `terraria-watchd.py` 已能识别日志被轮转/截断。
3. **`/api/server/status` 降载** ✅（已实现 `StatusCollector` 静态/动态分离缓存；旧路由已在 2.0.0 删除）。
   当时的分析：一次请求 = 7 条控制台命令 + 最多 7×2s 的等待。
   建议：
   * 加 3~5 秒的服务端 TTL 缓存（`functools.lru_cache` 或简单时间戳缓存），合并面板轮询；
   * `version/port/maxplayers/seed/motd` 从日志/启动横幅解析或缓存一次，只保留 `time` + `playing` 实时查询；
   * `get_recent_output()` 目前 `readlines()` 会整文件读入内存，改为读取尾部 N KB。
4. **`console.fifo` 写入加锁**：API 与 watchd 都可能写 FIFO，建议用文件锁串行化。

### P2+ 安全

* `password` 换成 12 位以上强密码（`password` 只防玩不防占位，但不能是弱口令）。
* 给 FastAPI 加 Bearer token（或至少把 CORS 的 `allow_origins` 收窄到面板域名），
  避免“靠一个来源 IP 当认证”。
* 8080 的安全组/防火墙保持“只放行面板出口 IP”，并在换 IP 时同步更新
  `iptables -R DOCKER-USER 1 -s <新IP> ...`。

### P3 更彻底但改动大

* 换 **TShock**：自己实现连接握手/超时，有 [Connection.Limit](https://tshock.co/xf/index.php?resources/connection-limit.205/) 之类的插件，
  对“非游戏连接”的处理比原版健壮（世界文件兼容）。
* 玩家全部走 **WireGuard / Tailscale**，compose 里 `7777` 只绑内网网卡（`127.0.0.1:7777` 或私网 IP），
  公网不再暴露游戏端口——扫描流量直接归零。

---

## 5. 上线状态与操作手册

### 5.1 当前状态（2026-09-17 15:20 CST）

| 项目 | 状态 |
| --- | --- |
| `ipset` | 宿主机已安装（v7.19）；容器镜像里也带了 ipset/iptables |
| 运行方式 | **Compose 侧车容器 `terraria-guard`**（`network_mode: host` + `NET_ADMIN`/`NET_RAW`），`docker compose up -d` 即随之启动 |
| `DOCKER-USER` | 4 条 `terraria-guard` 规则：`tg_ban` DROP → `tg_allow` RETURN → 单 IP 并发 >4 DROP → 单 IP 新建 >10/min DROP |
| `tg_ban` | 已自动封禁 8 个扫描源：`152.32.206.107`、`3.22.187.122`、`45.33.14.5`、`47.245.143.108`、`47.251.105.241`、`47.84.137.159`、`50.116.26.161`、`69.164.217.74` |
| `tg_allow` | 8 条：7 个真实玩家 IP + `172.18.0.0/16`（Docker 内部网段，防止误封面板/本机） |
| 学习型白名单 | 已启用（`LEARN_ALLOW=1`，在线 60s 后学习，7 天有效，最多 200 条），落地到 `guard/learned_allow.txt` |
| `terraria-guard.service` / `terraria-scan-watcher.service` | **disabled + inactive**，仅作为不使用 Docker 时的备用方案 |
| `config/serverconfig.txt` | `maxplayers=255`（原 8），世界 `gogogo.wld` 已 `save` 后优雅重启加载 |
| 服务端 | `Player limit: 255`，`No players connected.` |

> 注意：守护进程启动时会先把已有日志**补读**一遍（用于重新封禁惯犯），
> 补读期间被显式禁止触发 `save/exit`（`self.warmup`），只有启动之后新发生的事件才会触发自动恢复。
> 补读结束会打印 `[watchd] 历史日志补读完成，开始只监控新事件`。

### 5.2 重复执行 / 日常操作

```bash
cd /opt/terraria

# 看守护进程在做什么（strike/ban/learn/recover）
docker compose logs -f terraria-guard
docker compose ps                      # terraria-guard 必须保持 Up

# 改动 allow.txt 后重新加载（幂等，不会清空 tg_ban）
sudo ./guard/terraria-guard.sh apply   # 宿主机直接跑，操作的是同一套 netfilter
sudo ./guard/terraria-guard.sh status

# 手工解封某个 IP
sudo ipset del tg_ban <IP>

# 临时关闭自动恢复 / 关闭自动白名单：改 docker-compose.yml 里的 environment 后
docker compose up -d terraria-guard
```

### 5.3 安全验证（⚠️ 不要用裸 TCP 连接去试探 7777）

```bash
# 规则是否在链上（4 条）
sudo iptables -S DOCKER-USER | grep terraria-guard
# 容器与宿主机看到的是同一套规则
docker exec terraria-guard iptables -S DOCKER-USER | grep -c terraria-guard
# 白名单 / 封禁集合
sudo ipset list tg_allow; sudo ipset list tg_ban
# 规则命中计数（有扫描流量时会增长）
sudo iptables -L DOCKER-USER -n -v | grep 7777
# 守护日志（应只有 [ban]；新事件才会出现 [strike]/[learn]/[recover]）
docker compose logs --tail 50 terraria-guard
# 用真实游戏客户端登录一次，确认正常玩家不受影响
# 登录满 60 秒后应看到 [learn] xxx 加入白名单 tg_allow，并出现在 learned_allow.txt
```

真实远程流量走 DNAT → `FORWARD` → `DOCKER-USER`（与现有 8080 保护同一条链，
可在 `iptables -t nat -L DOCKER -n -v` 看到 `dpt:7777` 的 DNAT 计数），
所以上面这批规则对公网扫描是生效的；只有**本机/容器自身**发起的连接会经 `docker-proxy`
走 INPUT，不经过 `DOCKER-USER`（也正因如此才不需要给本机放行任何规则）。

### 5.4 回滚

```bash
cd /opt/terraria
docker compose stop terraria-guard          # 入口脚本的 trap 会撤销规则
sudo ./guard/terraria-guard.sh remove       # 双保险
# 游戏容器本身没有被改造，回滚后行为与之前完全一致
# 如需恢复旧上限：把 config/serverconfig.txt 改回 maxplayers=8 并重启容器
# 如想改用 systemd 方案：systemctl enable --now terraria-guard terraria-scan-watcher
```

---

## 6. 文件清单

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `guard/terraria-guard.sh` | ✅ 新增并已应用 | 网络层限流 + 封禁/白名单集合（`apply/remove/status`，幂等） |
| `guard/terraria-watchd.py` | ✅ 新增并常驻 | 自动封禁 + 假满员自动恢复 + 学习型白名单（纯标准库；`--dry-run/--once/--backfill/--no-learn`） |
| `guard/allow.txt` | ✅ 新增 | 手工白名单：7 个真实玩家 IP + `172.18.0.0/16` |
| `guard/learned_allow.txt` | ✅ 运行时生成 | 学习型白名单（IP + 过期时间戳），已加入 `.gitignore` |
| `guard/Dockerfile` | ✅ 新增 | 守卫镜像：debian-slim + iptables(nft)/ipset/python3 |
| `guard/docker-entrypoint.sh` | ✅ 新增 | 侧车入口：apply → 运行守护 → 退出时 remove |
| `docker-compose.yml` | ✅ 已改 | 新增 `terraria-guard` 服务（host 网络 + NET_ADMIN）；`7777/udp` 映射仍待删 |
| `guard/systemd/*.service` | ✅ 保留为备用 | 已 disable；改用 Docker 部署时不要同时启用 |
| `docs/connection-guard.md` | ✅ 新增 | 本文 |
| `config/serverconfig.txt` | ✅ 已改 | `maxplayers=255`（未提交 Git：含密码） |
| `password` | ⏳ 待改 | 仍是弱口令；**启用自动白名单后更必须换掉**，建议通知玩家后一起改 |
| `start.sh` | ⏳ 待改 | 日志加时间戳（需同步改 API 解析）、服务端退出后在容器内自动重启 |
| `api/app/routers/server.py` | ⏳ 待改 | `/status` 加缓存 + 解析改 `re.search` + 尾部读取日志 |
| `api/app/main.py` | ⏳ 待改 | 加 Bearer token、收窄 CORS |
| `/etc/logrotate.d/terraria` | ⏳ 建议 | 控制 `control/output.log` 无限增长（本次未创建，避免超出授权范围） |

