"""JSON-only local IPC. Never use Connection.recv(), which unpickles input."""
import hashlib
import json
import os
from pathlib import Path

VERSION = 1
MAX_MESSAGE = 256 * 1024


def runtime_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache"))
    path = root / "ListenerSpeech"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def endpoint() -> tuple[str, str]:
    if os.name == "nt":
        user = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:20]
        return rf"\\.\pipe\ListenerSpeech-v{VERSION}-{user}", "AF_PIPE"
    return str(runtime_dir() / "speech.sock"), "AF_UNIX"


def auth_key() -> bytes:
    # LocalAppData inherits the user's ACL. Keep the secret out of command lines.
    path = runtime_dir() / "auth.key"
    if not path.exists():
        key = os.urandom(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as file:
                file.write(key)
            return key
    import time
    for _ in range(100):
        data = path.read_bytes()
        if len(data) == 32:
            return data
        time.sleep(0.01)
    raise RuntimeError("Incomplete speech-service authentication key")


def send(connection, message: dict) -> None:
    data = json.dumps({**message, "version": VERSION}, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_MESSAGE:
        raise ValueError("IPC message too large")
    connection.send_bytes(data)


def receive(connection) -> dict:
    message = json.loads(connection.recv_bytes(MAX_MESSAGE).decode("utf-8"))
    if not isinstance(message, dict) or message.get("version") != VERSION:
        raise ValueError("Unsupported speech-service protocol")
    if not isinstance(message.get("type"), str):
        raise ValueError("Missing message type")
    return message


class ServiceLock:
    """An OS file lock, released even when the process crashes."""
    def __init__(self):
        self.file = None

    def __enter__(self):
        self.file = (runtime_dir() / "service.lock").open("a+b")
        try:
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Speech service already running") from None
        return self

    def __exit__(self, *exc):
        self.file.close()
