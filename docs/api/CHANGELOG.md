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
