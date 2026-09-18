# Terraria Server API — 变更记录

这份文件是**接口变更的权威人类可读文档**。契约同步一共三件套：

| 产物 | 作用 |
| --- | --- |
| `api/openapi.json` | 机器可读的契约快照，前端据此生成 TS 类型；CI 校验 |
| `docs/api/CHANGELOG.md`（本文件） | 人读的变更说明，带迁移示例与 Sunset 日期 |
| `GET /api/meta` | 运行时握手：版本不匹配时前端可提示用户刷新面板 |

## 版本号语义（`GET /api/meta` 的 `api_version`）

| 变更 | 含义 | 前端应该怎么做 |
| --- | --- | --- |
| **patch**（1.4.1 → 1.4.2） | **HTTP 契约没变**：内部修复、新增加定时任务、新的 webhook 事件类型、新的环境变量等 | 什么都不用做。**不要因为 patch 差异弹提示** |
| **minor**（1.4 → 1.5） | HTTP 契约有**新增**（新路由/新字段/新能力） | 用 `capabilities` 判断新功能是否可用；旧代码继续可用 |
| **major**（1.x → 2.x） | HTTP 契约有**删除/变更** | 看 `min_client_version` 与本文件的 Removed 段，必要时提示用户升级面板 |

实现建议（前端）：**只有 `compareVersions(CLIENT_VERSION, meta.min_client_version) < 0`
才是"必须升级"的硬条件**；`api_version` 只比较 major.minor 做软提示。
（面板当前是 `EXPECTED_API_VERSION = '1.4.0'` 全量比较，patch 差异也会报警，
已开 issue：tyrantblue/terrWeb#1。）

## 规则

* **破坏性变更只在 major 版本做**，并且提前一个发布周期在本文件里给出 Sunset 日期。
* 每条变更都要写清 `Added / Changed / Deprecated / Removed`，并给出 old → new 的迁移写法。
* 2.0.0 起旧接口已全部删除，`Deprecation` / `Sunset` 响应头与 `/api/meta/usage`
  埋点也一并移除；今后若要再弃用某个接口，先在本文件给出 Sunset 日期，
  并至少与替代接口并行一个发布周期。

---

## [2.1.0] — 2026-09-19

**minor：全部是新增字段/能力，现有字段语义不变；外加若干错误码与错误信封的收敛。**
对应仓库 issue #9–#18 的集中处理。

### Added

* **长任务的稳定文案与词汇表**（issue #18）：`GET /api/v1/operations/{id}` 新增
  `message_code` / `message_params` / `kind_label`（`message` 原样保留）。
  `message_code` 形如 `restart.stopping_server`，**终态也有**（`succeeded` / `failed`）。
  `/api/meta` 新增 `operation_kinds: [{kind, label}]` 与
  `exclusive_operation_kinds: [...]`，面板不必硬编码 kind → 文案映射。
* **定时任务结果结构化**（issue #10 / #18）：`GET /api/v1/scheduler` 的
  `jobs[].last_detail` 与 `history[].detail` 旁边新增 `last_code` / `code` 与
  `last_params` / `params`；`POST /api/v1/scheduler/{name}/run` 对 `restart` 类任务
  返回**顶层 `submitted: "<operation_id>"`**（不再只藏在 `detail` 字符串里）。
* **`capabilities` 补全 + 引入版本**（issue #14）：新增 `guard.state`、
  `scheduler.jobs`、`backups.list`、`notifications.status` 四项能力；
  `/api/meta` 新增 `capability_since`（能力 → 引入版本）。能力**只增不减**，
  新增能力属于 minor。
* **`GET /api/v1/operations` 新增 `in_flight`**，并支持 `?state=running` 过滤（issue #15）。
* **写操作限流**（issue #17.2）：`429 too_many_requests` 从此真正可达。
  环境变量 `TERRARIA_RATE_LIMIT_ENABLED` / `TERRARIA_RATE_LIMIT_WRITE_PER_MINUTE` /
  `TERRARIA_RATE_LIMIT_CONSOLE_PER_MINUTE` / `TERRARIA_RATE_LIMIT_RESTART_PER_MINUTE`，
  响应带 `Retry-After` 与 `error.details.{bucket,limit,window_seconds,retry_after}`。
* **可选写操作 token**（issue #17.1）：`TERRARIA_API_TOKEN` 非空时，写方法必须带
  `Authorization: Bearer <token>` 或 `X-API-Token`，否则 **401** `unauthorized`。
