# 管理后端（FastAPI）重构方案 + 前端契约同步方案

> 目标：把 `api/` 从「一个 461 行的 `server.py` 装下 19 个端点」重构成可维护的分层结构，
> 修正不合理的接口语义，并建立一套**不靠口头通知**的前后端契约同步机制。
>
> 本文是方案，不是实施记录。实施分 4 个阶段。**P0 已于 2026-09-17 完成**，
> 实施结果与实测数据见文末「§9 P0 实施结果」。

---

## 0. 进度

| 阶段 | 状态 |
| --- | --- |
| P0 内部重构 + 契约基线 | ✅ 已完成（前端零改动，21 个旧端点响应形状逐个比对通过） |
| P1 新增 `/api/v1`（22 条路由）+ 弃用头 + 埋点 | ✅ 已完成（1.2.0，旧接口全部标记 Sunset 2026-11-16） |
| P2 配置持久化 + 世界操作任务化 + 备份 + 命令白名单 | ✅ 已完成（后端部分） |
| P2 前端迁移到 v1 | ⏳ 等前端（后端双跑中，看 `/api/meta/usage`） |
| P2 删旧接口 / P3 鉴权 | ⏳ 待做（鉴权按「只有自己在用」的原则从简） |

> 实施细节见 §10；接口参考见 [`api/v1.md`](api/v1.md)；功能缺口见
> [`roadmap.md`](roadmap.md)。

---

## 1. 现状体检（都有据可查）

### 1.1 规模与分布

```
api/app/main.py              50 行
api/app/routers/server.py   461 行   ← 19 个端点全在这里
api/app/routers/world.py    187 行   ← 里面还塞了业务编排
api/app/routers/console.py  105 行
api/app/services/terraria.py 63 行   ← 只有 FIFO 读写 + 日志读
api/app/services/config.py   23 行   ← 只有 load_config()，没有写
```

运行中实例的 OpenAPI：`openapi 3.1.0`，**23 条路径**，只有 `server` / `world` 两个 tag，
所有 `/api/server/*` 都挂在同一个 tag 下；`info.version` 是 `main.py` 里硬编码的 `1.0.0`
（和代码、契约都无关，不能用来判断兼容性）。没有 `__init__.py`（靠隐式命名空间包），
没有 `tests/`，没有 dev 依赖（无 pytest/httpx）。

### 1.2 分层缺失（你提到的那个问题）

* `server.py` 同时承担三件事：HTTP 层（路由/状态码）、业务层（FIFO 命令编排）、
  协议层（拼 Terraria 控制台命令字符串）。任何改动都要动这个文件，冲突面大。
* `world.py` 里 `time.sleep(1)`、30 秒轮询等重启、直接读写 `serverconfig.txt`
  都是业务/基础设施逻辑，却写在 router 里；切换世界的流程只能被 HTTP 复用，不能被别处复用。
* `services/config.py` 只有读没有写 → 于是 `world.py` 自己又实现了一遍读写 `serverconfig.txt`
  （`world.py:131-151`），配置写入出现两份实现。
* **完全不需要 schema 层**：23 个端点全部返回裸 `dict`，OpenAPI 里响应体是空的，
  前端拿不到任何类型信息（这直接导致后面「契约同步」无从谈起）。

### 1.3 重复与不一致

* “非空 + 禁止换行”的校验在 `say` / `kick` / `ban` / `motd` / `password` 里复制了 5 份；
  而 `password` **漏了非空校验**，`{"password": ""}` 可以走通（会把服务器密码清掉）。
* 返回体风格不统一：`{"success": true, "command": ...}`（`/say` `/kick` `/ban` `/playing` …）、
  `{"success": true, "max_players": ...}`（`/maxplayers`）、`{"success": true, "motd": ...}`（`/motd`）、
  `{"success": true}`（`/password`）、`{"online": n, "players": [...]}`（`/players`）、
  而 `/status` 里同样的东西又是 `{"players": {"online": n, "list": [...]}}`。
  同一个数据两种形状，前端只能各写一套。
* 查询类操作被做成 POST：`POST /api/server/{version,port,maxplayers,playing,time,seed,motd}`。
  这些既不是创建也不是修改，纯粹是「读」，却用 POST，且返回体不含查询结果
  （只有 `{"success": true, "command": "version"}`）——**调了也拿不到值**，只能再去 GET `/status`。
  这 7 个端点没有任何存在价值。
