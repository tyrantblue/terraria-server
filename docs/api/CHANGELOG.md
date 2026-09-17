# Terraria Server API — 变更记录

这份文件是**接口变更的权威人类可读文档**。契约同步一共三件套：

| 产物 | 作用 |
| --- | --- |
| `api/openapi.json` | 机器可读的契约快照，前端据此生成 TS 类型；CI 校验 |
| `docs/api/CHANGELOG.md`（本文件） | 人读的变更说明，带迁移示例与 Sunset 日期 |
| `GET /api/meta` | 运行时握手：版本不匹配时前端可提示用户刷新面板 |

规则：

* **破坏性变更只在 major 版本做**，并且提前一个发布周期在本文件里给出 Sunset 日期。
* 每条变更都要写清 `Added / Changed / Deprecated / Removed`，并给出 old → new 的迁移写法。
* 旧接口与新接口**并行运行**；删除前先看弃用埋点数据（`/api/meta` 里会列出弃用项）。

---

## [1.3.0] — 2026-09-17

按 `../roadmap.md` 的 P1 清单继续开发：**定时任务、备份恢复、事件通知、日志时间戳+轮转**。
仍然是「只增不删」，旧接口照常可用。

### Added

* **定时任务**（配置见 `docker-compose.yml` 的 `SCHEDULE_*`）：
  * `save` —— 每 15 分钟保存一次，**没人在线自动跳过**（省掉一次 12MB 无意义落盘）；
  * `backup` —— 每 6 小时自动备份 + **按数量清理旧备份**（`pre-restore-*` 与迁移备份不清理）；
  * `restart` —— 每天 `SCHEDULE_RESTART_AT`（默认 05:00，时区 `SCHEDULE_TZ`）重启，
    **有人在线就跳过**并广播提示。
  * `GET /api/v1/scheduler` 查看任务与下次执行时间；
    `POST /api/v1/scheduler/{name}/run` 手动触发一次（返回本次结果）。
* **备份恢复**：`POST /api/v1/backups/{name}/restore` → **202**。
  * 恢复「当前激活的世界」会走两段式：保存 → 存安全副本 → `exit` → 覆盖文件 → `exit-nosave`
    （第二次必须不保存，否则退出时的自动保存会把恢复的文件覆盖回去）；
  * 恢复「非激活世界」只复制文件，不重启；
  * 结果里带 `sha256` 与 `safety_copy`（`backup/pre-restore-<时间戳>/`）；
  * `name` 支持 `auto:gogogo.wld.bak` 形式，可以直接用 Terraria 自己写的 `.wld.bak`/`.bak2`。
  * `GET /api/v1/backups` 的条目新增 `kind`（manual/auto/legacy）、`restorable`、`path`。
* **事件通知（webhook）**：`NOTIFY_WEBHOOK_URL` / `NOTIFY_FORMAT`（auto/discord/slack/json）/
  `NOTIFY_EVENTS`。
  * 事件：`player_join`、`player_leave`、`player_booted`、`server_up`、`server_error`、
    `backup_done`、`schedule_failed`、`restart_skipped`；
  * `GET /api/v1/notifications` 查看配置与最近投递结果（URL 做掩码，不回显完整 webhook）；
  * `POST /api/v1/notifications/test` 立刻发一条测试消息。
* **日志时间戳**：`start.sh` 现在给每行加 `[YYYY-mm-dd HH:MM:SS] ` 前缀（时区取 `TZ`）。
  * `GET /api/v1/console` 与 `WS /api/v1/console/stream` 的每行新增 `ts`（epoch，可能为 null）；
  * 加了 `/etc/logrotate.d/terraria`（`size 20M`、保留 4 份、`copytruncate`），
    `output.log` 不再无限增长。
* 游戏镜像补了 `tzdata`（否则容器里的 `TZ` 无效、时间戳全是 UTC）。

### Changed

* 备份目录名与 `pre-restore-*` 目录名改用容器时区（`TZ`，默认 Asia/Shanghai）。
  此前 API 容器是 UTC，目录名会比本地时间早 8 小时。
* API 容器也设置了 `TZ`，`ts` 字段按 `TERRARIA_LOG_TZ` 解析，不会整体偏移。

### Fixed

* **备份以前写在 API 容器的可写层**：`docker-compose.yml` 的 `terraria-api` 少了
  `./backup` 挂载，容器一重建备份就没了。已挂载到宿主机 `backup/`。

### 兼容性说明

