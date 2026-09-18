# 前端改造指南（terraria-panel）

> **状态：迁移已完成（2026-09-19）。** 面板 1.4.0 已全量使用 `/api/v1`，后端
> **2.0.0 删除了全部旧 `/api/*` 路由**。这份文档作为历史记录与「新面板怎么写」的参考保留：
> 下面的步骤已经走完，文中提到的 `Deprecation` / `Sunset` 响应头不再存在。
> 当前接口参考见 [`v1.md`](v1.md)，变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

面向面板开发者。后端当前版本 **1.4.0**，`/api/v1` 是新的接口面，
**旧 `/api/*` 全部照常可用**（会带 `Deprecation` / `Sunset: 2026-11-16` 响应头）。

> **结论先行**：不改也能跑。下面的顺序按「收益 / 成本」排，可以分批做，
> 每批做完都可以独立上线。第 1 步（请求层）是所有后续步骤的基础。

---

## 0. 怎么知道后端变了

三件套（详见 `v1.md` 第 1 节）：

| 产物 | 用途 |
| --- | --- |
| `GET /api/meta` | 运行时握手：`api_version`、`min_client_version`、`deprecations`、`capabilities` |
| `api/openapi.json`（仓库里） | 生成 TS 类型；字段删改会在**编译期**报错 |
| `docs/api/CHANGELOG.md` | 人读的变更说明 + 迁移示例 + Sunset 日期 |

面板启动时调一次 `/api/meta`：`api_version` 与面板构建时预期不符，
或者 `min_client_version` 比面板版本新，就直接在界面上提示「面板版本过旧，请刷新/更新」，
而不是让用户面对一堆字段缺失。

---

## 1. 请求层（建议先做，一次到位）

```ts
// src/api/client.ts
const BASE = "/api/v1";                       // 旧代码里的 "/api" → 换成 "/api/v1"
export const CLIENT_VERSION = "1.4.0";        // 面板自身的版本，后端用它统计旧接口调用量

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
              public details?: unknown) { super(message); }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Client-Version": CLIENT_VERSION,     // ← 后端据此判断旧接口还有谁在调
      ...(init.headers ?? {}),
    },
  });
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    // 统一错误体：{ detail, error: { code, message, details } }
    const err = (body as any)?.error ?? {};
    throw new ApiError(res.status, err.code ?? "unknown",
                       err.message ?? res.statusText, err.details);
  }
  return body as T;
}

// 启动握手
export async function checkCompatibility() {
  const meta = await api<any>("/meta".replace("/meta", "../meta")); // 或直接 fetch("/api/meta")
  const expected = CLIENT_VERSION;
  if (meta.min_client_version > expected) {
    return { ok: false, message: "面板版本过旧，请刷新页面或更新面板" };
  }
  return { ok: true, deprecations: meta.deprecations ?? [] };
}
```

类型生成（任选其一）：

```bash
# 从线上实例
npx openapi-typescript http://<VPS>:8080/openapi.json -o src/api/schema.d.ts
# 或从仓库（推荐，CI 里做，不依赖线上）
npx openapi-typescript ../terraria-server/api/openapi.json -o src/api/schema.d.ts
```

要点：

* **成功响应不再包 `{"success": true}`**，v1 直接返回资源本体。
* 错误体新增 `error.code`，`detail` 字段保留（旧代码读 `detail` 不会坏）。
* `error.code` 取值：`bad_request` `not_found` `conflict` `forbidden`
  `validation_failed` `upstream_failed` `console_unavailable` `console_busy`
  `console_timeout` `guard_unavailable` `internal_error`。

---

## 2. 端点对照表（旧 → 新）