* 动作与参数的位置倒置：`POST /api/server/kick {"player": "X"}`。
  资源在 body 里、动作用路径表达，导致「踢不存在的玩家」也只能返回 200。

### 1.4 运行/持久语义混在一起（接口设计最容易坑前端的地方）

| 端点 | 实际效果 | 重启后 |
| --- | --- | --- |
| `POST /api/server/maxplayers` | 只发控制台命令，改**运行时** | **回退**到 `serverconfig.txt` 的值 |
| `POST /api/server/motd` | 同上 | 回退 |
| `POST /api/server/password` | 同上 | 回退 |
| `POST /api/server/port` | 同上 | 回退 |
| `POST /api/world/switch` | **会写** `serverconfig.txt` | 保持 |

同一个 API 里两种语义并存，文档里一个字都没写。而且这些命令**发完就返回 200**，
`execute()` 甚至不读回显，所以「成功」完全不可信（`server.py:47-54`）。

补充一个现在才暴露出来的矛盾：我们为了解决原版「假满员」bug，特地把 `maxplayers` 设成了 `255`
（见 `docs/connection-guard.md`）。如果面板还留着「改玩家人数」的入口，用户随手改回 8
就会让那个 bug 立刻复活。接口必须能表达「这是为规避 bug 而设的缓冲值」。

### 1.5 命令通道是当前最大的正确性隐患

`services/terraria.py` 的机制是：**记录日志文件大小 → 写 FIFO → 轮询等文件变大 → 把新增内容当作本次命令的回显**。

```python
def execute_command(command, timeout=2.0):
    previous_size = get_log_size()
    send_command(command)
    return read_new_output(previous_size, timeout)
```

问题：

1. **没有请求/响应关联**。这段时间里任何写入日志的内容都会被当成「我的输出」：别的 HTTP 请求
   的命令回显、玩家进出、扫描连接的 `is connecting...`、世界保存进度……全都会混进来。
   `/status` 一次要串行发 7 条命令，7 个窗口任何一个被污染都会解析错。
2. **现在有第二个写者**。守卫进程 `guard/terraria-watchd.py` 也会写 FIFO（学习玩家 IP 时查
   `playing`、假满员恢复时发 `save`/`exit`），而它读回显用的是**同样的 size-delta 逻辑**。
   也就是说：面板轮询 `/status` 的同时有人登录触发学习，两边就可能互相读到对方的回显。
   （我用 6 组并发 `/status` + `/players` 压测没能复现出错误结果——空服时污染内容恰好同形，
   所以这是**机制上不成立**，而不是已经稳定复现的故障；但玩家在线时概率会明显上升。）
3. `send_command` 每次 `FIFO.open("w")` 且无超时；terraria 容器正好在自动恢复重启时会阻塞/报错。
4. `/status` 串行 7 条命令，每条最多等 2s → 最坏 14s（实测空服 ~0.36s）。
5. 副作用已经量化：**日志里约 65% 的行是这套轮询刷出来的**（1870 次 `version` → 7 行/次）。
6. `get_recent_output()` 用 `readlines()[-50:]`，每次把整个日志读进内存（会一直涨）。

### 1.6 错误契约与安全

* 全部 `HTTPException(detail=str)`，前端只能拿到一个字符串，没有机器可读的 `code`；
  `ValueError("command cannot be empty")` 会被转成 500（应该是 400/422）。
* `detail=f"...{e}"`（`world.py:118`）把内部异常文本直接吐给前端。
* **无认证**：所有写操作（踢人、封禁、改密码、上传世界、`command` 直通口）只靠
  `DOCKER-USER` 里的单 IP 允许；CORS 是 `allow_origins=["*"]`。
* `POST /api/server/command` 是任意控制台命令直通，绕过全部校验；没有白名单、没有审计。
* 没有速率限制，`execute_command` 也没有并发上限。

### 1.7 长耗时操作被塞进同步请求

* `POST /api/world/switch`：`sleep(1)` + 最多 30 秒轮询，前端得一直挂着；超时返回 504，
  但世界其实可能已经切完了。
* `POST /api/world/upload`：流式写盘但**没有大小上限**、没有磁盘余量检查、同名直接 409
  不能覆盖；上传过程中服务器可能正在使用该世界文件（没有原子性、没有回滚）。

---

## 2. 目标结构