* **可配置 CORS 来源**（issue #17.1）：`TERRARIA_CORS_ORIGINS`（逗号分隔，默认 `*`），
  并新增 `expose_headers: X-Client-Version`（issue #17.3）。
* 新错误码：`operation_not_found`、`client_outdated`、`unauthorized`、
  `too_many_requests`。

### Changed

* **`min_client_version` 从 `1.4.0` 提到 `1.4.1`，并且服务端开始真正强制它**
  （issue #17.3）：带 `X-Client-Version` 且低于门槛时，除 `/api/meta` 与
  `/api/health` 外一律 **426** `client_outdated`。不带头（脚本/curl）不拦。
  选 `1.4.1` 的依据：它是采用 2.0 密码语义（「留空=不修改」）并给
  `PUT /api/v1/config` 关掉自动重试的面板版本（见 issue #6 / #9）。
* **`GET /api/v1/server` 的 `running` 反映真实探测结果**（issue #11）：
  以能否从控制台读到回显为准，不再是硬编码的 `true`。控制台不可用且无缓存时
  返回 **200**（`running: false`，读不到的字段为 `null`），不再是 500。
* **`OperationRef.poll` 是按请求生成的真实 URL**（issue #16.2）：
  `"poll": "/api/v1/operations/8f3c…"`，不再是未替换的模板字符串。
* **`PUT /api/v1/config` 的 apply 语义**（issue #9）：`apply=true` 时无条件对齐请求里的
  `runtime_keys`（与文件是否变化无关），所以「文件已写入、上次 apply 失败」的请求
  可以原样重试；apply 失败时 `error.details` 给出 `{persisted, applied, pending}`。
  重启类键仍是**文件变了才重启**（整份表单原样 PUT 不会触发无谓重启），
  `requires_restart = changed ∩ restart_keys`（见 `v1.md` §5）。
* **互斥集合收敛**（issue #12）：`world.restore` 加入 `EXCLUSIVE_KINDS`；
  恢复进行中会拒绝 `world.backup`（409）。`config.apply` 从未被真正提交过，
  已从集合与文档删除。
* 中间件顺序调整，`CORSMiddleware` 现在包住所有早退响应（issue #13）：
  上传预检的 413/507、限流的 429、鉴权的 401、版本门槛的 426 都带 CORS 头与
  `X-Client-Version` 回显。

### Fixed

* **`operation` 不存在改用专用错误码**（issue #15）：404 且
  `error.code = "operation_not_found"`、`error.details.operation_id`；路由不存在仍是
  `not_found`。顺带修好「路由 404 不走统一信封」：`StarletteHTTPException` 现在也
  被统一处理，不存在的路径会得到 `{detail, error:{code:"not_found"}}`。
* **上传同名世界的 409 带 `error.details.file`**（issue #16.1）。
* **pydantic 校验失败（422）走统一信封**（issue #16.3）：仍保留 `detail` 错误数组以
  兼容旧客户端，新增 `error.code = "validation_failed"` 与 `error.details.errors`。
* **守卫非法 IP 由 503 改为 400 `bad_request`**（issue #16.4），
  `error.details.ip` 是原值；只有守卫确实不可用才是 503 `guard_unavailable`。
  顺带把 IPv4 校验收紧到每段 `0..255`。
* **`banlist.txt` 的 API 侧并发写不再丢行**（issue #17.4）：临时文件改为
  `banlist.txt.<8 位 hex>.tmp`，用 `banlist.txt.lock` 上的 flock 串行化 API 侧的
  读-改-写，并在 `os.replace` 前再确认文件没变（变了就重来）。
  **注意**：游戏进程自己追加封禁时不持这把锁，与它之间仍有一个极小的竞态窗口
  （原版文件协议的固有限制）——已写进 `v1.md` §3。此处不宣称「绝不丢行」。
* 重启一个**已经退出**的服务端时给出可操作的错误（issue #11 附加项）：
  提示用 `docker compose restart terraria` 拉起，而不是裸 ENXIO。

### Docs

* `v1.md`：新增 §0「认证、CORS 与限流」；补全 §1 能力表（名称 / 含义 / 引入版本 /
  降级行为）、§2 `running` 与停服语义、§5 persisted/applied、§7 operations 保留策略与
  结构化文案、§8 `submitted` 与 `code`、§9.1 守卫 400/503 的区分。
* 明确写下语言契约：`message` / `error.message` / `detail`（字符串）是面向人的中文
  回退文案，**不保证稳定、不要用于程序判断**；程序判断用 `code` / `state` / `error.code`。

