#!/usr/bin/env python3
"""
terraria-watchd.py —— Terraria 服务器连接守卫守护进程

做三件事（可用 --no-ban / --no-recover / --no-learn 分别关掉）：

1) 自动封禁扫描器
   读取 /opt/terraria/control/output.log，识别“连上来又马上掉线、从未 join”的 IP，
   以及原版服务器判定为 "Invalid operation at this state." 的畸形连接。
   达到阈值后把 IP 加入 ipset(tg_ban)，由 terraria-guard.sh 的规则 DROP 掉。

2) 自动从“假满员”里恢复
   原版 Linux 服务端的已知缺陷：扫描连接会占住 maxplayers 槽位，
   于是 `playing` 显示 0 人、新玩家却收到 "This server is full right now"。
   本守护检测到该状态后，通过控制 FIFO 执行 `save` + `exit`，
   让容器 restart 策略把服务端干净地重启（世界会保存），不再需要人工重启容器。

3) 成功登录自动加白名单（学习型）
   玩家 `has joined.` 之后，等它在线满 LEARN_DWELL 秒，再用 `playing` 的输出
   精确取出该玩家的 IP，写入 learned_allow.txt 并即时加入 ipset(tg_allow)，
   于是这个 IP 不再受限流、也不会被自动封禁。
   ⚠️ 注意：tg_allow 是**完全绕过** connlimit/hashlimit 的，所以这条链路等于
   “能登录 = 可信”。请务必配合强密码；不想要这个行为就设 LEARN_ALLOW=0
   或加 --no-learn。

   安全护栏（可用环境变量调整）：
     LEARN_DWELL=60    必须在线满 60 秒才学习（0=登录即学习）
     LEARN_TTL=604800  白名单有效期 7 天（0=永久）
     LEARN_MAX=200     最多保留 200 条，超出时淘汰最早过期的

用法：
    sudo python3 terraria-watchd.py                 # 前台运行
    sudo python3 terraria-watchd.py --dry-run       # 只打印不动作
    sudo python3 terraria-watchd.py --backfill      # 先扫一遍历史日志再实时跟随
    sudo python3 terraria-watchd.py --no-learn      # 关闭自动加白名单

systemd 常驻见 guard/systemd/terraria-scan-watcher.service
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import ipaddress
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------- 默认配置
CONTROL_DIR = Path(os.environ.get("TERRARIA_CONTROL", "/opt/terraria/control"))
LOG_FILE = CONTROL_DIR / "output.log"
FIFO_FILE = CONTROL_DIR / "command.fifo"
SCRIPT_DIR = Path(__file__).resolve().parent
ALLOW_FILE = Path(os.environ.get("TERRARIA_ALLOW_FILE", SCRIPT_DIR / "allow.txt"))
LEARNED_ALLOW_FILE = Path(
    os.environ.get("TERRARIA_LEARNED_ALLOW_FILE", SCRIPT_DIR / "learned_allow.txt")
)

GUARD_CHAIN = os.environ.get("GUARD_CHAIN", "DOCKER-USER")
BAN_SET = os.environ.get("BAN_SET", "tg_ban")
ALLOW_SET = os.environ.get("ALLOW_SET", "tg_allow")
PORT = int(os.environ.get("TERRARIA_PORT", "7777"))

# 控制台协议：必须与 api/app/services/console/channel.py 保持一致
#   · control/console.lock 上的 flock 让 API 与本进程的命令互斥
#   · 哨兵命令是「不带参数的 kick」：无副作用，回显固定为 "Usage: kick <player>"，
#     用来划定本次回显的结束边界
CONSOLE_SENTINEL = os.environ.get("TERRARIA_CONSOLE_SENTINEL", "kick")
CONSOLE_FENCE = b"Usage: kick <player>"
CONSOLE_LOCK_FILE = CONTROL_DIR / "console.lock"
CONSOLE_LOCK_TIMEOUT = float(os.environ.get("TERRARIA_CONSOLE_LOCK_TIMEOUT", "5"))

# 判定参数
STRIKES = int(os.environ.get("STRIKES", "5"))            # 窗口内达到几次可疑连接就封
STRIKE_WINDOW = float(os.environ.get("STRIKE_WINDOW", "1800"))   # 秒
JOIN_GRACE = float(os.environ.get("JOIN_GRACE", "10"))   # 连接后这么久内掉线且未 join = 可疑
BAN_TIME = int(os.environ.get("BAN_TIME", "3600"))       # 封禁时长（秒）
REJOIN_SAFE = float(os.environ.get("REJOIN_SAFE", "1800"))  # 最近 join 过的 IP 在此时长内不封
MAX_BANS_PER_MIN = int(os.environ.get("MAX_BANS_PER_MIN", "30"))
ALLOW_RELOAD = float(os.environ.get("ALLOW_RELOAD", "60"))

# 成功登录后自动加入白名单（tg_allow，完全绕过 connlimit/hashlimit）
LEARN_ALLOW = os.environ.get("LEARN_ALLOW", "1").lower() not in ("0", "false", "no", "")
LEARN_DWELL = float(os.environ.get("LEARN_DWELL", "60"))    # 至少在线这么多秒才学习（0=立即）
LEARN_TTL = int(os.environ.get("LEARN_TTL", str(7 * 86400)))  # 学习条目的有效期（秒，0=永久）
LEARN_MAX = int(os.environ.get("LEARN_MAX", "200"))        # 最多保留多少条学习条目
LEARN_PRUNE_INTERVAL = float(os.environ.get("LEARN_PRUNE_INTERVAL", "300"))  # 清理周期（秒）

# 假满员自动恢复
RECOVER_THRESHOLD = int(os.environ.get("RECOVER_THRESHOLD", "5"))   # 窗口内 full 提示次数
RECOVER_WINDOW = float(os.environ.get("RECOVER_WINDOW", "600"))     # 秒
RECOVER_COOLDOWN = float(os.environ.get("RECOVER_COOLDOWN", "1800"))  # 两次自动重启最小间隔
RECOVER_SAVE_WAIT = float(os.environ.get("RECOVER_SAVE_WAIT", "25"))  # save 后等待落盘
QUERY_TIMEOUT = float(os.environ.get("QUERY_TIMEOUT", "5"))         # 读命令回显超时

# ---------------------------------------------------------------- 日志解析
#: start.sh 会给每行加 "[YYYY-mm-dd HH:MM:SS] "（见 terraria/start.sh）。
#: 这里把「时间戳 + 提示符」都做成可选前缀，新旧日志格式都能解析——
#: 否则改日志格式的那次发布会让封禁/学习功能静默失效。
LINE_PREFIX = r"^(?:\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\]\s*)?(?::\s*)?"

IP = r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})"
RE_CONNECT = re.compile(rf"{LINE_PREFIX}{IP}:(?P<port>\d+) is connecting\.\.\.\s*$")
RE_LOST = re.compile(rf"{LINE_PREFIX}{IP}:(?P<port>\d+) lost connection\.\.\.\s*$")
RE_BOOTED = re.compile(rf"{LINE_PREFIX}{IP}:(?P<port>\d+) was booted:\s*(?P<reason>.*?)\s*$")
RE_JOINED = re.compile(rf"{LINE_PREFIX}(?P<name>.+?) has joined\.\s*$")
RE_LISTENING = re.compile(r"Listening on port \d+")
# playing 输出里的玩家行：  用户名 (1.2.3.4:56789)
RE_PLAYER_LINE = re.compile(
    rf"{LINE_PREFIX}(?P<name>.+?) \((?P<ip>\d{{1,3}}(?:\.\d{{1,3}}){{3}}):\d+\)\s*$"
)

FULL_REASON = "This server is full right now"
MALFORMED_REASON = "Invalid operation at this state."
VERSION_REASON = "You are not using the same version"


def now() -> float:
    return time.monotonic()


class Clock:
    """实时模式用真实秒；--backfill 模式用行号当虚拟时间。"""

    def __init__(self, virtual: bool) -> None:
        self.virtual = virtual
        self.tick = 0.0

    def __call__(self) -> float:
        if self.virtual:
            self.tick += 1.0
            return self.tick
        return now()


def join_grace(clock: Clock) -> float:
    # backfill 时没有时间戳，用“行数”近似（默认 40 行内掉线视作可疑）
    return 40.0 if clock.virtual else JOIN_GRACE


# ---------------------------------------------------------------- 封禁动作
class Firewall:
    def __init__(self, dry_run: bool, verbose: bool) -> None:
        self.dry_run = dry_run
        self.verbose = verbose
        self.has_ipset = shutil.which("ipset") is not None
        self.has_iptables = shutil.which("iptables") is not None
        # iptables 兜底模式下的封禁到期时间
        self.rules: dict[str, float] = {}
        self.bans_this_min: deque[float] = deque()

    # -- 内部 ---------------------------------------------------------
    def _run(self, argv: list[str]) -> bool:
        if self.dry_run:
            print(f"[dry-run] {' '.join(argv)}")
            return True
        try:
            subprocess.run(argv, check=True, capture_output=True, text=True)
            return True
        except subprocess.CalledProcessError as exc:
            print(f"[warn] 命令失败: {' '.join(argv)} :: {exc.stderr.strip()}", file=sys.stderr)
            return False
        except FileNotFoundError:
            print(f"[warn] 找不到命令: {argv[0]}", file=sys.stderr)
            return False

    def _rate_ok(self) -> bool:
        t = now()
        while self.bans_this_min and t - self.bans_this_min[0] > 60:
            self.bans_this_min.popleft()
        return len(self.bans_this_min) < MAX_BANS_PER_MIN

    # -- 对外 ---------------------------------------------------------
    def ban(self, ip: str, seconds: int) -> None:
        if not self._rate_ok():
            print(f"[warn] 封禁频率超限，本轮跳过 {ip}", file=sys.stderr)
            return

        if self.has_ipset:
            ok = self._run(["ipset", "add", BAN_SET, ip, "timeout", str(seconds), "-exist"])
        elif self.has_iptables:
            ok = self._run([
                "iptables", "-I", GUARD_CHAIN, "1",
                "-p", "tcp", "--dport", str(PORT),
                "-s", ip,
                "-m", "comment", "--comment", "tg-ban",
                "-j", "DROP",
            ])
            if ok:
                self.rules[ip] = now() + seconds
        else:
            print("[warn] 既没有 ipset 也没有 iptables，无法封禁", file=sys.stderr)
            return

        if ok:
            self.bans_this_min.append(now())
            print(f"[ban] {ip} 封禁 {seconds}s")

    def allow(self, ip: str) -> bool:
        """把 IP 加进 tg_allow（立即绕过 connlimit/hashlimit，且不会被封）。"""
        if not self.has_ipset:
            print("[warn] 未安装 ipset，无法自动加白名单", file=sys.stderr)
            return False
        return self._run(["ipset", "add", ALLOW_SET, ip, "-exist"])

    def disallow(self, ip: str) -> bool:
        if not self.has_ipset:
            return False
        return self._run(["ipset", "del", ALLOW_SET, ip])

    def sweep(self) -> None:
        """iptables 兜底模式下清理过期规则。"""
        if self.has_ipset or self.dry_run:
            return
        t = now()
        expired = [ip for ip, exp in self.rules.items() if exp <= t]
        for ip in expired:
            self._run([
                "iptables", "-D", GUARD_CHAIN,
                "-p", "tcp", "--dport", str(PORT),
                "-s", ip,
                "-m", "comment", "--comment", "tg-ban",
                "-j", "DROP",
            ])
            self.rules.pop(ip, None)
            print(f"[unban] {ip} 封禁到期")


# ---------------------------------------------------------------- 控制台交互
class Console:
    def __init__(
        self,
        fifo: Path,
        log: Path,
        dry_run: bool = False,
        lock_path: Path = CONSOLE_LOCK_FILE,
    ) -> None:
        self.fifo = fifo
        self.log = log
        self.dry_run = dry_run
        self.lock_path = lock_path

    def send(self, command: str) -> bool:
        """单向命令：只写不读（save / exit / say 等）。也会拿锁，避免插到 API 的
        「命令 + 哨兵」中间去污染它的回显。"""
        if self.dry_run:
            print(f"[dry-run] fifo <- {command!r}")
            return True
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            print(f"[warn] 无法写入控制 FIFO（服务端在运行吗？）: {exc}", file=sys.stderr)
            return False
        try:
            with self._exclusive():
                os.write(fd, (command + "\n").encode())
        except TimeoutError as exc:
            print(f"[warn] {exc}", file=sys.stderr)
            return False
        finally:
            os.close(fd)
        return True

    def query(self, command: str, timeout: float = QUERY_TIMEOUT) -> str:
        """需要回显的命令：命令 + 哨兵，读到哨兵那一行就是本次回显的结束边界。

        协议与 api/app/services/console/channel.py 完全一致，两边共用
        control/console.lock，所以面板轮询和守卫查询不会互相读串。
        """
        try:
            size = self.log.stat().st_size
        except OSError:
            size = 0
        if self.dry_run:
            print(f"[dry-run] fifo <- {command!r} (+sentinel)")
            return ""
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            print(f"[warn] 无法写入控制 FIFO: {exc}", file=sys.stderr)
            return ""
        try:
            with self._exclusive():
                os.write(fd, (command + "\n" + CONSOLE_SENTINEL + "\n").encode())
                return self._read_until_fence(size, timeout)
        except TimeoutError as exc:
            print(f"[warn] {exc}", file=sys.stderr)
            return ""
        finally:
            os.close(fd)

    @contextmanager
    def _exclusive(self):
        """与 API 进程共享的 flock（基于 control/console.lock）。"""
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        acquired = False
        try:
            deadline = now() + CONSOLE_LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if now() >= deadline:
                        raise TimeoutError("等待控制台锁超时（API 正在用？）") from exc
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:  # pragma: no cover
                    pass
            os.close(fd)

    def _read_until_fence(self, start: int, timeout: float) -> str:
        deadline = now() + timeout
        data = b""
        while True:
            try:
                size = self.log.stat().st_size
            except OSError:
                size = start
            if size > start:
                try:
                    with self.log.open("rb") as fh:
                        fh.seek(start)
                        data = fh.read()
                except OSError:
                    data = b""
            index = data.rfind(CONSOLE_FENCE)
            if index != -1:
                line_start = data.rfind(b"\n", 0, index) + 1
                return data[:line_start].decode("utf-8", errors="replace")
            if now() >= deadline:
                return data.decode("utf-8", errors="replace")
            time.sleep(0.05)

    def online_players(self) -> int:
        text = self.query("playing")
        if not text:
            return -1  # 未知
        if "No players connected." in text:
            return 0
        count = sum(1 for line in text.splitlines() if RE_PLAYER_LINE.match(line))
        # 认不出玩家列表时返回「未知」而不是 0——自动恢复依赖这个判断，
        # 绝不能把「读到的内容不完整」误判成「没人在线」。
        return count if count else -1

    def players_with_ip(self) -> dict[str, str]:
        """执行 playing，返回 {玩家名: IP}（playing 的输出才是“名字 ↔ IP”的权威来源）。"""
        text = self.query("playing")
        result: dict[str, str] = {}
        for line in text.splitlines():
            m = RE_PLAYER_LINE.match(line)
            if m:
                result[m.group("name").strip()] = m.group("ip")
        return result


# ---------------------------------------------------------------- 守护主体
class Watchdog:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.clock = Clock(virtual=args.backfill)
        self.fw = Firewall(args.dry_run, args.verbose)
        self.console = Console(FIFO_FILE, LOG_FILE, args.dry_run)

        self.pending: dict[tuple[str, str], tuple[float, bool]] = {}
        self.order: deque[tuple[str, str, float]] = deque()
        self.strikes: dict[str, deque[float]] = defaultdict(deque)
        self.last_join: dict[str, float] = {}
        self.allow: set[ipaddress._BaseNetwork] = set()
        self.allow_loaded_at = 0.0
        self.full_events: deque[float] = deque()
        self.last_recover = -1e9
        self.paused_until = 0.0
        # 启动时会把已有日志补读一遍（用于重新封禁惯犯），
        # 这段“补课”期间绝不能触发 save/exit 之类的动作。
        self.warmup = True

        # 学习型白名单：成功登录并在线满 LEARN_DWELL 秒的玩家 IP
        self.learned: dict[str, int] = {}          # ip -> 过期时间戳（0=永久）
        self.learn_queue: deque[tuple[str, float]] = deque()  # (玩家名, 到期检查时间)
        self.last_prune = 0.0

    # -- 学习型白名单 -------------------------------------------------
    def load_learned(self) -> None:
        if not LEARNED_ALLOW_FILE.exists():
            return
        current = int(time.time())
        loaded = {}
        for raw in LEARNED_ALLOW_FILE.read_text(encoding="utf-8").splitlines():
            raw = raw.split("#", 1)[0].strip()
            if not raw:
                continue
            parts = raw.split()
            ip = parts[0]
            try:
                expiry = int(parts[1]) if len(parts) > 1 else 0
            except ValueError:
                expiry = 0
            if expiry and expiry <= current:
                continue
            loaded[ip] = expiry
        self.learned = loaded
        if loaded:
            print(f"[watchd] 载入学习型白名单 {len(loaded)} 条（{LEARNED_ALLOW_FILE}）")

    def write_learned(self) -> None:
        items = list(self.learned.items())
        if LEARN_MAX > 0 and len(items) > LEARN_MAX:
            # 到期时间越晚越“新”，优先淘汰最早过期的
            items.sort(key=lambda kv: kv[1] or (1 << 62))
            for ip, _ in items[: len(items) - LEARN_MAX]:
                self.learned.pop(ip, None)
                if not self.static_allowed(ip):
                    self.fw.disallow(ip)
                print(f"[learn] 超出上限，移除 {ip}")
            items = list(self.learned.items())
        lines = [
            "# terraria-watchd 自动学习到的白名单（格式：IP 过期时间戳，0=永久）",
            "# 由守护进程维护，不要手工编辑；手工白名单请写在 allow.txt",
        ]
        lines += [f"{ip} {exp}" for ip, exp in sorted(items, key=lambda kv: kv[1])]
        tmp = LEARNED_ALLOW_FILE.with_name(LEARNED_ALLOW_FILE.name + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(LEARNED_ALLOW_FILE)

    def learn_ip(self, ip: str, name: str) -> None:
        if self.static_allowed(ip):
            return
        expiry = 0 if LEARN_TTL <= 0 else int(time.time()) + LEARN_TTL
        if self.learned.get(ip) == expiry:
            return
        self.learned[ip] = expiry
        self.write_learned()
        self.fw.allow(ip)
        human = "永久" if expiry == 0 else f"{LEARN_TTL // 86400} 天"
        print(f"[learn] {ip} ({name}) 加入白名单 {ALLOW_SET}，有效期 {human}")

    def process_learn_queue(self) -> None:
        if not LEARN_ALLOW or self.args.no_learn or not self.learn_queue:
            return
        t = now()
        if self.learn_queue[0][1] > t:
            return
        due: list[str] = []
        while self.learn_queue and self.learn_queue[0][1] <= t:
            due.append(self.learn_queue.popleft()[0])
        online = self.console.players_with_ip()
        for name in due:
            ip = online.get(name)
            if ip:
                self.learn_ip(ip, name)
            else:
                print(f"[learn] {name} 未满在线时长就离开了，不加入白名单")

    def prune_learned(self, force: bool = False) -> None:
        t = now()
        if not force and t - self.last_prune < LEARN_PRUNE_INTERVAL:
            return
        self.last_prune = t
        current = int(time.time())
        expired = [ip for ip, exp in self.learned.items() if exp and exp <= current]
        if not expired:
            return
        for ip in expired:
            self.learned.pop(ip, None)
            if not self.static_allowed(ip):
                self.fw.disallow(ip)
            print(f"[learn] {ip} 白名单已过期，移除")
        self.write_learned()

    # -- 白名单 -------------------------------------------------------
    def static_allowed(self, ip: str) -> bool:
        self.load_allow()
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in self.allow)

    def load_allow(self, force: bool = False) -> None:
        t = self.clock()
        if not force and t - self.allow_loaded_at < ALLOW_RELOAD:
            return
        self.allow_loaded_at = t
        entries: set[ipaddress._BaseNetwork] = set()
        if ALLOW_FILE.exists():
            for raw in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
                raw = raw.split("#", 1)[0].strip()
                if not raw:
                    continue
                try:
                    entries.add(ipaddress.ip_network(raw, strict=False))
                except ValueError:
                    print(f"[warn] 白名单条目无效: {raw}", file=sys.stderr)
        self.allow = entries

    def allowed(self, ip: str) -> bool:
        """静态白名单或学习型白名单命中都算放行。"""
        return self.static_allowed(ip) or ip in self.learned

    # -- 可疑连接判定 -------------------------------------------------
    def add_strike(self, ip: str, reason: str) -> None:
        if self.allowed(ip):
            return
        t = self.clock()
        if t - self.last_join.get(ip, -1e9) < (1e9 if self.clock.virtual else REJOIN_SAFE):
            return  # 近期成功 join 过，放过
        bucket = self.strikes[ip]
        bucket.append(t)
        while bucket and t - bucket[0] > (1e9 if self.clock.virtual else STRIKE_WINDOW):
            bucket.popleft()
        if self.args.verbose:
            print(f"[strike] {ip} ({reason}) -> {len(bucket)}/{STRIKES}")
        if len(bucket) >= STRIKES:
            bucket.clear()
            self.fw.ban(ip, BAN_TIME)

    # -- 事件处理 -----------------------------------------------------
    def handle(self, line: str, line_no: int) -> None:
        t = self.clock()

        m = RE_CONNECT.match(line)
        if m:
            key = (m.group("ip"), m.group("port"))
            self.pending[key] = (t, False)
            self.order.append((key[0], key[1], t))
            while len(self.order) > 512:
                self.order.popleft()
            return

        m = RE_LOST.match(line)
        if m:
            key = (m.group("ip"), m.group("port"))
            entry = self.pending.pop(key, None)
            if entry is None:
                return
            t0, joined = entry
            if not joined and t - t0 <= join_grace(self.clock):
                self.add_strike(m.group("ip"), "connect-then-drop")
            return

        m = RE_BOOTED.match(line)
        if m:
            ip = m.group("ip")
            self.pending.pop((ip, m.group("port")), None)
            reason = m.group("reason")
            if MALFORMED_REASON in reason:
                self.add_strike(ip, "malformed-packet")
            elif VERSION_REASON in reason:
                self.add_strike(ip, "version-mismatch")
            elif FULL_REASON in reason:
                self.full_events.append(t)
                self.maybe_recover()
            return

        m = RE_JOINED.match(line)
        if m:
            if self.order:
                ip, _port, _t0 = self.order.pop()          # 最近一次连接就是这个玩家
                self.pending.pop((ip, _port), None)
                self.last_join[ip] = t
                self.strikes.pop(ip, None)
            # 学习型白名单：先排队，等在线满 LEARN_DWELL 秒后再用 playing 精确取 IP
            if LEARN_ALLOW and not self.args.no_learn and not self.clock.virtual:
                self.learn_queue.append((m.group("name").strip(), t + LEARN_DWELL))
            return

        if RE_LISTENING.search(line):
            print("[watchd] 检测到服务端已重新监听，恢复正常监控")
            self.full_events.clear()
            self.pending.clear()
            self.order.clear()

    # -- 假满员自动恢复 ------------------------------------------------
    def maybe_recover(self) -> None:
        if not self.args.recover:
            return
        if self.warmup:
            return  # 补读历史日志阶段绝不动作
        t = self.clock()
        if self.clock.virtual:
            return
        if t < self.paused_until:
            return
        while self.full_events and t - self.full_events[0] > RECOVER_WINDOW:
            self.full_events.popleft()
        if len(self.full_events) < RECOVER_THRESHOLD:
            return
        if t - self.last_recover < RECOVER_COOLDOWN:
            return

        online = self.console.online_players()
        if online != 0:
            print(f"[recover] 出现 {len(self.full_events)} 次“已满”，但在线玩家={online}，跳过自动重启")
            self.full_events.clear()
            return

        print(f"[recover] 假满员确认：0 人在线但连续 {len(self.full_events)} 次拒绝新连接，执行 save + exit")
        self.last_recover = t
        self.paused_until = t + 120
        self.full_events.clear()
        self.console.send("say [server] auto-recovering from phantom-full, will be back in ~30s")
        self.console.send("save")
        time.sleep(RECOVER_SAVE_WAIT)
        self.console.send("exit")

    # -- 主循环 -------------------------------------------------------
    def follow(self, backfill: bool) -> None:
        print(
            f"[watchd] 监控 {LOG_FILE}  ban={self.args.ban} recover={self.args.recover} "
            f"learn={LEARN_ALLOW and not self.args.no_learn} dry_run={self.args.dry_run}"
        )
        offset = 0
        inode = None
        line_no = 0

        self.load_learned()
        self.prune_learned(force=True)

        if backfill and LOG_FILE.exists():
            print("[watchd] --backfill：先处理历史日志（只做封禁判定）")
            self.clock.virtual = True
            with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh, 1):
                    if self.args.ban:
                        self.handle(line.rstrip("\n"), line_no)
            self.clock.virtual = False
            offset = LOG_FILE.stat().st_size
            inode = LOG_FILE.stat().st_ino
            print(f"[watchd] backfill 完成，共 {line_no} 行")

        first_pass = True
        while True:
            self.fw.sweep()
            self.load_allow()
            try:
                if not LOG_FILE.exists():
                    time.sleep(1)
                    continue
                st = LOG_FILE.stat()
                if inode is not None and (st.st_ino != inode or st.st_size < offset):
                    print("[watchd] 日志被轮转/截断，重新打开")
                    offset = 0
                inode = st.st_ino
                if st.st_size > offset:
                    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(offset)
                        for line in fh:
                            line_no += 1
                            if self.args.ban or "is connecting" in line or "full right now" in line:
                                self.handle(line.rstrip("\n"), line_no)
                        offset = fh.tell()
            except OSError as exc:
                print(f"[warn] 读取日志失败: {exc}", file=sys.stderr)
            if not self.warmup:
                self.process_learn_queue()
                self.prune_learned()
            if first_pass:
                first_pass = False
                self.warmup = False
                # 历史状态只用于“重新封禁惯犯”，不参与后续判定
                self.full_events.clear()
                self.strikes.clear()
                self.pending.clear()
                self.order.clear()
                print("[watchd] 历史日志补读完成，开始只监控新事件")
            if self.args.once:
                return
            time.sleep(0.4)


# ---------------------------------------------------------------- CLI
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Terraria 连接守卫守护进程")
    p.add_argument("--no-ban", dest="ban", action="store_false", help="关闭自动封禁")
    p.add_argument("--no-recover", dest="recover", action="store_false", help="关闭假满员自动恢复")
    p.add_argument("--no-learn", dest="no_learn", action="store_true", help="关闭“登录成功自动加白名单”")
    p.add_argument("--dry-run", action="store_true", help="只打印将要执行的动作")
    p.add_argument("--backfill", action="store_true", help="启动时先处理历史日志")
    p.add_argument("--once", action="store_true", help="处理完当前日志后退出（适合 cron/测试）")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(ban=True, recover=True, no_learn=False)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0 and not args.dry_run:
        print("需要 root 权限（ipset/iptables）", file=sys.stderr)
        return 1
    Watchdog(args).follow(args.backfill)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[watchd] 退出")