```
api/
├── app/
│   ├── main.py                 # app 工厂 / 中间件 / 异常处理器 / 版本头
│   ├── core/
│   │   ├── settings.py         # pydantic-settings：路径、开关、API_VERSION
│   │   ├── errors.py           # 领域异常 + 统一错误响应
│   │   ├── logging.py          # 结构化日志（命令审计、弃用路由埋点）
│   │   └── security.py         # Bearer token 依赖（P3）
│   ├── schemas/                # ★ 契约唯一来源（出入参 Pydantic 模型）
│   │   ├── common.py           # ErrorResponse / OkResponse / Operation
│   │   ├── server.py  players.py  console.py  world.py  meta.py
│   ├── services/
│   │   ├── console/
│   │   │   ├── channel.py      # FIFO 单写者 + flock + 哨兵栅栏（见 §3）
│   │   │   ├── log_reader.py   # 尾部读取 / 轮转感知 / 解析
│   │   │   └── parser.py       # 玩家列表、状态块等解析（集中，不散落）
│   │   ├── server_service.py   # 用例：广播、踢人、封禁、改配置、重启
│   │   ├── world_service.py    # 上传/切换/备份：原子写 + 回滚
│   │   ├── config_service.py   # serverconfig.txt 唯一读写入口
│   │   └── operations.py       # 长任务注册表（内存 + 可选落盘）
│   └── api/
│       ├── deps.py             # 依赖注入（单例 service）
│       ├── legacy.py           # 旧 /api/* → 新 service 的薄适配层 + Deprecation 头
│       └── v1/
│           ├── router.py       # 聚合 /api/v1
│           ├── system.py       # health / meta
│           ├── server.py  players.py  console.py  worlds.py  operations.py
├── scripts/export_openapi.py   # 导出 openapi.json 快照
├── tests/                      # pytest + httpx（不需要真 Terraria，见 §7）
└── openapi.json                # 契约快照（提交进仓库）
```

分层规则（写进 `api/README.md`，避免又退化）：

* **router 只做**：HTTP 语义（路径、状态码、依赖注入、调用 service、返回 schema）。不许出现
  `sleep`、文件读写、字符串拼命令、正则解析。
* **service 只做**：业务用例编排 + 领域规则。不许 import `fastapi`。
* **schema 只做**：数据形状与校验。响应必须声明 `response_model`（这样 OpenAPI 才有内容）。
* 校验用 Pydantic 声明式表达（`Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\r\n]+$")]`），
  消灭 5 份复制粘贴。

---

## 3. 命令通道重做（已实测可行）

### 3.1 发现：Terraria 控制台有确定性回显

实测（本机 FIFO）：

```
写入: playing
日志: No players connected.
      :

写入: zzz_sentinel_98765      ← 不存在的命令
日志: Invalid command.
      :
```

不存在的命令**固定输出 `Invalid command.`**，不回显命令本身。结合「一次只有一条命令在飞」的
串行化，这个固定输出就是一个可靠的**栅栏（fence）**：把哨兵命令跟在真命令后面，
读到哨兵那行 `Invalid command.` 为止，之前的内容就一定是真命令的回显。

```
写入: playing
写入: __eoc__
日志: No players connected.   :  Invalid command.   :      ← 前面是真命令回显
```

### 3.2 设计

```python
# services/console/channel.py
class ConsoleChannel:
    """进程内串行 + 跨进程 flock 的 FIFO 通道。"""
    def __init__(self, fifo: Path, log: Path, lock: Path = CONTROL_DIR / "console.lock"):
        ...
    async def run(self, command: str, timeout: float = 3.0) -> str:
        async with self._mutex:                 # 进程内：同一时刻只有一条命令
            with file_lock(self._lock):         # 跨进程：与 guard 守护进程互斥
                offset = log_size()
                write(f"{command}\n")
                write(f"{SENTINEL}\n")          # '__eoc__' 之类的不存在命令
                text = await read_until(r"^Invalid command\.$", offset, timeout)
        return strip_prompt(text.rsplit("Invalid command.", 1)[0])
```

要点：

* **单写者**：只有 `ConsoleChannel` 能写 FIFO；`guard/terraria-watchd.py` 改为使用同一把
  `flock`（同一个锁文件），它的 `playing` 查询也走「命令 + 哨兵」。
* 哨兵每命令多打 1 行日志，作为代价可接受——相比现在每次 `/status` 多打 7 行且不可靠，是净改善。
* 保留旧的 size-delta 作为**降级路径**：如果哨兵在超时内没出现（例如未来版本改了
  `Invalid command.` 文案），退化为原逻辑并打 warning，不会因为一个文案变化就整体不可用。