### Internal

* 新增 `app/core/version.py`（语义化版本比较）与 `app/services/ratelimit.py`
  （进程内滑动窗口限流）。
* 测试 206 → **273**：issue #9–#18 各有对应回归文件
  （`test_issue_9_config_apply.py` … `test_issue_18_message_codes.py`）。

---

## [2.0.1] — 2026-09-19

**patch：HTTP 契约没变**，只修两处实现缺陷，并补齐 2.0.0 遗漏的文档。

### Fixed

* **审计 `?tail=N` 的内存兜底顺序反了**。回读不到文件时（`audit.log` 还没建、
  落盘失败、目录只读）会退回内存缓存，但取的是**最旧**的 N 条而不是最近的 N 条：

  ```python
  list(reversed(self._entries))[-tail:]   # 旧：最旧的 N 条
  list(reversed(self._entries))[:tail]    # 新：最近的 N 条
  ```

  文件正常时不受影响；只有「审计写不进去」这种降级场景才会看到错误的历史。

* **并发上传同名世界的 `.part` 临时文件会互相覆盖**：临时名固定为
  `xxx.wld.part`，两个请求同时上传 `gogogo.wld` 会交错写同一个文件，
  先完成的那个 `os.replace` 之后，另一个必然失败（`FileNotFoundError`）或写坏内容。
  现在临时名带随机后缀（`gogogo.wld.<8 位 hex>.part`）。

### Docs

* `docs/api/v1.md`：补上 2.0.0 已上线但没写的字段——`GET /api/v1/server` 的
  `log_stalled` / `log_age`（含判定与不误报的说明）、`world.metadata` 的字段表、
  世界表的 `metadata`，以及通知事件 `log_stalled`（含 `console_stalled` 改名提示）。
* `README.md` / `README_zh-CN.md`：示例里的 `GET /api/server/status` 在 2.0.0 已删除，
  改为 `GET /api/v1/server`。
* `docs/api/frontend-migration.md`：「旧接口照常可用 / 不改也能跑」的正文与
  2.0.0 的事实矛盾，已明确标注为历史记录。
* `app/api/v1/__init__.py`、`app/services/console/log_reader.py` 里指向已删除模块与
  旧路由的注释已更新。

### Internal

* 测试 204 → **206**（审计兜底顺序、并发同名上传各一条回归）。

---

## [2.0.0] — 2026-09-19

**major：删除全部旧 `/api/*` 资源路由，并改掉了两处「读得到明文 / 读到假值」的行为。**
对应仓库 issue #1–#8 的集中处理，逐条结论见各 issue 的评论。

### 升级须知

* **面板必须是 1.4.0+**（已迁完 `/api/v1`）。`MIN_CLIENT_VERSION` 从 `1.0.0` 提到
  `1.4.0`：低于它的面板会在 `GET /api/meta` 握手上拿到明确的「必须升级」信号。
* **`GET /api/v1/config` 不再返回明文密码**（见 Changed）。面板的密码输入框要改成
  「留空 = 不修改」，不要再用返回值预填。
* 通知事件 `console_stalled` 改名为 **`log_stalled`**；如果 `NOTIFY_EVENTS` 里写死了
  旧名字，需要同步改，否则这条告警会被白名单过滤掉。

### Removed