* 日志格式变化（新增 `[时间戳]` 前缀）对调用方**不可见**：
  API 侧的解析器与守卫进程都同时兼容「有时间戳」和「没有时间戳」两种格式，
  镜像回退到旧版本也能正常解析。恢复旧行为只需把 `start.sh` 里的 awk 去掉。
* 新增接口均为附加；`GET /api/v1/backups` 只增加了字段。

### Internal

* 测试 84 → **133**；新增 `test_scheduler.py` / `test_restore.py` /
  `test_notifications.py` / `test_guard_parsing.py`。
* `test_guard_parsing.py` 专门盯住「日志格式变化导致守卫静默失效」这一类问题
  （封禁、学习型白名单都依赖日志正则）。

---

## [1.2.0] — 2026-09-17

新增 `/api/v1` 接口面（资源导向 + 长任务 + 结构化控制台 + 配置持久化），
并把整片旧接口标记为弃用（仍然可用）。**前端可以照常运行，无需改动**；
要迁移的话照着 [v1.md](v1.md) 的对照表走即可。

### Added

* **`/api/v1` 接口面**（完整参考见 [v1.md](v1.md)）：
  * `GET  /api/v1/server` —— 一次拿全版本/端口/上限/时间/种子/MOTD/玩家/世界/配置，
    取代旧版「7 个查询型 POST + `/status` + `/world/list`」；
  * `GET  /api/v1/players` —— **带 IP 与端口**的玩家列表；
  * `POST /api/v1/players/{name}/kick`、`POST/DELETE /api/v1/players/{name}/ban`、
    `GET /api/v1/bans` —— 玩家不在线时返回 404，不再「假装成功」；
  * `POST /api/v1/broadcast`；
  * `POST /api/v1/server/actions`（save/settle）、`POST /api/v1/server/time`；
  * `POST /api/v1/server/restart` → **202 + operation_id**；
  * `GET  /api/v1/console`（**每行带 `kind`**，支持 `since` 游标增量拉取）、
    `WS /api/v1/console/stream`（JSON 事件）、
    `POST /api/v1/console/commands`（**命令白名单 + 审计**，禁止 exit）、
    `GET /api/v1/console/audit`；
  * `GET/PUT /api/v1/config` —— 明确区分「写进 serverconfig.txt」与「立即生效」；
  * `GET/POST /api/v1/worlds`、`DELETE /api/v1/worlds/{file}`、
    `POST /api/v1/worlds/{file}/activate`、`POST /api/v1/worlds/{file}/backup`、
    `GET /api/v1/backups`；
  * `GET /api/v1/operations`、`GET /api/v1/operations/{id}` —— 长任务进度。
* `GET /api/meta/usage` —— 被弃用接口的调用量（按客户端版本分组），
  用来判断「前端已经迁完」再删旧接口。
* 旧接口响应新增弃用头：`Deprecation` / `Sunset` / `Link` / `X-API-Deprecated`
  （纯附加，不改状态码与响应体）。
* 脚本 `api/scripts/live_compat_check.py`：**默认只读**的线上契约比对。

### Changed

* `PUT /api/v1/config` 写入 `serverconfig.txt` 是原子的，并保留注释与键顺序。
* `maxplayers` **小于 64 需要二次确认**（`confirm_low_max_players: true`），否则 409：
  原版会把每条陌生 TCP 连接都算进名额，8 个槽位很容易被扫描流量拖成「假满员」，
  详见 `../connection-guard.md`。
* 旧的 `POST /api/world/switch` 仍然同步返回同样的响应体，但内部改为走操作框架，
  因此**新增**一种失败可能：已有重启类操作在跑时会返回 409 `conflict`
  （以前会直接并发执行两个切换）。

### Deprecated

* 旧接口全部标记弃用，Sunset 定在 **2026-11-16**，替代品见 [v1.md](v1.md) 第 8 节。
  删除前会先看 `GET /api/meta/usage` 的调用量。

### Internal

* 测试 52 → 84 个用例（新增 v1、操作互斥、配置校验、命令白名单、弃用头与埋点）。
* 修复测试隔离问题：`Settings` 夹具现在覆盖 `backup_dir`，
  避免测试往线上 `backup/` 目录写东西。

---

## [1.1.0] — 2026-09-17

后端内部重构（P0）。**URL、请求体、响应体全部保持不变**，前端无需改动。
（方案见 `docs/api-refactor-plan.md`。）

