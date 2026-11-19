"""Start an interactive CLI without interpolating recognized speech into a shell."""
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from listener.speech_protocol import runtime_dir
from listener.console_process import spawn_console

from .config import HermesConfig


class HermesLauncher:
    def __init__(self, cfg: HermesConfig, *, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.process = None
        self._lock = threading.Lock()
        self._probed = False
        self.windows = {}

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def _prefix(self):
        if self.cfg.backend == "wsl":
            wsl = shutil.which("wsl.exe", path=self._environment().get("PATH"))
            if not wsl:
                raise FileNotFoundError("wsl.exe not found")
            prefix = [wsl]
            if self.cfg.wsl_distribution:
                prefix.extend(["-d", self.cfg.wsl_distribution])
            return prefix + ["--cd", self.cfg.wsl_cwd, "--", self.cfg.executable]
        executable = shutil.which(self.cfg.executable, path=self._environment().get("PATH"))
        if executable is None:
            raise FileNotFoundError(f"Hermes CLI not found: {self.cfg.executable}; set hermes.executable in config.json")
        if os.name == "nt" and Path(executable).suffix.lower() not in (".exe", ".com"):
            raise ValueError("Configure the Hermes .exe entry point, rather than a shell alias or .cmd/.ps1 wrapper")
        return [executable]

    @staticmethod
    def _environment():
        environment = dict(os.environ)
        if os.name == "nt":
            import winreg
            paths = [environment.get("PATH", "")]
            for hive, key in ((winreg.HKEY_LOCAL_MACHINE,
                               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                              (winreg.HKEY_CURRENT_USER, "Environment")):
                try:
                    with winreg.OpenKey(hive, key) as registry:
                        value, _ = winreg.QueryValueEx(registry, "Path")
                    paths.append(os.path.expandvars(value))
                except OSError:
                    pass
            environment["PATH"] = os.pathsep.join(paths)
        return environment

    def probe(self):
        cwd = Path(self.cfg.cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise FileNotFoundError(f"Hermes working directory does not exist: {cwd}")
        command = self._prefix() + self.profile_args() + self.cfg.args + ["--help"]
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=20,
                                env=self._environment(),
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise RuntimeError(f"Hermes CLI help failed (exit {result.returncode})")
        if "--query-file" not in result.stdout + result.stderr:
            raise RuntimeError("Installed Hermes CLI does not support --query-file; upgrade Hermes or configure a supported chat entry point")
        self._probed = True
        return {"available": True, "backend": self.cfg.backend, "command": command[:-1], "cwd": str(cwd)}

    def profile_args(self):
        return ["--profile", self.cfg.profile] if self.cfg.profile else []

    def session_cwd(self):
        if self.cfg.backend == "native":
            return str(Path(self.cfg.cwd).expanduser().resolve())
        result = subprocess.run(self._prefix()[:-1] + ["pwd"], capture_output=True,
                                text=True, encoding="utf-8", timeout=10, env=self._environment(),
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        path = result.stdout.strip()
        if result.returncode or not path.startswith("/"):
            raise ValueError("Cannot resolve WSL working directory")
        return path

    def open_session(self, session_id):
        """Open a viewer for a specific gateway session; never start another agent."""
        process = self.windows.get(session_id)
        if process is not None and process.poll() is None:
            return {"pid": process.pid, "already_open": True}
        display_args = list(self.cfg.args)
        if "--cli" not in display_args and "--tui" not in display_args:
            # The classic REPL's idle pump paints its own /queue admissions.
            # The TUI renders external Gateway admissions and approval events too.
            display_args.extend(["--tui", "--native"])
        command = self._prefix() + self.profile_args() + display_args + ["--resume", session_id]
        process = spawn_console(command, cwd=Path(self.cfg.cwd).expanduser().resolve(), env=self._environment())
        self.windows[session_id] = process
        return {"pid": process.pid, "already_open": False}

    def _wsl_path(self, path):
        prefix = self._prefix()[:-1]
        result = subprocess.run(prefix + ["wslpath", "-a", str(path)], capture_output=True,
                                text=True, encoding="utf-8", errors="strict", timeout=10,
                                env=self._environment(),
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode or not result.stdout.strip().startswith("/"):
            raise RuntimeError("Cannot map the prompt file into WSL")
        return result.stdout.strip()

    def launch(self, prompt=""):
        with self._lock:
            if self.running:
                raise RuntimeError("A Hermes CLI opened by this app is already running")
            if self.dry_run:
                return {"dry_run": True, "has_prompt": bool(prompt), "backend": self.cfg.backend}
            if not self._probed:
                self.probe()
            command = self._prefix() + self.profile_args() + self.cfg.args
            prompt_path = None
            try:
                if prompt:
                    prompts = runtime_dir() / "prompts"
                    prompts.mkdir(mode=0o700, exist_ok=True)
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt",
                                                     prefix="hermes-", dir=prompts, delete=False) as file:
                        file.write(prompt)
                        prompt_path = Path(file.name)
                    argument = self._wsl_path(prompt_path) if self.cfg.backend == "wsl" else str(prompt_path)
                    command.extend(["--query-file", argument])
                # Inherit a NEW console, not piped stdio: Hermes stays interactive.
                self.process = spawn_console(command, cwd=Path(self.cfg.cwd).expanduser().resolve(),
                                             env=self._environment())
            except Exception:
                if prompt_path:
                    prompt_path.unlink(missing_ok=True)
                raise
            if prompt_path:
                threading.Thread(target=self._cleanup, args=(self.process, prompt_path), daemon=True).start()
            return {"pid": self.process.pid, "has_prompt": bool(prompt)}

    @staticmethod
    def _cleanup(process, path):
        try:
            process.wait()
        finally:
            path.unlink(missing_ok=True)
