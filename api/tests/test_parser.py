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
