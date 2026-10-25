import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

if not getattr(sys, "frozen", False):
    APP_DIR = Path(__file__).resolve().parent.parent
elif sys.platform == "darwin":
    # Writing inside Listener.app breaks its code signature; keep config/log in the user's Library.
    APP_DIR = Path.home() / "Library" / "Application Support" / "Listener"
    APP_DIR.mkdir(parents=True, exist_ok=True)
else:
    APP_DIR = Path(sys.executable).parent
CONFIG_PATH = APP_DIR / "config.json"

AVAILABLE_MODELS = ["medium", "large-v3"] + (["large-v3-turbo"] if sys.platform == "darwin" else [])


@dataclass
class Config:
    model: str = "large-v3"
    device: str = "auto"
    compute_type: str = "float16"
    language: str = "ko"
    hotkey: str = "ctrl+alt+m"
    beam_size: int = 5
    vad_filter: bool = True
    sample_rate: int = 16000
    input_device: str = ""  # sounddevice name (substring ok); "" = system default


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        return Config()
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