| 旧 | 新 | 变化 |
| --- | --- | --- |
| `GET /api/server/status` | `GET /api/v1/server` | 一次拿全（含世界与配置）；`time`/`players` 最多 3 秒缓存 |
| `GET /api/server/players` | `GET /api/v1/players` | **多了 `ip` / `port`** |
| `POST /api/server/{playing,version,port}` | `GET /api/v1/server` | 查询不该用 POST，旧接口返回体里也没有值 |
| `POST /api/server/kick\|ban` | `POST /api/v1/players/{name}/kick\|ban` | 玩家不在线 → **404**（旧接口永远 200） |
| `POST /api/server/say` | `POST /api/v1/broadcast` | body 都是 `{"message": "..."}` |
| `POST /api/server/save\|settle` | `POST /api/v1/server/actions` | `{"action": "save"}` |
| `POST /api/server/time/{phase}` | `POST /api/v1/server/time` | `{"phase": "dawn"}` |
| `POST /api/server/{maxplayers,motd,password,port}` | `PUT /api/v1/config` | **会写进 serverconfig.txt**，不再重启即回退 |
| `POST /api/server/command` | `POST /api/v1/console/commands` | 命令白名单 + 审计；`exit` 一律 403 |
| `GET /api/server/console` | `GET /api/v1/console` | 每行带 `kind` / `ts`，支持 `since` 游标增量 |
| `WS /api/server/ws` | `WS /api/v1/console/stream` | 收 JSON 事件而不是裸文本行 |
| `GET /api/world/list` | `GET /api/v1/worlds` | 多了 `backup_dir` |
| `POST /api/world/upload` | `POST /api/v1/worlds` | 成功 **201** |
| `POST /api/world/switch` | `POST /api/v1/worlds/{file}/activate` | **202 + operation_id**，不再挂 30 秒 |
| — | `DELETE /api/v1/worlds/{file}` | 新：删除世界（激活中的不能删，409） |
| — | `POST /api/v1/worlds/{file}/backup`、`GET /api/v1/backups`、`POST /api/v1/backups/{name}/restore` | 新：备份与恢复 |
| — | `GET /api/v1/scheduler`、`POST /api/v1/scheduler/{name}/run` | 新：定时任务 |
| — | `GET /api/v1/guard`、`POST/DELETE /api/v1/guard/bans/*`、`POST/DELETE /api/v1/guard/allow`、`POST /api/v1/guard/reload` | 新：连接守卫 |
| — | `GET /api/v1/notifications`、`POST /api/v1/notifications/test` | 新：事件通知 |
| — | `GET /api/v1/operations`、`GET /api/v1/operations/{id}` | 新：长任务进度 |

---

## 3. 各页面的具体改法

### 3.1 概览 / 状态页

一个请求替换掉原来的 9 个：

```ts
const s = await api<ServerState>("/server");
s.version; s.port; s.max_players; s.time; s.seed; s.motd;
s.players.online; s.players.players;   // [{name, ip, port}]
s.world?.file; s.config;
```

轮询建议 5 秒一次（后端有 3 秒缓存，再快也不会更实时，只会浪费）。字段可能是 `null`
（服务端刚重启），渲染时注意空值。

### 3.2 玩家页

```ts
await api(`/players/${encodeURIComponent(name)}/kick`, { method: "POST" });
await api(`/players/${encodeURIComponent(name)}/ban`, { method: "POST" });
```

* 玩家名可能含空格/日文，**必须 `encodeURIComponent`**。
* 不在线会返回 404 `not_found`，直接展示 `error.message` 即可（比"假装成功"好）。
* 封禁名单：`GET /api/v1/bans`（`exists: false` 表示 `banlist.txt` 还没生成，
  `note` 里解释了原版没有 `unban`）；`DELETE /api/v1/players/{name}/ban` 可以移除历史封禁。

### 3.3 控制台页

```ts
// 增量拉取：把上次的 cursor 传回来
let cursor = 0;
async function pull() {
  const res = await api<ConsoleResponse>(`/console?since=${cursor}`);
  cursor = res.cursor;
  for (const line of res.lines) appendLine(line.kind, line.ts, line.text);
}
setInterval(pull, 1000);
```

* `kind`：`player_join` `player_leave` `connect` `disconnect` `boot` `chat` `world_save`
  `startup` `error` `prompt` `output` `blank` —— 直接按它上色/过滤，不用再写正则。
* `ts` 是 epoch 秒（可能为 `null`，旧日志没有时间戳）。
* 实时用 WebSocket（`/api/v1/console/stream`）：连接后先收到
  `{"type":"hello","cursor":N}`，之后每条 `{"type":"console.line","offset","ts","kind","text"}`。
  断线重连时用最后收到的 `offset` 作为 `since` 补齐，不会丢行也不会重复。
* 命令输入框走 `POST /api/v1/console/commands`；`exit` 会被 403（关服请用重启按钮）。

### 3.4 世界页

```ts
// 切换世界：立刻返回，然后轮询进度
const { operation_id } = await api(`/worlds/${file}/activate`, { method: "POST" });
const timer = setInterval(async () => {
  const op = await api<Operation>(`/operations/${operation_id}`);
  setProgress(op.progress, op.message);
  if (op.state === "succeeded" || op.state === "failed") {
    clearInterval(timer);
    if (op.state === "failed") toast.error(op.error);
  }
}, 800);
```

恢复备份同理：`POST /api/v1/backups/{name}/restore` → 202 → 轮询。
备份列表里 `kind: "auto"` 的是游戏自己写的 `.wld.bak`，`restorable: true`，
名字形如 `auto:gogogo.wld.bak`（URL 编码后传）。