* `send_command` 加超时 + 明确的 `ConsoleUnavailable` 领域异常（容器在重启时前端能收到 503 而不是 500）。
* 命令层记录审计日志：`request_id / command / actor / duration / ok`。

### 3.3 顺带修掉状态查询的放大效应

引入 `StatusCollector`：后台任务按需（最多每 5s）刷新一次状态并缓存，HTTP 处理器只读缓存。

* **静态字段**（`version` / `port` / `maxplayers` / `seed` / `motd`）：内存缓存，只在启动、
  自己改过配置、或日志里检测到服务端重启时刷新。
* **变化字段**（`time` / `players`）：按 TTL 刷新（默认 5s）。
* 结果：面板轮询 10 次/分钟不再等于 70 条控制台命令 + 70 行日志，而是 ≤2 条/分钟。
* 解析逻辑全部集中到 `console/parser.py`，并配上真实日志样本的单元测试（正则不再散落在 router 里）。

---

## 4. 接口重新设计（v1）

设计原则：

1. 资源导向、名词复数；动作用子资源（`POST /players/{name}/kick`），不要 `POST /kick {player}`。
2. 读用 GET、写用 PUT/PATCH/POST/DELETE；**查询绝不用 POST**。
3. 长任务返回 `202 + operation_id`，用 `GET /operations/{id}` 查进度；不要用同步请求 sleep 30 秒。
4. 成功返回资源本体（不再包 `{"success": true}`）；失败统一 `{"error": {...}}`。
5. 明确区分**运行时**与**持久配置**：持久化只走 `PUT /config`，运行时动作走 `/server/actions`。
6. 路径版本化 `/api/v1`（简单、可调试、对 Cloudflare/Worker 缓存友好）。

### 4.1 新旧对照表

| 现有 | 建议（v1） | 说明 |
| --- | --- | --- |
| `GET /api/server/status` | `GET /api/v1/server` + `GET /api/v1/players` | 拆成资源；字段类型固定；读缓存 |
| `GET /api/server/players` | `GET /api/v1/players` | 返回 `[{name, ip, port}]`（现在只给名字，IP 丢了） |
| `GET /api/server/console` | `GET /api/v1/console?tail=200&cursor=` | 返回 `{ts, level, text}` 结构化行 + 游标（见 §4.3） |
| `WS /api/server/ws` | `WS /api/v1/console/stream` | 推 JSON 事件而不是裸文本行 |
| `POST /api/server/kick {player}` | `POST /api/v1/players/{name}/kick` | 不存在则 404；与封禁对称 |
| `POST /api/server/ban {player}` | `POST /api/v1/players/{name}/ban` | 新增 `DELETE .../ban` 与 `GET /api/v1/bans` |
| `POST /api/server/say {message}` | `POST /api/v1/broadcast {message}` | 广播是独立资源 |
| `POST /api/server/maxplayers` | `PUT /api/v1/config {max_players, persist}` | 语义明确 + 可持久化 |
| `POST /api/server/motd` | `PUT /api/v1/config {motd}` | 同上 |
| `POST /api/server/password` | `PUT /api/v1/config {password}` | 同上；禁止空值 |
| `POST /api/server/port` | `PUT /api/v1/config {port}` | 需要重启才生效 → 返回 `202 + operation` |
| `POST /api/server/{version,port,maxplayers,playing,time,seed,motd}` | **删除** | 查询型 POST，且拿不到值 |
| `POST /api/server/time/{dawn,noon,dusk,midnight}` | `POST /api/v1/server/time {phase}` | 4 → 1，phase 用枚举校验 |
| `POST /api/server/save` / `settle` | `POST /api/v1/server/actions {action:"save"\|"settle"}` | 或保留 `/server/save`；动作返回真实回执 |
| `POST /api/server/command` | `POST /api/v1/console/commands` | 白名单 + 审计 + 权限位 |
| `GET /api/world/list` | `GET /api/v1/worlds` | 复数资源 |
| `POST /api/world/upload` | `POST /api/v1/worlds`（multipart，`?overwrite=`） | 加大小上限/磁盘检查/原子落盘 |
| — | `DELETE /api/v1/worlds/{file}` | 新增（带 `?force=`） |
| `POST /api/world/switch {file}` | `POST /api/v1/worlds/{file}/activate` → `202` | 长任务异步化 |
| — | `GET /api/v1/operations/{id}`、`GET /api/v1/operations` | 新增：世界切换/重启/备份的进度 |
| — | `POST /api/v1/server/restart` → `202` | 新增：优雅 `save`+`exit`（复用守卫的恢复路径） |
| — | `POST /api/v1/worlds/{file}/backup`、`GET /api/v1/backups` | 新增：备份管理/恢复 |
| `GET /api/health` | `GET /api/v1/health` | 保留，另加 `/api/meta` |
| — | `GET /api/meta` | ★ 版本握手入口（见 §5） |

