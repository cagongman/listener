import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from listener.config import APP_DIR

CONFIG_PATH = (APP_DIR / "hermes.json" if getattr(sys, "frozen", False)
               else Path(__file__).resolve().parent.parent / "config.json")


@dataclass
class HermesConfig:
    backend: str = "native"
    executable: str = "hermes"
    args: list[str] = field(default_factory=lambda: ["chat"])
    cwd: str = str(APP_DIR)
    wsl_distribution: str = ""
    wsl_cwd: str = "~"
    profile: str = ""
    session_title: str = ""


@dataclass
class Config:
    wake_phrases: list[str] = field(default_factory=lambda: ["헤이 모니카", "모니카"])
    wake_aliases: dict[str, str] = field(default_factory=lambda: {"네 모니카": "헤이 모니카"})
    wake_enabled: bool = True
    command_start_timeout_seconds: float = 5.0
    max_command_seconds: float = 30.0
    cooldown_seconds: float = 3.0
    hermes: HermesConfig = field(default_factory=HermesConfig)


def load_config(path: Path = CONFIG_PATH) -> Config:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Hermes config must be a JSON object")
    hermes_data = data.pop("hermes", {})
    if not isinstance(hermes_data, dict):
        raise ValueError("hermes must be a JSON object")
    if hermes_data.pop("mode", "interactive") != "interactive":
        raise ValueError("Only interactive Hermes mode is currently supported")
    cfg = Config(**data, hermes=HermesConfig(**hermes_data))
    if not isinstance(cfg.wake_enabled, bool):
        raise ValueError("wake_enabled must be a boolean")
    if (not isinstance(cfg.wake_phrases, list) or not cfg.wake_phrases
            or any(not isinstance(p, str) or not p.strip() for p in cfg.wake_phrases)):
        raise ValueError("wake_phrases must contain non-empty strings")
    if (not isinstance(cfg.wake_aliases, dict)
            or any(not isinstance(alias, str) or not alias.strip() or canonical not in cfg.wake_phrases
                   for alias, canonical in cfg.wake_aliases.items())):
        raise ValueError("wake_aliases must map non-empty aliases to configured wake_phrases")
    for name, minimum, maximum in (("command_start_timeout_seconds", .2, 30),
                                    ("max_command_seconds", 1, 30), ("cooldown_seconds", 1, 60)):
        value = getattr(cfg, name)
        if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"Invalid {name}: expected {minimum}..{maximum}")
    h = cfg.hermes
    if h.backend not in ("native", "wsl"):
        raise ValueError("hermes.backend must be native or wsl")
    if (not isinstance(h.executable, str) or not h.executable
            or not isinstance(h.args, list) or any(not isinstance(a, str) for a in h.args)
            or not isinstance(h.cwd, str) or not h.cwd
            or not isinstance(h.wsl_distribution, str) or not isinstance(h.wsl_cwd, str)):
        raise ValueError("Invalid Hermes executable, args or working directory")
    if not isinstance(h.profile, str) or not isinstance(h.session_title, str):
        raise ValueError("Hermes profile and session_title must be strings")
    if not h.args or h.args[0] != "chat" or any(arg not in {"chat", "--cli", "--tui", "--native"} for arg in h.args):
        raise ValueError("hermes.args supports chat and display options only; use hermes.profile/session_title/cwd for routing")
    return cfg
