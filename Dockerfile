FROM ubuntu:24.04

RUN apt-get update \
    && apt-get install -y wget unzip ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

RUN wget -O terraria-server.zip \
        https://terraria.org/api/download/pc-dedicated-server/terraria-server-1458.zip \
    && unzip terraria-server.zip \
    && mkdir -p /terraria-server \
    && cp -r 1458/Linux/* /terraria-server/ \
    && chmod +x /terraria-server/TerrariaServer.bin.x86* \
    && rm -rf /tmp/*

# 日志时间戳（start.sh 里的 awk strftime）用这个时区；
# 没有 tzdata 时容器会忽略 TZ、全部按 UTC 输出，所以这个包是必需的。
ENV TZ=Asia/Shanghai

WORKDIR /terraria-server

ENTRYPOINT ["/terraria-server/TerrariaServer.bin.x86_64"]