"""解析器回归测试：用真实日志片段。"""

from __future__ import annotations

from app.services.console import parser

PLAYING_WITH_PLAYERS = """2 players connected.
ユノの犬 (45.195.19.200:26557)
CTQ (121.33.239.89:44176)
: """

PLAYING_EMPTY = "No players connected.\n: "


def test_parse_players_empty() -> None:
    assert parser.parse_players(PLAYING_EMPTY) == []


def test_parse_players_two() -> None:
    assert parser.parse_players(PLAYING_WITH_PLAYERS) == ["ユノの犬", "CTQ"]


def test_parse_players_ignores_unrelated_lines() -> None:
    mixed = "203.0.113.9:1 is connecting...\n" + PLAYING_WITH_PLAYERS
    assert parser.parse_players(mixed) == ["ユノの犬", "CTQ"]


def test_scalar_parsers() -> None:
    assert parser.parse_version("Terraria Server v1.4.5.8\n: ") == "1.4.5.8"
    assert parser.parse_port("Port: 7777\n: ") == 7777
    assert parser.parse_max_players("Player limit: 255\n: ") == 255
    assert parser.parse_game_time("Time: 7:41 PM\n: ") == "7:41 PM"
    assert parser.parse_seed("World Seed: 3.3.1.0.1269458679\n: ") == "3.3.1.0.1269458679"
    assert parser.parse_motd("MOTD: Fly to the sky\n: ") == "Fly to the sky"


def test_scalar_parsers_return_none_on_garbage() -> None:
    assert parser.parse_port("nothing here") is None
    assert parser.parse_version("") is None
    assert parser.parse_motd("Invalid command.") is None


# ---------------------------------------------------------------- 时间戳兼容
TS = "[2026-09-17 17:01:02] "


def test_parsers_tolerate_leading_timestamp() -> None:
    """start.sh 会给每行加 [时间戳]，解析结果必须与旧格式完全一致。"""
    assert parser.parse_players(TS + PLAYING_EMPTY) == []
    assert parser.parse_players(TS + PLAYING_WITH_PLAYERS) == ["ユノの犬", "CTQ"]

    assert parser.parse_version(TS + "Terraria Server v1.4.5.8") == "1.4.5.8"
    assert parser.parse_port(TS + ": Port: 7777") == 7777
    assert parser.parse_max_players(TS + "Player limit: 255") == 255
    assert parser.parse_game_time(TS + "Time: 7:41 PM") == "7:41 PM"
    assert parser.parse_seed(TS + "World Seed: 3.3.1.0.1") == "3.3.1.0.1"
    assert parser.parse_motd(TS + "MOTD: hello") == "hello"


def test_player_entries_tolerate_leading_timestamp() -> None:
    entries = parser.parse_player_entries(TS + "CTQ (121.33.239.89:44176)")
    assert [(e.name, e.ip, e.port) for e in entries] == [("CTQ", "121.33.239.89", 44176)]


def test_split_timestamp_only_strips_the_timestamp() -> None:
    """提示符保持原样，保证重构前的解析结果逐字节不变。"""
    stamp, rest = parser.split_timestamp(TS + ": CTQ (1.2.3.4:5)")
    assert stamp is not None and rest == ": CTQ (1.2.3.4:5)"

    stamp, rest = parser.split_timestamp(": CTQ (1.2.3.4:5)")
    assert stamp is None and rest == ": CTQ (1.2.3.4:5)"


def test_split_line_strips_timestamp_and_prompt() -> None:
    stamp, body = parser.split_line(TS + ": Time: 7:41 PM")
    assert stamp is not None and body == "Time: 7:41 PM"
    assert parser.split_line(": Usage: kick <player>") == (None, "Usage: kick <player>")


def test_classify_line_with_timestamp() -> None:
    assert parser.classify_line(TS + "Alice has joined.") == "player_join"
    assert parser.classify_line(TS + ": <CTQ> hi") == "chat"
    assert parser.classify_line(TS + ": Listening on port 7777") == "startup"
    assert parser.classify_line(TS + ": ") == "prompt"


def test_fence_filter_still_matches_with_timestamp() -> None:
    assert parser.is_fence_line(TS + ": Usage: kick <player>")