### 3.5 设置页

```ts
const cfg = await api<ConfigView>("/config");     // values + editable_keys + runtime_keys + restart_keys

const res = await api<ConfigUpdate>("", {
  method: "PUT",
  body: JSON.stringify({ values: { motd: "hello" }, apply: true }),
});
// 200：只改了运行时项 → res.applied
// 202：改了需要重启的项（world/port/…）→ res.operation_id，轮询进度
```

**`maxplayers` 的二次确认**（重要，别漏）：

```ts
try {
  await putConfig({ values: { maxplayers: 8 }, apply: true });
} catch (e) {
  if (e.code === "conflict" && e.details?.reason === "phantom-full") {
    // 弹确认框：推荐值 e.details.recommended_min（64）
    // 用户确认后再发一次，带上 confirm_low_max_players: true
  }
}
```

原因：原版服务端会把每条陌生 TCP 连接都算进玩家名额，`maxplayers` 太小会被
端口扫描拖成「假满员」（见 `../connection-guard.md`）。面板应该解释这一点，
而不是让用户随手把 255 改回 8。

### 3.6 新页面（可选，但后端已经全好了）

| 页面 | 接口 | 展示建议 |
| --- | --- | --- |
| 备份 | `GET /backups`、`POST /worlds/{f}/backup`、`POST /backups/{n}/restore` | 列表 + 大小/时间 + 一键备份 + 恢复（确认框，恢复会重启两次） |
| 定时任务 | `GET /scheduler`、`POST /scheduler/{name}/run` | 三个任务卡片：下次执行时间、上次结果（`skipped` 要显示原因） |
| 连接守卫 | `GET /guard`、`/guard/bans*`、`/guard/allow*` | 白名单（区分 static/learned）、被封 IP + 剩余时间、手动封禁/解封、`available:false` 时提示守卫已停止 |
| 通知 | `GET /notifications`、`POST /notifications/test` | 显示是否启用（URL 已掩码）、最近投递结果、一个"发测试消息"按钮 |

---

## 4. 行为差异清单（容易踩）

1. **成功体不再有 `success: true`**（仅 v1；旧接口不变）。
2. **404 语义变准**：踢/封一个不在线的玩家现在会 404。
3. **状态有缓存**：`/api/v1/server` 的 `time`/`players` 最多 3 秒旧；
   静态字段（version/port/max_players/seed/motd）只在服务端重启或改过配置时刷新。
4. **长任务状态在内存里**：API 重启后 `/operations/{id}` 返回 404，
   前端当「已结束、结果未知」处理即可。
5. **`PUT /config` 的 `world` 是文件名**（`gogogo.wld`），而 `config.values` 之外
   （比如直接读文件）才是 `/worlds/gogogo.wld`。
6. **控制台行内含时间戳前缀**：`text` 是原始行（`[2026-09-17 17:07:15] : ...`），
   要干净内容就自己截掉，或者直接用 `kind` + `ts` 渲染。
7. **命令白名单**：`POST /console/commands` 只放行 18 个命令，`exit` 403。

---

## 5. 建议的迁移批次

| 批次 | 内容 | 预估 |
| --- | --- | --- |
| 1 | 请求层（baseURL / 错误体 / `X-Client-Version` / meta 握手）+ 生成 TS 类型 | 半天 |
| 2 | 概览页、玩家页（`/server`、`/players`、kick/ban） | 半天 |
| 3 | 控制台页（结构化 + `since` 增量 + WS JSON） | 1 天 |
| 4 | 世界页（列表/上传/删除 + 切换任务化 + 进度条） | 半天 |
| 5 | 设置页（`GET/PUT /config` + `maxplayers` 二次确认） | 半天 |
| 6 | 新页面：备份、定时任务、守卫、通知 | 1~2 天 |

每批做完都能独立上线（后端双跑）。**旧接口只在你确认零调用后才会删**：
用 `GET /api/meta/usage` 可以看到每个旧路由的调用次数和客户端版本，
运维侧确认「连续一段时间零调用」再删。

---

## 6. 本地怎么验证

```bash
# 交互式文档（Swagger UI），所有接口都能直接点
open http://<VPS>:8080/docs

# 快速试
curl -s http://<VPS>:8080/api/meta | jq
curl -s http://<VPS>:8080/api/v1/server | jq
curl -s "http://<VPS>:8080/api/v1/console?tail=10" | jq '.lines[0]'

# 契约快照（用来生成类型 / 对比字段）
curl -s http://<VPS>:8080/openapi.json > schema.json
```

有疑问先看 `openapi.json`：所有响应的字段和类型都在里面，比口头约定可靠。
