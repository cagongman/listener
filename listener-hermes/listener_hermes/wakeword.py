import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class WakeMatch:
    phrase: str
    command: str


class WakewordMatcher:
    def __init__(self, phrases, aliases=None):
        self.patterns = []
        self.last_reason = "not_evaluated"
        entries = [(phrase, phrase) for phrase in phrases] + list((aliases or {}).items())
        for spoken, phrase in sorted(entries, key=lambda entry: len(entry[0].replace(" ", "")), reverse=True):
            normalized = unicodedata.normalize("NFKC", spoken).strip()
            pattern = r"[\s,，]*".join(re.escape(word) for word in normalized.split())
            self.patterns.append((phrase, spoken != phrase,
                                  re.compile(r"^" + pattern + r"(?=$|[\s,，.!?。！？:：…\"'“”‘’」』])")))

    def match(self, text: str, *, avg_logprob=None, no_speech_prob=None):
        self.last_reason = "not_wake_phrase"
        text = unicodedata.normalize("NFKC", text).lstrip(' \t\n\r\"\'“”‘’「『')
        for phrase, alias, pattern in self.patterns:
            result = pattern.match(text)
            if result is None:
                continue
            short = len(phrase.replace(" ", "")) <= 3
            if avg_logprob is not None and avg_logprob < (-0.65 if short else -1.0):
                self.last_reason = "low_logprob"
                return None
            if no_speech_prob is not None and no_speech_prob > (0.4 if short else 0.6):
                self.last_reason = "high_no_speech_probability"
                return None
            command = text[result.end():].strip(' \t\r\n,，.!?。！？:：…\"“”\'‘’」』')
            if re.match(r"^(?:는|가|를|의|처럼|에게)(?:\s|$)", command):
                self.last_reason = "ordinary_mention"
                return None
            self.last_reason = "matched_alias" if alias else "matched"
            return WakeMatch(phrase, command)
        return None
