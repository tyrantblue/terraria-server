#!/bin/bash

set -e

mkdir -p /control

FIFO=/control/command.fifo
LOG=/control/output.log

rm -f "$FIFO"
mkfifo "$FIFO"

touch "$LOG"

echo "Starting Terraria server..."

# 以读写方式打开 FIFO，避免 Terraria 因为没有 writer 而阻塞
exec 3<> "$FIFO"

# Terraria 的标准输出/错误输出：先补时间戳，再落盘。
#
# 日志行格式（契约）：[YYYY-mm-dd HH:MM:SS] <原始内容>
#   * 时间戳用 TZ（见 Dockerfile / docker-compose.yml）里的时区，默认 Asia/Shanghai；
#   * 服务端的行会原样保留，包括它自己的 ": " 提示符；
#   * API 侧（app/services/console/parser.py 的 split_timestamp）与守卫进程
#     （guard/terraria-watchd.py）都能同时解析「有时间戳」和「没时间戳」两种格式，
#     所以镜像回退旧版本也不会解析错。
#   * awk 必须逐行 fflush，否则管道缓冲会让日志延迟几十行才可见。
/terraria-server/TerrariaServer.bin.x86_64 \
    -config /configs/serverconfig.txt \
    <&3 2>&1 \
  | awk '{ printf "[%s] %s\n", strftime("%Y-%m-%d %H:%M:%S"), $0; fflush() }' \
  | tee -a "$LOG"

exec 3>&-