### Added

* `GET /api/meta` —— 版本握手接口。返回 `api_version`、`min_client_version`、
  `server_version`、`capabilities`、`deprecations` 与文档链接。
  面板启动时调用一次，若 `api_version` 与面板构建时预期不一致即可提示用户刷新，
  避免用户面对 404 或字段缺失。
* 所有响应的 200 schema 现在都有明确的类型（之前 OpenAPI 里是空的 `{}`），
  前端可以用 `openapi-typescript` 直接生成类型。
* 错误响应新增机器可读结构（**纯新增字段，`detail` 原样保留**）：

  ```json
  {
    "detail": "world not found: WSD.wld",
    "error": { "code": "not_found", "message": "world not found: WSD.wld" }
  }
  ```

  旧的 `{"detail": "..."}` 读法继续有效。

### Changed

* `GET /api/server/status` 现在走 3 秒状态缓存：`version/port/max_players/seed/motd`
  属于静态字段（只在服务端重启或我们改过配置时刷新），`time/players` 按 TTL 刷新。
  因此这些字段**最多可能有 3 秒延迟**（此前每次请求都实时查询，但代价是每次 7 条
  控制台命令，且日志约 65% 都是这个轮询刷出来的）。
* 控制台命令改为「单写者 + 哨兵栅栏」：写 FIFO 前后会持 `control/console.lock`，
  并在命令后追加一条哨兵命令，用固定回显 `Invalid command.` 划定本次回显的边界。
  这修掉了旧实现里「把期间任何日志都当成本次命令输出」的串台问题。
  为了不把哨兵行暴露给面板，`/api/server/console` 与 `/console/ws` 会过滤它。
* `save` / `settle` / `exit` / `say` / `kick` / `ban` / `motd` / `password` /
  `maxplayers` / `time/*` 仍是「发出即返回」的单向命令，语义与旧版一致
  （`save` 会阻塞服务端主循环若干秒，等回显只会平白超时）。

### Fixed

* `POST /api/server/password`：`{"password": ""}` 之前会被接受并把服务器密码清空，
  现在返回 **400** `bad_request`。
* `POST /api/server/command`：空命令之前返回 **500**，现在返回 **400**。
* 服务端不可用（容器重启中、FIFO 写不进去）时由 **500** 改为 **503**
  `console_unavailable`；等锁/回显超时分别是 `console_busy` / `console_timeout`。
* `POST /api/world/switch`：等待服务端重启超时由 500 改为 **504** `upstream_failed`
  （语义不变，只是状态码更准确）。

### Internal（不影响接口）

* 分层：`app/core/`（配置、异常）、`app/schemas/`（出入参模型）、
  `app/services/`（业务与基础设施）、`app/api/`（薄路由）。
  原先 461 行的 `app/routers/server.py` 拆开；`world.py` 里的 sleep 轮询下沉到
  `WorldService`；补上 `ConfigService` 的写入（原先 `world.py` 自己实现了一份）。
* `get_recent_output()` 改为只回读日志尾部至多 256KB（原先 `readlines()` 全量读入）。
* 新增 `api/tests/`（49 个用例，不需要真实服务端）+ `api/openapi.json` 快照 +
  CI 校验。

---

## [1.0.0] — 2026-09-17（重构前的基线）

记录当时实际存在并正在被面板使用的接口，作为后续差异的对照基准。

```
GET  /api/health
GET  /api/server/status
GET  /api/server/players
GET  /api/server/console
WS   /api/server/ws
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
POST /api/server/time/dawn
POST /api/server/time/noon
POST /api/server/time/dusk
POST /api/server/time/midnight
GET  /api/world/list
POST /api/world/upload
POST /api/world/switch
```

已知遗留问题（在 1.1.0 之后的新版本里处理，见 `docs/api-refactor-plan.md`）：

* `POST /api/server/{version,port,maxplayers,playing,time,seed,motd}` 这类「查询型 POST」
  既不符合语义，返回体里也不含查询结果——计划在 P1 用 `GET /api/v1/server` 取代。
* `POST /api/server/{maxplayers,motd,password,port}` 只改运行时、不写
  `serverconfig.txt`，容器重启即回退；而 `POST /api/world/switch` 会写配置文件。
  两种语义混在一起且没有文档说明——计划在 P2 用 `PUT /api/v1/config` 明确区分。
* 无应用层鉴权，写操作仅依赖 `DOCKER-USER` 的单来源 IP 允许。
