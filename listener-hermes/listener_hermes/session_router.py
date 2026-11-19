"""Voice control phrases are exact commands; everything else is a normal prompt."""
import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    action: str
    value: str = ""


def classify(text: str) -> Intent:
    normalized = unicodedata.normalize("NFKC", text).strip().strip(".!?。！？")
    compact = re.sub(r"[\s,，]+", "", normalized)
    commands = {
        "new": {"새대화", "새대화시작해", "새대화시작해줘", "새대화열어", "새세션시작해", "새세션열어"},
        "reopen": {"창다시열어", "창다시열어줘", "대화다시열어", "현재대화열어", "현재대화다시열어"},
        "previous": {"이전대화열어", "이전대화열어줘", "이전대화로돌아가", "이전세션열어"},
        "list": {"대화목록", "대화목록보여줘", "세션목록", "세션목록보여줘"},
    }
    for action, phrases in commands.items():
        if compact in phrases:
            return Intent(action)
    ordinal = re.fullmatch(r"(첫번째|두번째|세번째|네번째|다섯번째|[1-9]번?)대화(?:열어|열어줘|선택해)", compact)
    if ordinal:
        number = {"첫번째": 1, "두번째": 2, "세번째": 3, "네번째": 4, "다섯번째": 5}.get(ordinal[1])
        return Intent("select_index", str(number or int(ordinal[1].rstrip("번"))))
    return Intent("send", text)
