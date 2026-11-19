"""Bounded pending jobs; running inference is never forcibly interrupted."""
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Job:
    kind: str
    peer: Any = None
    request_id: str = ""
    session_id: str = ""
    audio: Any = None
    generation: int = 0
    start_frame: int = 0
    end_frame: int = 0
    final: bool = True
    expires: float = float("inf")
    value: str = ""
    created_at: float = field(default_factory=time.monotonic)


class JobQueue:
    PRIORITY = {"load": -2, "model": -1, "dictation": 0, "command": 1, "wake": 2}

    def __init__(self, capacity: int = 32):
        self.capacity = capacity
        self._jobs: list[Job] = []
        self._condition = threading.Condition()
        self._closed = False

    def put(self, job: Job) -> None:
        with self._condition:
            self._jobs = [j for j in self._jobs if j.expires > time.monotonic()
                          and not (job.kind == j.kind == "wake" and job.peer is j.peer)]
            if self._closed or len(self._jobs) >= self.capacity:
                raise RuntimeError("Speech queue is full or closed")
            self._jobs.append(job)
            self._condition.notify()

    def get(self) -> Job | None:
        with self._condition:
            while not self._jobs and not self._closed:
                self._condition.wait()
            if self._closed:
                return None
            index = min(range(len(self._jobs)), key=lambda i: self.PRIORITY[self._jobs[i].kind])
            return self._jobs.pop(index)

    def discard(self, predicate) -> None:
        with self._condition:
            self._jobs = [job for job in self._jobs if not predicate(job)]

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
