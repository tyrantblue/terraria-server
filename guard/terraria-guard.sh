#!/usr/bin/env bash
#
# terraria-guard.sh —— 在网络层削弱“陌生 IP 占满玩家槽位”的问题
#
# 背景：Terraria 原版专用服务器（Linux）存在一个已知缺陷：
#   任何一条到达 7777 端口的 TCP 连接（包括扫描器、Censys/Rapid7/ShoDAN 之类的
#   探测器）都会被计入 maxplayers，并且连接断开后槽位常常不会归还。
#   结果就是面板显示“No players connected.”，但新玩家一律收到
#   “This server is full right now”。原版唯一解法是重启进程。
#
# 这个脚本不修改游戏逻辑，只在 Docker 的 DOCKER-USER 链里加规则，
# 让“根本不是游戏客户端”的连接尽量进不到容器里：
#
#   1) 封禁集合  tg_ban    —— 由 terraria-watchd.py 动态填充，命中即 DROP
#   2) 白名单    tg_allow  —— 直接放行，不受下面的限流影响。来源有两个：
#                             · allow.txt          手工维护（自己人、Docker 内部网段）
#                             · learned_allow.txt  watchd 自动学习（成功登录并在线满
#                                                  LEARN_DWELL 秒的玩家 IP，带 TTL）
#   3) 单 IP 并发连接数上限（connlimit）—— 一个 IP 最多同时开 N 条连接
#   4) 单 IP 新建连接速率上限（hashlimit）—— 一分钟最多新建 R 条连接
#   5) 可选：ALLOWLIST_ONLY=1 时变成“只允许白名单”，最严格
#
# 用法：
#   sudo ./terraria-guard.sh apply     # 应用（幂等）
#   sudo ./terraria-guard.sh status    # 查看当前规则与集合
#   sudo ./terraria-guard.sh remove    # 移除本脚本加的规则
#
# 可用环境变量覆盖（见下方默认值）：
#   TERRARIA_PORT / MAX_CONN_PER_IP / NEW_CONN_RATE / NEW_CONN_BURST
#   ALLOWLIST_ONLY / ALLOW_FILE / LEARNED_ALLOW_FILE / BAN_TIMEOUT
#
set -euo pipefail

PORT="${TERRARIA_PORT:-7777}"
GUARD_CHAIN="${GUARD_CHAIN:-DOCKER-USER}"
MARK="terraria-guard"

MAX_CONN_PER_IP="${MAX_CONN_PER_IP:-4}"        # 单 IP 同时连接数上限
NEW_CONN_RATE="${NEW_CONN_RATE:-10/min}"       # 单 IP 新建连接速率上限
NEW_CONN_BURST="${NEW_CONN_BURST:-10}"         # 允许的突发量
ALLOWLIST_ONLY="${ALLOWLIST_ONLY:-0}"          # 1 = 只放行白名单
BAN_TIMEOUT="${BAN_TIMEOUT:-86400}"            # 动态封禁默认时长（秒）

BAN_SET="${BAN_SET:-tg_ban}"
ALLOW_SET="${ALLOW_SET:-tg_allow}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLOW_FILE="${ALLOW_FILE:-${SCRIPT_DIR}/allow.txt}"
# terraria-watchd.py 自动维护的“学习型白名单”（每行：IP 过期时间戳，0=永久）
LEARNED_ALLOW_FILE="${LEARNED_ALLOW_FILE:-${SCRIPT_DIR}/learned_allow.txt}"

IPT="iptables"

log()  { printf '[guard] %s\n' "$*"; }
warn() { printf '[guard][warn] %s\n' "$*" >&2; }
die()  { printf '[guard][error] %s\n' "$*" >&2; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "需要 root 权限运行（sudo $0 $*）"
}

have_ipset() {
    command -v ipset >/dev/null 2>&1
}

chain_exists() {
    "$IPT" -n -L "$GUARD_CHAIN" >/dev/null 2>&1
}

# 等待 Docker 建好 DOCKER-USER（开机时 Docker 可能还没起来）
wait_chain() {
    local i
    for i in $(seq 1 30); do
        chain_exists && return 0
        sleep 2
    done
    return 1
}

