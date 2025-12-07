import os
import random
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import yaml


class Settings:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._find_config()
        self._config = self._load_config()

    def _find_config(self) -> str:
        paths = [
            Path("config.yaml"),
            Path("config/config.yaml"),
            Path(__file__).parent / "config.yaml",
        ]
        for p in paths:
            if p.exists():
                return str(p)
        raise FileNotFoundError("config.yaml not found")

    def _load_config(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @property
    def api_id(self) -> int:
        return int(self._config["api_id"])

    @property
    def api_hash(self) -> str:
        return self._config["api_hash"]

    @property
    def sessions_dir(self) -> Path:
        return Path(self._config.get("sessions_dir", "./sessions"))

    @property
    def tdatas_dir(self) -> Path:
        return Path(self._config.get("tdatas_dir", "./tdatas"))

    @property
    def database(self) -> str:
        return self._config.get("database", "./database.db")

    @property
    def proxies_file(self) -> Path:
        return Path(self._config.get("proxies_file", "./proxies.txt"))

    @property
    def max_reactions_per_day(self) -> int:
        return self._config.get("safety", {}).get("max_reactions_per_day", 20)

    @property
    def min_account_age_days(self) -> int:
        return self._config.get("safety", {}).get("min_account_age_days", 7)

    @property
    def delay_range(self) -> Tuple[int, int]:
        delay = self._config.get("safety", {}).get("delay_range", [5, 15])
        return tuple(delay)

    @property
    def reactions(self) -> Dict[str, str]:
        default = {
            "thumbsup": "👍",
            "heart": "❤️",
            "fire": "🔥",
            "clap": "👏",
            "party": "🎉",
            "angry": "🤬",
        }
        return self._config.get("reactions", default)

    def get_random_reaction(self) -> str:
        return random.choice(list(self.reactions.values()))

    def get_reaction_emoji(self, name: str) -> str:
        if not name:
            return self.get_random_reaction()
        return self.reactions.get(name, name)


_settings: Optional[Settings] = None


def get_settings(config_path: Optional[str] = None) -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings(config_path)
    return _settings
