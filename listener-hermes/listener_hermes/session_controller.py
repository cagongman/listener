"""Persist the selected session; submit prompts to its authority, independently of windows."""
import hashlib
import json
import logging
import queue
import time
import uuid
from pathlib import Path

from listener.speech_protocol import runtime_dir

from .gateway import GatewayClient, GatewayError, OutcomeUnknown

log = logging.getLogger(__name__)


class SessionChoiceRequired(RuntimeError):
    pass


class SessionController:
    def __init__(self, cfg, launcher, *, gateway=None, state_path=None, dry_run=False):
        self.cfg = cfg
        self.launcher = launcher
        self.gateway = gateway or GatewayClient(launcher)
        self.dry_run = dry_run
        identity = json.dumps([cfg.backend, cfg.executable, cfg.profile, str(Path(cfg.cwd).resolve()), cfg.wsl_cwd])
        self.state_path = state_path or runtime_dir() / ("sessions-" + hashlib.sha256(identity.encode()).hexdigest()[:16] + ".json")
        self.state = {"version": 1, "current": None, "previous": None, "known": {}, "submissions": []}
        if self.state_path.exists():
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            if (not isinstance(saved, dict) or saved.get("version") != 1
                    or not isinstance(saved.get("known"), dict) or not isinstance(saved.get("submissions"), list)):
                raise ValueError(f"Invalid session state file: {self.state_path}")
            self.state.update(saved)
        self.catalog = []
        self.active_info = {}
        self._last_poll = 0.0
        self.workspace = str(Path(cfg.cwd).expanduser().resolve())

    @property
    def current(self):
        return self.state.get("current")

    @property
    def title(self):
        return self.state["known"].get(self.current, {}).get("title") or (self.current or "선택 없음")

    @property
    def connected(self):
        return self.dry_run or self.gateway.connected

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def connect(self):
        if self.dry_run:
            return {"dry_run": True}
        runtime = self.gateway.connect()
        if self.cfg.backend == "wsl":
            self.workspace = self.launcher.session_cwd()
        previous_profile = self.state.get("profile_id")
        profile = runtime.get("profile_id")
        if previous_profile and profile != previous_profile:
            self.gateway.close()
            raise GatewayError("Gateway profile changed; select a conversation explicitly before sending")
        self.state["profile_id"] = profile
        if self.current:
            try:
                self._adopt(self.gateway.call("session.resume", session_id=self.current))
            except GatewayError as exc:
                if exc.reason not in {"not_found", "permission_denied", "profile_mismatch"}:
                    raise
                self.state["current"] = None
                self.active_info = {}
                self._save()
        self.list_sessions()
        return runtime

    def _adopt(self, snapshot):
        session_id = snapshot.get("stored_session_id") or snapshot.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise GatewayError("Gateway returned no stable session ID")
        info = snapshot.get("info") or {}
        old = self.current
        if old and old != session_id:
            self.state["previous"] = old
        self.state["current"] = session_id
        self._update_activity(snapshot)
        self.state["known"][session_id] = {"title": info.get("title") or session_id,
                                          "last_selected": time.time()}
        self._save()
        log.info("Session selected: id=%s", session_id)
        return session_id

    def _update_activity(self, snapshot):
        pending = snapshot.get("pending") or []
        prompts = snapshot.get("prompts") or []
        open_requests = snapshot.get("open_requests") or []
        self.active_info = {**(snapshot.get("info") or {}),
                            "execution_generation": snapshot.get("execution_generation"),
                            "pending": pending,
                            "running": snapshot.get("running", any(row.get("status") == "started" for row in pending)),
                            "waiting": bool(prompts or open_requests),
                            "waiting_ids": tuple(str(p.get("prompt_id") or p.get("request_id") or "pending")
                                                 for p in [*prompts, *open_requests]),
                            "queue_count": sum(row.get("status") == "queued" for row in pending)}
        # Snapshot reconciliation also works after missed events / app reconnects.
        for row in pending:
            for submission in self.state["submissions"]:
                if (submission.get("admission_id") == row.get("admission_id")
                        and submission.get("status") != row.get("status")):
                    submission["status"] = row["status"]
                    self._save()

    def list_sessions(self, *, live_only=False):
        if self.dry_run:
            return self.catalog
        found = []
        seen = set()
        scopes = ["live"] if live_only else ["live", "stored"]
        for scope in scopes:
            params = {"scope": scope, "limit": 20, "picker": True}
            if not self.cfg.session_title:
                params["workspace"] = self.workspace
            result = self.gateway.call("session.list", **params)
            for row in result.get("sessions", []):
                sid = row.get("session_id") or row.get("stored_session_id") or row.get("id")
                if not sid or sid in seen:
                    continue
                if row.get("source") not in {None, "cli", "tui", "gui", "desktop"}:
                    continue
                seen.add(sid)
                found.append({"id": sid, "title": row.get("title") or sid,
                              "state": row.get("execution_state", ""), "scope": scope})
        if not live_only:
            self.catalog = found
        return found

    def new(self):
        if self.dry_run:
            return {"dry_run": True, "action": "new"}
        accepted = self.gateway.runtime.get("session_create", {}).get("parameters", [])
        if "cwd" not in accepted:
            raise GatewayError("Gateway does not support an explicit working directory")
        snapshot = self.gateway.call("session.create", request_id=uuid.uuid4().hex,
                                     source="cli", cwd=self.workspace)
        session_id = self._adopt(snapshot)
        self.launcher.open_session(session_id)
        self.list_sessions()
        return {"action": "new", "session_id": session_id, "title": self.title}

    def ensure_current(self):
        if self.current:
            return self.current
        if self.cfg.session_title:
            # Explicit named target (e.g. a future Bot Chat), never global latest.
            return self._adopt(self.gateway.call("session.resume", session_id=self.cfg.session_title))
        live = self.list_sessions(live_only=True)
        if len(live) == 1:
            return self._adopt(self.gateway.call("session.resume", session_id=live[0]["id"]))
        if len(live) > 1:
            self.list_sessions()
            raise SessionChoiceRequired("여러 대화가 열려 있습니다. 트레이의 ‘대화 선택’에서 대화를 선택해 주세요.")
        self.new()
        return self.current

    def select(self, session_id, *, open_window=True):
        if self.dry_run:
            return {"dry_run": True, "action": "select"}
        self._adopt(self.gateway.call("session.resume", session_id=session_id))
        if open_window:
            self.launcher.open_session(self.current)
        return {"action": "select", "session_id": self.current, "title": self.title}

    def previous(self):
        previous = self.state.get("previous")
        if not previous:
            raise SessionChoiceRequired("이전에 선택한 대화가 없습니다. ‘대화 목록 보여줘’로 선택해 주세요.")
        return self.select(previous)

    def reopen(self):
        if self.dry_run:
            return {"dry_run": True, "action": "reopen"}
        session_id = self.ensure_current()
        view = self.launcher.open_session(session_id)
        return {"action": "reopen", "session_id": session_id, "title": self.title, **view}

    def send(self, text, *, input_id=None):
        if self.dry_run:
            return {"dry_run": True, "action": "send", "has_prompt": bool(text)}
        session_id = self.ensure_current()
        # Re-subscribe after a reconnect and snapshot the exact target before admission.
        self._adopt(self.gateway.call("session.resume", session_id=session_id))
        input_id = input_id or uuid.uuid4().hex
        existing = next((s for s in self.state["submissions"] if s["input_id"] == input_id), None)
        if existing:
            raise OutcomeUnknown("This voice request was already submitted; it will not be replayed")
        entry = {"input_id": input_id, "session_id": session_id, "status": "unknown", "created_at": time.time()}
        self.state["submissions"] = (self.state["submissions"] + [entry])[-100:]
        self._save()
        # prompt.submit has queue intent on the authority. No interrupt, no new CLI.
        receipt = self.gateway.call("prompt.submit", session_id=session_id, input_id=input_id,
                                    text=text, queued=True)
        admission_id = receipt.get("admission_id")
        if not isinstance(admission_id, str) or not admission_id:
            raise OutcomeUnknown("Gateway returned no admission receipt; do not resend automatically")
        entry.update({"admission_id": admission_id, "status": receipt.get("status", "queued")})
        self._save()
        log.info("Voice input admitted: session=%s input=%s admission=%s status=%s", session_id,
                 input_id, admission_id, entry["status"])
        # Reopen a known closed viewer while leaving the same session/agent intact.
        view = self.launcher.windows.get(session_id)
        if view is not None and view.poll() is not None:
            self.launcher.open_session(session_id)
        return {"action": "send", "session_id": session_id, "title": self.title,
                "status": entry["status"], "admission_id": admission_id}

    def poll(self):
        """Consume only lifecycle metadata. The CLI renders model/tool output."""
        if self.dry_run:
            return False
        changed = False
        while True:
            try:
                event = self.gateway.events.get_nowait()
            except queue.Empty:
                break
            kind = event["type"]
            if kind == "refresh_required":
                self._last_poll = 0
            if event.get("session_id") == self.current:
                changed = True
                if kind == "message.start":
                    self.active_info["running"] = True
                elif kind == "message.complete":
                    self.active_info["running"] = False
                elif kind in {"approval.request", "clarify.request"}:
                    self.active_info["waiting"] = True
                    self._last_poll = 0
                elif kind in {"approval.settled", "clarify.settled"}:
                    self.active_info["waiting"] = False
                    self._last_poll = 0
                elif kind == "session.replay_gap":
                    self._last_poll = 0
                if event.get("title"):
                    self.state["known"].setdefault(self.current, {})["title"] = event["title"]
            if kind == "message.complete":
                for submission in self.state["submissions"]:
                    if submission.get("admission_id") == event.get("admission_id"):
                        submission["status"] = event.get("status") or "terminal"
                        self._save()
        if self.current and time.monotonic() - self._last_poll >= 5:
            self._last_poll = time.monotonic()
            # session.info omits live approval prompts. Reattach the same ID with
            # transcript omitted to recover authoritative activity and waiters.
            snapshot = self.gateway.call("session.resume", session_id=self.current, omit_messages=True)
            self._update_activity(snapshot)
            info = snapshot.get("info") or {}
            if info.get("title"):
                self.state["known"].setdefault(self.current, {})["title"] = info["title"]
            changed = True
        return changed

    def close(self):
        self.gateway.close()
