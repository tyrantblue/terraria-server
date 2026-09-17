#!/usr/bin/env bash
#
# 守卫容器的入口：
#   1) 先用 terraria-guard.sh 把 DOCKER-USER 规则应用到**宿主机**（host 网络命名空间）
#   2) 再前台运行 terraria-watchd.py（自动封禁 + 假满员恢复 + 学习型白名单）
#   3) 收到 TERM/INT（docker compose stop/down）时移除自己加的规则
#
# apply 失败就直接退出，让 compose 的 restart 策略把容器拉起来并暴露在
# `docker compose ps` 里，避免“服务在跑但没有防护”这种静默状态。
#
set -uo pipefail

cd "$(dirname "$0")"

echo "[entrypoint] 应用连接守卫规则 -> 宿主机 DOCKER-USER"
if ! ./terraria-guard.sh apply; then
    echo "[entrypoint] 规则应用失败，退出（compose 会重启本容器）" >&2
    exit 1
fi

echo "[entrypoint] 启动守护进程 terraria-watchd.py"
python3 -u ./terraria-watchd.py &
watcher=$!

cleanup() {
    echo "[entrypoint] 收到退出信号：停止守护进程并移除规则"
    kill -TERM "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    ./terraria-guard.sh remove || true
}
trap cleanup TERM INT

wait "$watcher"
rc=$?

echo "[entrypoint] 守护进程退出（rc=$rc），移除规则"
./terraria-guard.sh remove || true
exit "$rc"
