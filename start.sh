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

# Terraria 的标准输出/错误输出保存下来
/terraria-server/TerrariaServer.bin.x86_64 \
    -config /configs/serverconfig.txt \
    <&3 2>&1 | tee -a "$LOG"

exec 3>&-