# 删除本脚本以前加的规则（靠 comment 标记识别）
del_marked() {
    local spec
    while IFS= read -r spec; do
        [ -n "$spec" ] || continue
        # iptables -S 输出形如：-A DOCKER-USER -p tcp ... -m comment --comment terraria-guard -j DROP
        # shellcheck disable=SC2086
        $IPT -D ${spec#-A } 2>/dev/null || true
    done < <($IPT -S "$GUARD_CHAIN" 2>/dev/null | grep -F -- "--comment $MARK" || true)
}

add_allow_entry() {
    local entry="$1"
    [ -n "$entry" ] || return 1
    if ipset add "$ALLOW_SET" "$entry" -exist 2>/dev/null; then
        return 0
    fi
    warn "白名单条目无效，已忽略：$entry"
    return 1
}

load_allow() {
    ipset flush "$ALLOW_SET"

    # 1) 手工维护的白名单
    local count=0 entry
    if [ -f "$ALLOW_FILE" ]; then
        while IFS= read -r entry; do
            entry="${entry%%#*}"
            entry="$(echo "$entry" | tr -d '[:space:]')"
            if add_allow_entry "$entry"; then
                count=$((count + 1))
            fi
        done < "$ALLOW_FILE"
        log "静态白名单载入 $count 条（$ALLOW_FILE）"
    else
        warn "白名单文件不存在：$ALLOW_FILE（跳过）"
    fi

    # 2) terraria-watchd.py 学习到的白名单（第二列是过期时间戳，0/缺省=永久）
    #    注意：这里必须容错——learned 文件损坏时不能让 apply 半途退出，
    #    否则规则已被 del_marked 删掉、端口就没有保护了。
    local learned=0 expired=0
    if [ -f "$LEARNED_ALLOW_FILE" ]; then
        local now_ts
        now_ts="$(date +%s)"
        expired="$(awk -v now="$now_ts" '
            /^[[:space:]]*#/ { next }
            NF == 0 { next }
            { ex = $2 + 0; if (ex != 0 && ex <= now) c++ }
            END { print c + 0 }
        ' "$LEARNED_ALLOW_FILE" 2>/dev/null || echo 0)"
        while IFS= read -r entry; do
            if add_allow_entry "$entry"; then
                learned=$((learned + 1))
            fi
        done < <(awk -v now="$now_ts" '
            /^[[:space:]]*#/ { next }
            NF == 0 { next }
            { ex = $2 + 0; if (ex == 0 || ex > now) print $1 }
        ' "$LEARNED_ALLOW_FILE" 2>/dev/null || true)
        log "学习型白名单载入 $learned 条（已跳过过期 ${expired:-0} 条，$LEARNED_ALLOW_FILE）"
    fi
}

apply_sets() {
    ipset create "$BAN_SET"   hash:ip  timeout "$BAN_TIMEOUT" -exist
    ipset create "$ALLOW_SET" hash:net -exist
    load_allow
}

apply() {
    wait_chain || die "找不到 ${GUARD_CHAIN} 链，Docker 是否在运行？"
    del_marked

    if have_ipset; then
        apply_sets
        # 1) 动态封禁
        $IPT -A "$GUARD_CHAIN" -p tcp --dport "$PORT" \
            -m set --match-set "$BAN_SET" src \
            -m comment --comment "$MARK" -j DROP
        # 2) 白名单直接放行（跳过下面的限流）
        $IPT -A "$GUARD_CHAIN" -p tcp --dport "$PORT" \
            -m set --match-set "$ALLOW_SET" src \
            -m comment --comment "$MARK" -j RETURN
        # 3) 只允许白名单模式
        if [ "$ALLOWLIST_ONLY" = "1" ]; then
            $IPT -A "$GUARD_CHAIN" -p tcp --dport "$PORT" \
                -m comment --comment "$MARK" -j DROP
            log "ALLOWLIST_ONLY=1：非白名单 IP 一律拒绝"
        fi
    else
        warn "未安装 ipset：跳过 动态封禁/白名单（建议 apt-get install -y ipset）"
    fi

    # 4) 单 IP 并发连接上限
    $IPT -A "$GUARD_CHAIN" -p tcp --dport "$PORT" \
        -m connlimit --connlimit-above "$MAX_CONN_PER_IP" --connlimit-mask 32 \
        -m comment --comment "$MARK" -j DROP

    # 5) 单 IP 新建连接速率上限
    $IPT -A "$GUARD_CHAIN" -p tcp --dport "$PORT" \
        -m conntrack --ctstate NEW \
        -m hashlimit --hashlimit-name tg-new \
            --hashlimit-above "$NEW_CONN_RATE" --hashlimit-burst "$NEW_CONN_BURST" \
            --hashlimit-mode srcip \
        -m comment --comment "$MARK" -j DROP

    log "已应用：port=$PORT 单IP并发<=$MAX_CONN_PER_IP 新建<=$NEW_CONN_RATE(burst $NEW_CONN_BURST)"
    log "提示：规则重启后会丢失，请启用 guard/systemd/terraria-guard.service"
}

remove() {
    chain_exists || die "找不到 ${GUARD_CHAIN} 链"
    del_marked
    if have_ipset; then
        ipset destroy "$BAN_SET"   2>/dev/null || true
        ipset destroy "$ALLOW_SET" 2>/dev/null || true
    fi
    # hashlimit 的 bucket 会残留在 /proc，无需处理
    log "已移除 terraria-guard 规则"
}

status() {
    echo "== ${GUARD_CHAIN} =="
    $IPT -S "$GUARD_CHAIN" 2>/dev/null | sed 's/^/  /'
    if have_ipset; then
        echo "== ipset $BAN_SET =="
        ipset list "$BAN_SET" 2>/dev/null | sed -n '1,8p' | sed 's/^/  /' || true
        echo "== ipset $ALLOW_SET =="
        ipset list "$ALLOW_SET" 2>/dev/null | sed -n '1,12p' | sed 's/^/  /' || true
    else
        echo "  (ipset 未安装)"
    fi
    echo "== 白名单来源 =="
    echo "  静态:   $(grep -cvE '^[[:space:]]*(#|$)' "$ALLOW_FILE" 2>/dev/null || echo 0) 条  ($ALLOW_FILE)"
    echo "  学习型: $(grep -cvE '^[[:space:]]*(#|$)' "$LEARNED_ALLOW_FILE" 2>/dev/null || echo 0) 条  ($LEARNED_ALLOW_FILE)"
}

require_root "$@"
case "${1:-}" in
    apply)  apply  ;;
    remove) remove ;;
    status) status ;;
    *)      echo "用法: $0 {apply|remove|status}"; exit 2 ;;
esac
