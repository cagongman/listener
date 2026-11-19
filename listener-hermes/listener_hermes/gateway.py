"""Authenticated Hermes JSON-RPC transport. Tickets and prompts are never logged."""
import json
import logging
import queue
import subprocess
import threading
import uuid
from urllib.parse import urlsplit

log = logging.getLogger(__name__)


class GatewayError(RuntimeError):
    def __init__(self, reason, code=None):
        super().__init__(str(reason)[:300])
        self.reason = str(reason)
        self.code = code


class OutcomeUnknown(GatewayError):
    """A mutation may have reached the authority. Never automatically replay it."""


class GatewayClient:
    def __init__(self, launcher):
        self.launcher = launcher
        self.events = queue.Queue(maxsize=256)
        self.runtime = {}
        self._ws = None
        self._context = None
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = {}

    @property
    def connected(self):
        return self._ws is not None

    def connect(self):
        if self.connected:
            return self.runtime
        from websockets.sync.client import connect
        command = self.launcher._prefix() + self.launcher.profile_args() + [
            "gateway", "attach", "--start", "--timeout", "30"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=40, env=self.launcher._environment(),
                                creationflags=subprocess.CREATE_NO_WINDOW if subprocess._mswindows else 0)
        try:
            grant = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise GatewayError("Hermes Gateway bootstrap returned invalid JSON") from None
        if result.returncode:
            raise GatewayError("Hermes Gateway: " + str(grant.get("reason_code", grant.get("state", "unavailable"))))
        url = grant.get("url", "")
        parsed = urlsplit(url)
        if (parsed.scheme not in ("ws", "wss") or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or grant.get("protocol") != "hermes-gateway-v1"
                or not isinstance(grant.get("protocols"), list)):
            raise GatewayError("Unsupported Gateway bootstrap endpoint or protocol")
        try:
            context = connect(url, subprotocols=grant["protocols"], proxy=None, open_timeout=10,
                              close_timeout=2, max_size=8 * 1024 * 1024, max_queue=128)
            ws = context.__enter__()
            if ws.subprotocol != grant["protocol"]:
                ws.close()
                raise GatewayError("Gateway protocol negotiation failed")
        except GatewayError:
            raise
        except Exception as exc:
            # Handshake errors can contain authentication headers: only retain class.
            raise GatewayError(f"Gateway connection failed ({type(exc).__name__})") from None
        self._ws = ws
        self._context = context
        threading.Thread(target=self._read, args=(ws,), daemon=True).start()
        try:
            self.runtime = self.call("runtime.describe")
            log.info("Gateway connected: profile=%s epoch=%s", self.runtime.get("profile_id"),
                     self.runtime.get("authority_epoch"))
            return self.runtime
        except Exception:
            self.close()
            raise

    def call(self, method, *, timeout=20.0, **params):
        ws = self._ws
        if ws is None:
            raise GatewayError("Gateway disconnected; request was not sent")
        request_id = uuid.uuid4().hex
        response = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = (ws, response)
        try:
            try:
                with self._send_lock:
                    ws.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                                       ensure_ascii=False))
            except Exception:
                raise OutcomeUnknown("Gateway connection lost; request outcome is unknown") from None
            try:
                frame = response.get(timeout=timeout)
            except queue.Empty:
                self.close()
                raise OutcomeUnknown("Gateway response timed out; request outcome is unknown") from None
            if isinstance(frame, Exception):
                raise frame
            if "error" in frame:
                error = frame["error"]
                data = error.get("data") or {}
                raise GatewayError(data.get("type") or error.get("message", "Gateway RPC refused"), error.get("code"))
            return frame.get("result", {})
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def _read(self, ws):
        try:
            while True:
                frame = json.loads(ws.recv())
                with self._pending_lock:
                    pending = self._pending.get(frame.get("id"))
                if pending and pending[0] is ws:
                    pending[1].put_nowait(frame)
                elif "method" in frame:
                    params = frame.get("params") or {}
                    kind = params.get("type")
                    # Discard model text/tool contents; the CLI is the renderer.
                    if kind in {"session.info", "session.updated", "message.start", "message.complete",
                                "session.replay_gap", "approval.request", "approval.settled",
                                "clarify.request", "clarify.settled"}:
                        payload = params.get("payload") or {}
                        event = {"session_id": params.get("session_id"), "type": kind,
                                 "admission_id": params.get("admission_id") or payload.get("admission_id"),
                                 "execution_generation": params.get("execution_generation"),
                                 "status": payload.get("status"), "running": payload.get("running"),
                                 "title": payload.get("title")}
                        try:
                            self.events.put_nowait(event)
                        except queue.Full:
                            self.events.get_nowait()
                            self.events.put_nowait({"type": "refresh_required"})
        except Exception:
            pass
        finally:
            if self._ws is ws:
                self._ws = None
            with self._pending_lock:
                for owner, pending in self._pending.values():
                    if owner is ws:
                        try:
                            pending.put_nowait(OutcomeUnknown("Gateway disconnected; request outcome is unknown"))
                        except queue.Full:
                            pass

    def close(self):
        ws, self._ws = self._ws, None
        context, self._context = self._context, None
        if context is not None:
            context.__exit__(None, None, None)
        elif ws is not None:
            ws.close()
