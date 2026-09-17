# 连接守卫镜像：只提供 iptables / ipset / python3 工具链，
# 脚本本身通过 bind mount 从仓库里的 guard/ 目录挂进来（和 start.sh 的做法一致）。
#
# 关键点：容器使用 host 网络命名空间（docker-compose.yml 里的 network_mode: host）
# 并只加 NET_ADMIN/NET_RAW，因此容器里的 iptables/ipset 直接操作**宿主机**的
# netfilter；镜像里的 iptables 必须是 nft 后端，和宿主机（Ubuntu 24.04,
# iptables-nft）保持一致，否则两边会写进不同的后端。
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        iptables \
        ipset \
        python3 \
        procps \
    && rm -rf /var/lib/apt/lists/* \
    && (update-alternatives --set iptables  /usr/sbin/iptables-nft  || true) \
    && (update-alternatives --set ip6tables /usr/sbin/ip6tables-nft || true)

WORKDIR /opt/terraria/guard

ENTRYPOINT ["/opt/terraria/guard/docker-entrypoint.sh"]