* **旧 `/api/*` 全部删除（现在 404）**：`/api/server/*`（status、players、console、
  command、playing、version、port、maxplayers、motd、password、say、kick、ban、
  save、settle、time/*）、`/api/world/*`（list、upload、switch）、`WS /api/server/ws`。
  迁移对照表从 1.2.0 起就在本文件里，前端 1.4.0 已全量迁完。

  | 旧 | 新 |
  | --- | --- |
  | `GET /api/server/status` | `GET /api/v1/server` |
  | `GET /api/server/players` | `GET /api/v1/players` |
  | `POST /api/server/command` | `POST /api/v1/console/commands`（白名单 + 审计） |
  | `GET /api/server/console` / `WS /ws` | `GET /api/v1/console` / `WS /api/v1/console/stream` |
  | `POST /api/world/{list,upload,switch}` | `GET|POST /api/v1/worlds`、`POST /api/v1/worlds/{file}/activate` |

* `GET /api/meta/usage` 与弃用埋点中间件：它唯一的用途是「用数据判断旧接口还有没有人调」，
  旧接口删掉后没有意义。`X-Client-Version` 请求头现在只做回显（响应里原样带回）。
* `{"success": true}` 包装相关的响应模型（`SuccessResponse` / `CommandResponse`）、
  legacy 契约 golden 快照（`tests/golden/legacy_shapes.json`）与
  `scripts/live_compat_check.py`（它比对的就是那份 golden）。

### Changed

* **敏感配置不再回显明文**（issue #6）：

  ```jsonc
  // GET /api/v1/config
  { "values": { "password": "••••••" }, "password_set": true }
  // GET /api/v1/server
  { "config": { "password": "••••••" }, "password_set": true }
  ```

  未设置密码时是 `""`（不掩码），用 `password_set` 区分。新增能力
  `config.password_masked`，面板据它决定是否走「留空 = 不修改」的输入法。

  `PUT /api/v1/config` 的 `password` 语义（掩码之后必须写死，别再猜）：

  | 请求里 | 结果 |
  | --- | --- |
  | 不带 `password` | 不修改 |
  | 非空值 | 设为该密码 |
  | `""` | **清空密码**（面板的「留空即移除」） |
  | `"••••••"`（把 GET 的结果原样回传） | **400**，`details.key = "password"` |

  注意：1.x 的 `POST /api/server/password` 曾经**拒绝**空密码（怕误清空）。新接口刻意
  保留了「带空值 = 清空」这条明确语义，因为面板就是用它来移除密码的；
  「不修改」用**省略该键**表达，两者不会混。

* `GET /api/v1/server` 新增 `log_stalled` / `log_age`（issue #2），直接反映日志管道健康：
  心跳哨兵没回显、或「能读到版本但日志超过 `LOG_STALL_SECONDS`（默认 120s）没更新」
  时为 `true`；重启/切世界期间不会误报。
* `WS /api/v1/console/stream` 的历史回放帧**带上真实 `offset`**（issue #7），
  不再恒为 `-1`：回放与实时同语义，前端可以直接拿它做去重键 / React key，无需特殊分支。
  `offset >= hello.cursor` 的行留给实时循环发送（不重不漏）；`hello` 仍然**最后**发，
  解析顺序与 1.x 一致。
* `GET /api/v1/worlds` 与 `GET /api/v1/server` 里的 world 增加 `metadata`（issue #4）：
  `{format_version, size_tier, width, height, difficulty, created_at}`，解析失败为 `null`
  （接口不因此失败）。新增能力 `world.metadata`。
* `GuardState` / `GuardCounters` 的**所有字段都变成必填**（issue #8）：守卫未运行时返回
  `available: false` + `allow: []` + `banned: []` + 四项全 0 的 `counters`，客户端不用再写
  `?? []` / `?? 0`。行为与文档（`v1.md` §9.1）已对齐。
* `GET /api/v1/console/audit` 的条目新增 `result`（控制台回显，最长 500 字符），
  并支持 `?tail=N`（1..1000，从文件回读；不带参数仍读内存里的最近 200 条）。
* 上传世界失败时的状态码语义明确化：超限 **413** `payload_too_large`、
  磁盘余量不足 **507** `insufficient_storage`。
* 旧 `/api/health` 与 `/api/meta` 保持不变；`deprecations` 字段保留但恒为 `[]`
  （前端可以不再依赖它）。

### Added

* **`GET /api/v1/metrics?minutes=60`**（issue #2）：CPU%（cgroup v2/v1，按可用核数折算，
  没配额时按 `cpuset` 核数）、内存用量/上限、磁盘可用/总量、在线人数的时间序列，
  默认 1 分钟一个点、保留 1440 个点（24 小时），`METRICS_INTERVAL_SECONDS=0` 可关闭。
  新增能力 `server.metrics`。
* **审计落盘**（issue #3）：`POST /api/v1/console/commands` 的审计追加写到
  `control/audit.log`（JSON Lines：`{ts, actor, command, result}`），API 重启后仍可回查；
  失败的命令也会留痕（`result: "failed: ConsoleTimeout"` 之类）。轮转配置加在
  `ops/logrotate.terraria`（`copytruncate`，保留 12 份）。审计写不进去时只记 warning，
  不影响控制台命令本身。新增能力 `console.audit.persistent`。
* **上传世界的三道防线**（issue #3）：`Content-Length` 预检放在中间件里
  （**必须在 Starlette 把 multipart 落到临时文件之前**）、写入过程累计上限、
  每写一块检查磁盘余量（至少 2× 已写字节且不低于 64MB）；文件先写
  `xxx.wld.part` 再 `os.replace` 原子改名，任何中途失败都清理 `.part`。
  上限由 `TERRARIA_WORLD_UPLOAD_MAX_BYTES`（默认 500MB）控制。
* 新环境变量：`LOG_STALL_SECONDS`（120）、`METRICS_INTERVAL_SECONDS`（60）、
  `METRICS_RETENTION_POINTS`（1440）、`TERRARIA_WORLD_UPLOAD_MAX_BYTES`（500MB）。
* 新能力（`GET /api/meta` 的 `capabilities`）：`world.metadata`、
  `config.password_masked`、`console.audit.persistent`、`server.log_health`、
  `server.metrics`。前端用它们做功能开关。
* **仓库脱敏**（issue #1）：`guard/allow.txt` 不再纳入 Git（`.gitignore` + 只留
  `guard/allow.txt.example`），文档与测试里的真实玩家/服主 IP 全部换成 RFC 5737
  文档网段（`192.0.2.x` / `198.51.100.x` / `203.0.113.x`）。迁移时必须单独复制
  `guard/allow.txt`，README 的迁移章节已补上这一步。

### Fixed

* 通知事件名 `console_stalled` → **`log_stalled`**（issue #2 里建议的名字）。
* `LogEventWatcher` / `MetricsSampler` 把停止事件命名为 `_stop`，把
  `threading.Thread._stop()` 覆盖掉了——一旦有人调用 `is_alive()` 或 `join()`
  就会抛 `TypeError: 'Event' object is not callable`。已改名 `_stopped`，并加了回归测试
  （`test_thread_can_be_joined`）。
* `.wld` 头部解析：用文件头里的 section 指针定位 WorldHeader（不硬编码
  tile-frame-important 位数组的长度），`.NET DateTime.ToBinary()` 的 Kind 位按
  Utc/Local 分别处理，超出合理年份就丢弃而不是给个错值。

### Internal

* 测试 166 → **204**：删除 legacy 契约测试（旧接口没了），新增 issue #2/#3/#4/#6/#7/#8
  的用例（`test_metrics.py`、`test_upload_and_audit.py`、`test_world_metadata.py`，
  以及 v1 契约与 WS 回放的补充）。
* `api/openapi.json` 重新导出：旧路径消失，新增 `/api/v1/metrics`、`GuardState` 必填字段、
  `WorldMetadataView` 等 schema。CI 的 `export_openapi.py --check` 是这道闸门。

---

## [1.4.2] — 2026-09-18

HTTP 契约**没有变化**（patch）。加了一个主动探活，用来发现本轮真踩到的那类事故。

### Added

* **控制台心跳**：定时任务新增 `console`（默认每 60 秒，`SCHEDULE_CONSOLE_CHECK_SECONDS=0` 关闭）。
  它会主动往 FIFO 发一条**哨兵**（裸 `kick`，无副作用），并根据结果区分三种情况：
  * 有回显 → `ok`；
  * FIFO 写不进去（服务端正在重启）→ `unavailable`，**不告警**；
  * 写进去了但日志一直没有哨兵行 → `stalled`，说明**日志管道停更**
    （表现为"游戏能玩、面板读不到状态"），发 `console_stalled` 事件到 webhook。
  停滞告警有冷却（`CONSOLE_STALL_COOLDOWN`，默认 30 分钟），不会每分钟刷屏。

  为什么不直接看"日志多久没更新"：没人在线、面板也没开的时候，日志本来就可以安静很久，
  那样会误报；主动注入哨兵才能区分"服务端没事"和"管道停了"。
  哨兵行会被控制台视图按文本过滤掉，所以探活不会污染面板与日志。

* 任务状态照旧在 `GET /api/v1/scheduler` 里可见（`console` 任务的 `last_detail`
  会显示 `ok` / `stalled: ...` / `unavailable: ...`）。

### Fixed

* `ConsoleTimeout` 是 `ConsoleUnavailable` 的子类，心跳的异常分支顺序必须先捕获前者，
  否则"日志停更"会被误判成"服务端正在重启"而**漏报**。
* 控制台锁文件所在目录不可用时（`control/` 不存在或不可写），
  现在抛 `ConsoleUnavailable` 而不是裸 `OSError`。
* 探活走新的 `ConsoleChannel.probe()`：**哨兵没出现就一定报错**，
  不再走 `run()` 的"回退成返回部分输出"分支——否则日志只 flush 了一半
  （有回显、没有哨兵行）会被误判成正常，恰好漏掉要抓的停更。
* 重启/切世界（`EXCLUSIVE_KINDS`）在跑时跳过探活并返回 `unavailable`：
  重启窗口内日志本来就会安静，直接探活会误报一次 `console_stalled`。

### Internal

* 测试 154 → **166**（新增 `test_console_health.py`，其中包含"日志停更"的模拟：
  假服务端照收命令但不写日志；另有探活必须报错、重启中让路两个用例）。

---

## [1.4.1] — 2026-09-17

### Fixed

* **玩家名字前面多了一个冒号**（例如 `": C"`）。

  原因：Terraria 会把控制台提示符 `:` 和输出写在一起，而提示符落在**哪一行取决于
  上一条命令的输出是否以换行结束**——实测它可能粘在玩家名那一行上：

  ```
  [2026-09-17 19:12:35] : C (198.51.100.20:12811)
  [2026-09-17 19:12:35] 1 player connected.
  ```

  解析器把整行当成了名字。现在 `GET /api/v1/players`、`GET /api/server/players`、
  `GET /api/server/status` 都会剥掉行首的时间戳**与提示符**，返回 `"C"`。
  这是**后端的问题**，与前端无关。

  影响面：只有「玩家名」这一处（`version`/`port`/`motd` 等字段用的是 `re.search`，
  不受行首提示符影响）。守卫进程的正则本来就允许提示符前缀，所以它的学习型白名单
  一直是干净的——这次是补齐 API 侧，两边现在一致了。

  前端如果之前自己做了「去掉名字里的冒号」的兜底，可以删掉；**如果保留，注意
  玩家名本身可以合法地包含冒号**（例如 `a:b`），硬替换会破坏这种名字。

### Internal

* 测试用的假服务端以前把提示符写在输出**末尾**，掩盖了这个 bug；现在按线上真实格式
  （提示符在首行输出前、计数行在列表之后）生成，`test_parser.py` 增加了对应的回归用例。
* 测试 152 → **154**。

---

## [1.4.0] — 2026-09-17

两件事：**连接守卫可视化**（roadmap 第 2 项）与**飞书通知支持**。

### Added

* **`/api/v1/guard`**：把守卫进程的状态暴露给面板，并支持手动操作。
  * `GET /api/v1/guard` —— 白名单（区分 `static`/`learned`，带过期时间）、
    被封 IP 与剩余时间、计数器（累计封禁数/命令数/学习数/降级次数）、
    `available`/`stale`/`age`。守卫没在运行也返回 200（`available: false`），
    面板可以直接显示「守卫已停止」，不会因为 5xx 白屏。
  * `POST /api/v1/guard/bans` `{ip, seconds?}` / `DELETE /api/v1/guard/bans/{ip}` —— 手动封禁/解封。
  * `POST /api/v1/guard/allow` `{ip}` / `DELETE /api/v1/guard/allow/{ip}` ——
    加入白名单会**写进 `guard/allow.txt`**（重启不丢）并立即对 ipset 生效；移除会同时清静态与学习型名单。
  * `POST /api/v1/guard/reload` —— 手工改过白名单文件后重新同步 ipset。
  * 守卫没运行时下发命令返回 **503** `guard_unavailable`，不会假装成功。
* **飞书（Lark）webhook 支持**：`NOTIFY_FORMAT=feishu`（`auto` 会按 URL 自动识别）。
  飞书的 HTTP 状态码恒为 200、真正的结果在响应体的 `code` 里，所以额外解析响应体，
  避免"看起来投递成功其实没发出去"。webhook URL 建议放 `.env`（已被 gitignore），
  仓库里提供 `.env.example`。

### Changed

* `NOTIFY_WEBHOOK_URL` / `NOTIFY_FORMAT` / `NOTIFY_EVENTS` 改为从 `.env` 读取
  （`docker-compose.yml` 里是 `${...:-}`），避免密钥进 Git。

### Internal

* 守卫与 API 之间用**文件 IPC**，而不是让 API 拿 ipset/NET_ADMIN 权限：
  * `control/guard-state.json` —— 守卫写（每 5 秒 + 每次命令后）、API 读；
  * `control/guard-commands.jsonl` —— API 追加一行 JSON、守卫消费并把结果写回状态文件。
  API 是公网暴露面，不给它防火墙权限是刻意的架构选择。
* 测试 133 → **152**（新增 `test_guard_api.py` 用假守卫测 API 侧、
  `test_guard_control.py` 直接驱动真守卫测命令执行与状态发布）。
* 新增前端改造指南：[`frontend-migration.md`](frontend-migration.md)。

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
