"""Pure session decisions, independently testable without a tray or microphone."""
import time
import uuid
from collections import deque


class CommandSession:
    def __init__(self, matcher, *, cooldown=3.0):
        self.matcher = matcher
        self.cooldown = cooldown
        self.cooldown_until = 0.0
        self.id = None
        self.includes_wake = False
        self.seen = deque(maxlen=256)
        self.minimum_frame = 0
        self.last_decision = "not_evaluated"

    def on_utterance(self, event):
        identity = event.get("utterance_id")
        for rejected, reason in (
            (bool(self.id), "active_session"),
            (time.monotonic() < self.cooldown_until, "cooldown"),
            (identity in self.seen, "duplicate"),
            (event.get("end_frame", 0) <= self.minimum_frame, "before_resume_frame"),
            (time.monotonic() - event.get("created_at", time.monotonic()) > 15, "stale_event"),
        ):
            if rejected:
                self.last_decision = reason
                return None
        self.seen.append(identity)
        match = self.matcher.match(event["text"], avg_logprob=event.get("avg_logprob"),
                                   no_speech_prob=event.get("no_speech_prob"))
        self.last_decision = self.matcher.last_reason
        if match is None:
            return None
        self.id = uuid.uuid4().hex
        if event["final"] and match.command:
            return {"action": "launch", "prompt": match.command}
        self.includes_wake = not event["final"]
        return {"action": "arm", "after_frame": event["start_frame"] if self.includes_wake else event["end_frame"]}

    def on_transcript(self, event):
        if not self.id or event.get("session_id") != self.id:
            return None
        text = event["text"].strip()
        if self.includes_wake:
            match = self.matcher.match(text)
            if match is None:
                return {"action": "cancel", "error": "Wake phrase was not confirmed in the completed utterance"}
            self.includes_wake = False
            if not match.command:
                return {"action": "arm", "after_frame": event["end_frame"]}
            text = match.command
        if not text:
            return {"action": "cancel", "error": "No command was recognized"}
        return {"action": "launch", "prompt": text}

    def reset(self, *, cooldown=True):
        self.id = None
        self.includes_wake = False
        if cooldown:
            self.cooldown_until = time.monotonic() + self.cooldown