### 4.2 统一错误体

```json
{ "error": { "code": "world_not_found",
             "message": "world not found: WSD.wld",
             "details": { "file": "WSD.wld" } } }
```

* `code` 是稳定字符串（前端可 switch），`message` 面向人，`details` 可选。
* 领域异常 → 状态码映射集中在一处：`NotFound→404`、`Conflict→409`、`ValidationFailed→422`、
  `ConsoleUnavailable→503`、`UpstreamFailed→502`。
* 过渡期**同时保留 `detail` 字段**（值同 `message`），这样旧前端不会因为错误体变化而白屏。

### 4.3 控制台接口的结构化

现在 `/console` 和 WS 直接吐日志原文（含那 65% 的横幅噪声）。建议：

* 后端按行打标：`{"ts": 1758000000.0, "level": "info", "kind": "player_join|connect|boot|command_output|world_save", "text": "..."}`。
  `ts` 由后端读取时打（日志文件本身没有时间戳，这也是为什么要在 API 侧补）。
* 折叠噪声：把 `Terraria Server v... / : Port: ... / : Player limit: ...` 这类状态块识别为
  `kind=status_block`，面板默认过滤。
* WS 事件：`{"type":"console.line"|"player.join"|"player.leave"|"server.restart"|"operation.progress", "data": {...}}`，
  前端不必再自己正则解析文本。

---

## 5. 前端契约同步方案（回答「怎么让对方知道」）

先说结论：**单独做一个「版本接口」不够，手动发文档也不可靠。**
版本接口只能告诉前端「变了」，不能告诉它「变成什么样」；而口头/微信通知会丢、会记错、
会和实际代码不一致。正确做法是四件事组合：

### 5.1 权威契约：把 spec 当代码管（仓库里，机器可读）

1. `api/scripts/export_openapi.py` 把 schema 导出成 `api/openapi.json`，**提交进仓库**。
2. CI 里跑「快照对比」：如果生成结果和仓库里的快照不一致，就要求显式更新快照并附版本号变更
   （不允许「悄悄变」）。
3. 前端仓库把 `api/openapi.json` 当唯一真源，用 `openapi-typescript` 生成 `types/api.d.ts`：
   字段改名/删除/必填化会在**编译期**报错，不会等到线上白屏。

### 5.2 人类可读：`docs/api/CHANGELOG.md`

按版本分节，每条写清 `Added / Changed / Deprecated / Removed`，并**带迁移示例**：

```markdown
## [2.0.0] - 2026-10-01
### Removed
- `POST /api/server/maxplayers` → 改用 `PUT /api/v1/config { "max_players": 20 }`
  迁移：`{ "max_players": 20 }`（旧 body）→ `{ "max_players": 20, "persist": true }`
### Deprecated（将于 2026-12-01 移除）
- `GET /api/server/status` → `GET /api/v1/server`
```

同时把当前版本冻结成基线（`## [1.0.0]` = 现在的 23 个路径），这样任何后续差异都有据可查。

### 5.3 运行时握手：尽早发现版本错配（保险丝）

* 所有响应加头：`X-API-Version: 2.1.0`。
* 被弃用路由额外加：`Deprecation: true`、`Sunset: Wed, 01 Dec 2026 00:00:00 GMT`、
  `Link: </api/v1/server>; rel="successor-version"`。
* 新增 `GET /api/meta`（无需认证）：

```json
{
  "api_version": "2.1.0",
  "min_client_version": "2.0.0",
  "server_version": "1.4.5.8",
  "capabilities": ["players.ip", "config.persist", "operations"],
  "deprecations": [
    { "path": "/api/server/status", "sunset": "2026-12-01", "replacement": "/api/v1/server" }
  ],
  "links": { "openapi": "/openapi.json", "changelog": "docs/api/CHANGELOG.md 的 URL" }
}
```

* 前端每次加载时先请求 `/api/meta`（并带上自己的 `X-Client-Version`）：
  * 版本兼容 → 正常；
  * 后端 major 更高或 `min_client_version` 更高 → 面板顶部直接显示「面板版本过旧，请刷新/更新」，
    而不是让用户面对一堆 404/字段缺失。**这对 Cloudflare 上缓存了旧 bundle 的 SPA 特别重要。**

