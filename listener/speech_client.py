"""Thread-safe JSON RPC and asynchronous events over the local speech pipe."""
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from multiprocessing.connection import Client

from .config import APP_DIR
from .speech_protocol import auth_key, endpoint, receive, send



class SpeechClient:
    def __init__(self):
        self.events: queue.Queue = queue.Queue(maxsize=128)
        self._connection = None
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str, tuple[object, queue.Queue]] = {}
        self._reader = None

    @property
    def connected(self):
        return self._connection is not None

    def connect(self, *, start_service=True, timeout=300.0):
        if self.connected:
            return self.call("status")
        address, family = endpoint()
        key = auth_key()
        deadline = time.monotonic() + timeout
        spawned = False
        while True:
            try:
                connection = Client(address, family=family, authkey=key)
                break
            except (OSError, EOFError):
                if not start_service or time.monotonic() >= deadline:
                    raise ConnectionError("Cannot connect to the local speech service") from None
                if not spawned:
                    self._spawn_service()
                    spawned = True
                time.sleep(0.2)
        self._connection = connection
        self._reader = threading.Thread(target=self._read, args=(connection,), daemon=True)
        self._reader.start()
        try:
            while time.monotonic() < deadline:
                status = self.call("status")
                if status["state"] == "ready":
                    return status
                if status["state"] == "error":
                    raise RuntimeError(status.get("error", "Model load failed"))
                time.sleep(0.2)
            raise TimeoutError("Speech model initialization timed out")
        except Exception:
            self.close()
            raise

    @staticmethod
    def _spawn_service():
        if getattr(sys, "frozen", False):
            service = APP_DIR / "ListenerSpeech.exe"
            if not service.is_file():
                raise FileNotFoundError(f"Missing shared service: {service}")
            command = [str(service)]
        else:
            command = [sys.executable, "-m", "listener.speech_service"]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(command, cwd=APP_DIR, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)

    def call(self, operation: str, *, timeout=30.0, **payload):
        connection = self._connection
        if connection is None:
            raise ConnectionError("Speech service is disconnected")
        request_id = uuid.uuid4().hex
        response = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = (connection, response)
        try:
            with self._send_lock:
                send(connection, {"type": operation, "request_id": request_id, **payload})
            try:
                message = response.get(timeout=timeout)
            except queue.Empty:
                # A timed-out mutation may already have happened; never replay it.
                self.close()
                raise TimeoutError(f"Speech request timed out: {operation}") from None
            if not message.get("ok"):
                raise RuntimeError(message.get("error", "Speech request failed"))
            return message.get("result", {})
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def _event(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # A stalled UI must not block the reader and all RPC replies.
            try:
                self.events.get_nowait()
            except queue.Empty:
                pass
            self.events.put_nowait(event)

    def _read(self, connection):
        try:
            while True:
                message = receive(connection)
                if message["type"] == "response":
                    with self._pending_lock:
                        pending = self._pending.get(message.get("request_id"))
                    if pending is not None and pending[0] is connection:
                        pending[1].put_nowait(message)
                else:
                    self._event(message)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            if self._connection is connection:
                self._connection = None
            with self._send_lock:
                connection.close()
            with self._pending_lock:
                for owner, pending in self._pending.values():
                    if owner is connection:
                        try:
                            pending.put_nowait({"ok": False, "error": "Speech service disconnected"})
                        except queue.Full:
                            pass
            if self._connection is None:
                self._event({"type": "disconnected"})

    def close(self):
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                # Let the service release ownership immediately, then close locally.
                with self._send_lock:
                    send(connection, {"type": "detach", "request_id": uuid.uuid4().hex})
            except (OSError, EOFError):
                pass
            with self._send_lock:
                connection.close()
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
