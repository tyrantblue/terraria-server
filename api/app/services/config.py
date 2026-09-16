from pathlib import Path


CONFIG_FILE = Path("/opt/terraria/config/serverconfig.txt")


def load_config() -> dict[str, str]:
    config = {}

    if not CONFIG_FILE.exists():
        return config

    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()

    return config