### 5.4 安全删除：双跑 + 埋点（保证删得掉、删得对）

1. **双跑**：`/api/v1/*` 与旧 `/api/*` 同时在线；旧路由只是薄适配层，调用同一套 service，
   所以修 bug 只需修一处。
2. **埋点**：记录每个被弃用路由的 `调用次数 + X-Client-Version + 来源 IP`，
   暴露成 `GET /api/meta/usage`（或写进结构化日志）。
   **用数据判断「前端已经迁完」**，而不是凭感觉定日期。
3. **删除只在大版本做**，且提前一个发布周期在 CHANGELOG 里写 Sunset 日期。
4. 在 FastAPI 里用一张集中登记表声明弃用，避免「哪条废弃了」散落各处：

```python
DEPRECATIONS = {
    "/api/server/status": Deprecation(since="2.0.0", sunset="2026-12-01",
                                      replacement="/api/v1/server"),
    ...
}
```

### 5.5 通知渠道（Push）

* 仓库打 tag：`api-v2.0.0`；Release 正文贴 CHANGELOG 对应小节。
* 在 `terraria-panel` 仓库开一个 issue/PR，标题 `API v2 迁移`，正文引用具体章节和对照表。
* 微信/口头只作为「提醒去看 CHANGELOG」，不作为唯一来源。

> 一句话：**OpenAPI 快照 + CHANGELOG（仓库，权威） + `/api/meta` 握手（运行时保险丝）
> + 弃用双跑与埋点（安全删除）**。你手动发给对方可以作为提醒，但不要让它承担契约的角色。

---

## 6. 分阶段实施

### P0 — 纯内部重构，前端零改动（建议先做）

目标：结构变好、修掉隐患，但**所有 URL、请求体、响应体保持不变**。

* 拆 `core/ errors/ schemas/ services/`，把 `server.py` 的业务搬进 `server_service.py`。
* 引入 `ConsoleChannel`（单写者 + flock + 哨兵栅栏），`guard/terraria-watchd.py` 共用同一把锁。
* `StatusCollector` + 缓存；`get_recent_output` 改尾部读取；删掉 `world.py` 里的 `sleep` 轮询，
  换成 service 里的显式状态机（行为不变）。
* 补上 `password` 的非空校验、修掉 500/400 语义、错误体加 `code`（同时保留 `detail`）。
* 加 `response_model`（响应内容不变，只是 OpenAPI 从此有内容）。
* 导出 `openapi.json` 基线 + 建立 `docs/api/CHANGELOG.md`（`1.0.0` = 现状）。
* 加 `tests/`（用假的 FIFO/日志文件，不依赖真 Terraria）+ CI。

**前端影响：零。** 但从此每次变更都有 spec diff 可看。

### P1 — 只增不改（前端可并行迁移）

* 新增 `/api/v1/*`、`/api/meta`、`/api/v1/operations/*`、`/api/v1/bans`、
  `DELETE /api/v1/worlds/{file}`、`POST /api/v1/server/restart`。
* 旧路由加 `Deprecation`/`Sunset` 头并开始埋点。
* `/api/v1/console` 与 WS 改成结构化事件（旧接口继续吐原文）。

### P2 — 需要前端配合

* 前端切换到 v1 后，把旧路由标记为待删除。
* 配置持久化语义落地：`PUT /api/v1/config`（默认 `persist=true`，需重启的字段返回 `202`）。
* 世界切换/上传/备份全部任务化（`202 + operation`）。
* 面板上的 `maxplayers` 要显示「当前为 255：为规避原版假满员 bug 的缓冲值」的说明，
  并在改成 ≤64 时二次确认（否则守卫的缓解效果会被抵消）。

### P3 — 大版本

* 上 Bearer token（面板存 token）+ 审计日志 + 速率限制；CORS 收窄到面板域名。
* 删除 `/api/*` 旧路由，`info.version` 升到 `2.0.0`。

---

## 7. 测试与 CI

* `tests/`：pytest + `httpx.ASGITransport`，不启真服务。
  * **假 FIFO/日志 fixture**：用临时目录里的 FIFO + 一个「假 Terraria」线程按真实格式回写
    （包括 `Invalid command.` 哨兵），即可测命令通道的正确性与超时。
  * 用真实日志片段做解析测试（玩家列表、状态块、`is connecting/lost connection`）。
