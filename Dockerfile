FROM ubuntu:24.04

RUN apt-get update \
    && apt-get install -y wget unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

RUN wget -O terraria-server.zip \
        https://terraria.org/api/download/pc-dedicated-server/terraria-server-1458.zip \
    && unzip terraria-server.zip \
    && mkdir -p /terraria-server \
    && cp -r 1458/Linux/* /terraria-server/ \
    && chmod +x /terraria-server/TerrariaServer.bin.x86* \
    && rm -rf /tmp/*

WORKDIR /terraria-server

ENTRYPOINT ["/terraria-server/TerrariaServer.bin.x86_64"]