* `tests/test_contract.py`：把 `app.openapi()` 与 `api/openapi.json` 比对，
  若出现**破坏性变更**（删路径、删字段、改类型、可选变必填）而 `API_VERSION` 没升 major → 测试失败。
* CI：`ruff`（lint/format）+ `pytest` + `export_openapi --check`（快照未更新就失败）。
  这样「接口变了但没更新文档」在 CI 就被拦住，不再依赖人的自觉。

---

## 8. 需要你决策的点

1. **P0 是否现在做？**（前端零改动，纯内部重构 + 建立契约基线；风险低、收益立刻体现在可维护性上）
2. **版本化方式**：`/api/v1` 路径版本（推荐，简单可调试）还是 `Accept` 头版本？
3. **鉴权**：P3 用 Bearer token（面板存 localStorage）？还是继续只靠来源 IP 白名单？
   （建议前者，顺便把 CORS 收窄。）
4. **配置持久化**：`PUT /config` 是否允许改 `port`/`world` 这类需要重启的字段？
   我倾向允许但返回 `202 + operation`，并且 `maxplayers` 改成需要二次确认。
5. **旧接口保留多久**：建议 `Sunset` 定在「埋点显示旧路由连续 14 天零调用」之后，而不是拍一个日期。

---

## 9. P0 实施结果（2026-09-17）

### 9.1 结构

```
api/
├── app/
│   ├── main.py                     # app 工厂 / 统一异常处理 / 路由注册（77 行）
│   ├── core/{settings,errors}.py   # 配置、领域异常 + 错误体
│   ├── schemas/{common,server,world}.py   # 出入参模型（response_model 的来源）
│   ├── services/
│   │   ├── console/{channel,log_reader,parser}.py   # FIFO 通道 / 日志读取 / 回显解析
│   │   ├── config_service.py       # serverconfig.txt 唯一读写入口（含原子写）
│   │   ├── status.py               # 带 TTL 的状态缓存 + 重启探测
│   │   ├── server_service.py       # 用例：广播/踢人/封禁/改配置/时间
│   │   ├── world_service.py        # 上传/切换（sleep 轮询从 router 下沉到这里）
│   │   └── runtime.py              # 进程级单例装配（测试可整体替换）
│   └── api/{deps,system,server,world,console}.py    # 薄路由层
├── scripts/export_openapi.py       # 契约快照导出（--check 供 CI）
├── tests/                          # 52 个用例 + 假 Terraria + 兼容性基线
├── openapi.json                    # 契约快照（提交进仓库）
├── pyproject.toml / uv.lock        # dev 依赖组（pytest/httpx）+ 锁文件
└── pytest.ini
```

原 `app/routers/server.py`（461 行，19 个端点）拆开后，路由层只做 HTTP；
业务与解析各归其位。`app/routers/` 与 `app/services/terraria.py`、`config.py` 已删除。

### 9.2 命令通道：哨兵从「无效命令」换成「裸 kick」

计划里原本打算用一条不存在的命令（回显 `Invalid command.`）当哨兵。实现时发现
**不带参数的 `kick` 更好**：它同样是无效调用（无副作用），回显固定为
`Usage: kick <player>`。好处是这个字符串可以**按文本**识别，于是：

* 控制台视图不依赖「记住自己写过哪些字节偏移」，守卫进程产生的哨兵行、
  以及 API 重启前留在日志里的旧行都能一起过滤掉；
* 不需要共享的噪音记录文件；
* 面板里不会再出现成片的 `Invalid command.`。

实测：`/api/server/console` 里新哨兵泄漏 0 行。

### 9.3 实测数据

| 指标 | 旧实现 | P0 之后 |
| --- | --- | --- |
| 单次 `/api/server/status` 的控制台命令数 | 7 条（每次请求） | 静态 5 条/5 分钟；动态 2 条/3 秒（缓存命中时为 0） |
| 10 次 `/status`、间隔 3 秒产生的日志行数 | ≈70 行 | **30 行** |
| 并发时读到别条命令回显 | 会（size-delta 机制） | 不会（flock + 哨兵边界，含跨进程用例） |
| 单条命令回显是否可能被截断 | 会（一有新增就返回） | 不会（读到哨兵才算完整） |
| `/api/server/console` 内存占用 | `readlines()` 全量读入 | 只回读尾部至多 256KB |

### 9.4 兼容性验证

* 重构前先对运行中的旧实现逐个调用 21 个端点，把「结构 + 类型」存成
  `api/tests/golden/legacy_shapes.json`；
* 重构后同一批请求逐个比对：**全部一致**（`tests/test_legacy_contract.py` 在 CI 里持续守着）；
* 线上部署后再次比对：21 个端点全部一致；`/api/meta` 正常返回 `api_version=1.1.0`。

### 9.5 实施中发现并修掉的问题

1. 我第一版给 `status.py` 的 `_watch_log` 忘了同步 `LogReader.read_lines` 的新签名
   （已不再返回偏移），单次调用的测试覆盖不到——**第一次 `/status` 只初始化游标，
   第二次才会扫描新增行**。已修，并补了 `tests/test_status_cache.py` 专门覆盖重复轮询。
2. 守卫脚本原本用 size-delta 读 `playing`；已改为同一把 `console.lock` +
   同一哨兵协议，并把 `online_players()` 收紧为「认不出来就返回未知」，
   避免自动恢复把「读到一半」误判成「没人在线」。
3. 旧实现允许 `password` 传空字符串（会把密码清空），P0 顺手补了校验。

### 9.6 P0 之后仍然存在（留给后续阶段）

* 查询型 POST（`/api/server/{version,port,maxplayers,playing,time,seed,motd}`）仍在，
  计划 P1 用 `GET /api/v1/server` 取代；
* 运行时 vs 持久配置的语义仍然混在一起（`maxplayers/motd/password/port` 重启即回退），
  计划 P2 用 `PUT /api/v1/config` 明确区分；
* 无应用层鉴权（按「只有自己在用」的原则，只在来源 IP 白名单之上保持简单）。


---

## 10. P1 / P2 实施结果（2026-09-17，1.2.0）

### 10.1 交付内容

| 领域 | 接口 |
| --- | --- |
| 状态 | `GET /api/v1/server`（一次拿全，含带 IP 的玩家列表与世界/配置） |
| 玩家 | `GET /api/v1/players`、`POST /api/v1/players/{name}/kick`、`POST`/`DELETE .../ban`、`GET /api/v1/bans`、`POST /api/v1/broadcast` |
| 控制 | `POST /api/v1/server/actions`、`POST /api/v1/server/time`、`POST /api/v1/server/restart` |
| 控制台 | `GET /api/v1/console`（逐行 `kind` + `since` 游标）、`WS /api/v1/console/stream`、`POST /api/v1/console/commands`（白名单+审计）、`GET /api/v1/console/audit` |
| 配置 | `GET`/`PUT /api/v1/config`（原子持久化，runtime/restart 键区分） |
| 世界 | `GET`/`POST /api/v1/worlds`、`DELETE /api/v1/worlds/{file}`、`.../activate`、`.../backup`、`GET /api/v1/backups` |
| 长任务 | `GET /api/v1/operations`、`GET /api/v1/operations/{id}` |
| 契约 | `GET /api/meta`（带 deprecations）、`GET /api/meta/usage` |

### 10.2 几个刻意的取舍

* **旧接口一个都没删**，只是加了 `Deprecation`/`Sunset`/`Link` 头和调用量埋点；
  删之前先看 `/api/meta/usage`（按 `X-Client-Version` 分组）。
* **踢人/封禁在线校验**：原版 `kick` 对不在线的名字也会返回成功，容易误导；
  v1 先查 `playing`，不在线就 404。旧接口保持原样以免破坏前端。
* **命令白名单**：v1 只放行 18 个命令，`exit`/`exit-nosave` 一律 403
  （关服必须走 `server/restart`，它会保存并等待恢复）。旧接口仍是任意命令直通。
* **`maxplayers < 64` 需要二次确认**：这是把「假满员」事故（见 `connection-guard.md`）
  固化成接口约束，而不是靠文档提醒。
* **`POST /api/world/switch` 保持同步**：虽然内部改成了操作框架，但旧响应体必须不变，
  所以旧路由会等操作结束再返回（新增了 409 冲突这一种失败可能，已写进 CHANGELOG）。
* **操作状态只在内存里**：单进程面板够用；重启后 404 即视为「结果未知」。

### 10.3 测试

`api/tests/` 84 个用例（P0 时 52），新增覆盖：v1 全部资源、操作互斥与进度、
配置校验（含 `maxplayers` 二次确认、world 路径穿越）、控制台 `kind` 分类与游标、
命令白名单与审计、弃用头与埋点。

另外新增 `api/scripts/live_compat_check.py`：**默认只读**的线上比对（起因是曾经用
`POST /api/server/motd` 探测把线上 MOTD 改成了 "m"，写接口现在一律交给假 Terraria 测）。
