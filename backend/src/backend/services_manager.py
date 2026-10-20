import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from .settings import settings

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
BRIDGES_ROOT = REPO_ROOT / "bridges"
RUNTIME_DIR = REPO_ROOT / ".runtime"

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


def _find_pid_by_port(port: int) -> Optional[int]:
    """Find listening PID on a given TCP port."""
    try:
        res = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip():
            pids = res.stdout.strip().split()
            if pids:
                return int(pids[0])
    except Exception:
        pass
    return None


def _find_pids_by_pattern(pattern: str) -> List[int]:
    """Find PIDs matching pattern using pgrep, ignoring self."""
    try:
        res = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip():
            current_pid = os.getpid()
            pids = [int(p) for p in res.stdout.strip().split() if int(p) != current_pid]
            return pids
    except Exception:
        pass
    return []


def _kill_pids(pids: List[int], timeout_s: float = 3.0) -> bool:
    """Send SIGTERM then SIGKILL to a list of PIDs."""
    if not pids:
        return True
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning("Error sending SIGTERM to %s: %s", pid, e)

    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except ProcessLookupError:
                pass
        if not alive:
            return True
        time.sleep(0.3)

    # Force kill any remaining
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning("Error sending SIGKILL to %s: %s", pid, e)
    return True


def _read_env_file(filepath: Path) -> Dict[str, str]:
    env = os.environ.copy()
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


class ServiceDefinition:
    def __init__(
        self,
        service_id: str,
        name: str,
        description: str,
        category: str,
        port: Optional[int] = None,
        health_url: Optional[str] = None,
        log_filename: Optional[str] = None,
        pattern: Optional[str] = None,
    ):
        self.service_id = service_id
        self.name = name
        self.description = description
        self.category = category
        self.port = port
        self.health_url = health_url
        self.log_path = RUNTIME_DIR / (log_filename or f"{service_id}.log")
        self.pattern = pattern
        self.pid_path = RUNTIME_DIR / f"{service_id}.pid"

    def get_pids(self) -> List[int]:
        if self.port:
            pid = _find_pid_by_port(self.port)
            if pid:
                return [pid]
        owned = self._owned_pids()
        if owned:
            return owned
        if self.pattern:
            return _find_pids_by_pattern(self.pattern)
        return []

    def check_health(self) -> Dict[str, Any]:
        pids = self.get_pids()
        is_alive = len(pids) > 0
        details: Dict[str, Any] = {}
        warning: Optional[str] = None
        status = "running" if is_alive else "stopped"

        if self.health_url:
            try:
                res = httpx.get(self.health_url, timeout=1.5)
                if res.is_success:
                    try:
                        details = res.json()
                    except Exception:
                        details = {"raw": res.text[:200]}
                else:
                    if is_alive:
                        status = "warning"
                        warning = f"Health check returned HTTP {res.status_code}"
            except Exception as e:
                if is_alive:
                    status = "warning"
                    warning = f"Process running on port {self.port} but health check failed: {e}"

        # Custom service warnings
        if self.service_id == "flowkit" and status == "running":
            ext_connected = details.get("extension_connected", False)
            if not ext_connected:
                status = "warning"
                warning = "FlowKit extension is not connected. Open Google Chrome with the FlowKit extension."

        if self.service_id == "chatgpt_web" and status == "running":
            logged_in = details.get("logged_in_hint")
            if logged_in is False:
                status = "warning"
                warning = "ChatGPT browser is not signed in."

        return {
            "id": self.service_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "port": self.port,
            "status": status,
            "pid": pids[0] if pids else None,
            "all_pids": pids,
            "url": self.health_url or (f"http://127.0.0.1:{self.port}" if self.port else None),
            "warning": warning,
            "details": details,
            "log_path": str(self.log_path),
        }

    def _owned_pids(self) -> List[int]:
        if not self.pid_path.is_file():
            return []
        try:
            pid = int(self.pid_path.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return [pid]
        except (ValueError, ProcessLookupError, OSError):
            self.pid_path.unlink(missing_ok=True)
            return []

    def _remember_pid(self, pid: int) -> None:
        self.pid_path.write_text(str(pid), encoding="utf-8")

    def stop(self) -> Dict[str, Any]:
        if self.service_id == "gemini_web":
            docker_compose = BRIDGES_ROOT / "docker-compose.yml"
            res = subprocess.run(
                ["docker", "compose", "-f", str(docker_compose), "stop", "gemini-web"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode != 0:
                return {"ok": False, "error": f"Docker compose failed: {res.stderr}"}
            return {"ok": True, "message": f"{self.name} stopped successfully."}

        pids = self.get_pids()
        if not pids:
            self.pid_path.unlink(missing_ok=True)
            return {"ok": True, "message": f"{self.name} is already stopped."}
        _kill_pids(pids)
        self.pid_path.unlink(missing_ok=True)
        return {"ok": True, "message": f"{self.name} stopped successfully."}

    def start(self) -> Dict[str, Any]:
        pids = self.get_pids()
        if pids:
            return {"ok": True, "message": f"{self.name} is already running (PID {pids[0]})."}

        log_file = open(self.log_path, "a", encoding="utf-8")
        log_file.write(f"\n--- Started {self.name} at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log_file.flush()

        proc = None
        if self.service_id == "flowkit":
            flowkit_root = Path(settings.flowkit_root)
            python_bin = flowkit_root / "venv" / "bin" / "python"
            if not python_bin.exists():
                return {"ok": False, "error": f"FlowKit python executable not found at {python_bin}"}
            proc = subprocess.Popen(
                [str(python_bin), "-u", "-m", "agent.main"],
                cwd=str(flowkit_root),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        elif self.service_id == "chatgpt_web":
            provider_dir = BRIDGES_ROOT / "chatgpt-web-provider"
            python_bin = provider_dir / ".venv" / "bin" / "python"
            env = _read_env_file(BRIDGES_ROOT / "chatgpt.env")
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(BRIDGES_ROOT / ".playwright")
            proc = subprocess.Popen(
                [
                    str(python_bin),
                    "-m",
                    "uvicorn",
                    "chatgpt_web_provider.app:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8791",
                ],
                cwd=str(provider_dir),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        elif self.service_id == "movie_worker":
            worker_bin = BACKEND_ROOT / ".venv" / "bin" / "movie-worker"
            if not worker_bin.exists():
                return {"ok": False, "error": f"Movie worker not found at {worker_bin}"}
            proc = subprocess.Popen(
                [str(worker_bin)],
                cwd=str(BACKEND_ROOT),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        elif self.service_id == "gemini_web_image":
            provider_dir = BRIDGES_ROOT / "gemini-web-provider"
            python_bin = BRIDGES_ROOT / "chatgpt-web-provider" / ".venv" / "bin" / "python"
            env = _read_env_file(BRIDGES_ROOT / "gemini.env")
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(BRIDGES_ROOT / ".playwright")
            proc = subprocess.Popen(
                [
                    str(python_bin),
                    "-m",
                    "uvicorn",
                    "gemini_web_provider.app:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8792",
                ],
                cwd=str(provider_dir),
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        elif self.service_id == "gemini_web":
            try:
                subprocess.run(["docker", "info"], capture_output=True, timeout=3, check=True)
            except Exception:
                return {
                    "ok": False,
                    "error": "Docker Desktop is not running. Please launch Docker first to run Gemini Web Bridge.",
                }
            docker_compose = BRIDGES_ROOT / "docker-compose.yml"
            res = subprocess.run(
                ["docker", "compose", "-f", str(docker_compose), "up", "-d", "gemini-web"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if res.returncode != 0:
                return {"ok": False, "error": f"Docker compose failed: {res.stderr}"}
            return {"ok": True, "message": "Gemini Web container started."}

        else:
            return {"ok": False, "error": f"Unknown service: {self.service_id}"}

        if proc is not None:
            self._remember_pid(proc.pid)

        # Wait up to 4 seconds for startup
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            new_pids = self.get_pids()
            if new_pids:
                return {"ok": True, "message": f"{self.name} started successfully (PID {new_pids[0]})."}
            if proc and proc.poll() is not None:
                return {"ok": False, "error": f"{self.name} exited immediately with code {proc.poll()}"}
            time.sleep(0.3)
        return {"ok": True, "message": f"{self.name} start command issued."}

    def restart(self) -> Dict[str, Any]:
        stop_res = self.stop()
        if not stop_res.get("ok"):
            return stop_res

        # Poll until previous process PIDs are completely terminated (up to 5s)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not self.get_pids():
                break
            time.sleep(0.2)

        start_res = self.start()
        return {
            "ok": start_res.get("ok", False),
            "message": (
                f"{self.name} restarted successfully."
                if start_res.get("ok")
                else f"Failed to restart {self.name}: {start_res.get('error')}"
            ),
        }

    def get_logs(self, lines: int = 80) -> str:
        if not self.log_path.exists():
            return "No logs available yet."
        try:
            res = subprocess.run(
                ["tail", "-n", str(lines), str(self.log_path)],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return res.stdout or "Log file is empty."
        except Exception as e:
            return f"Error reading logs: {e}"


SERVICES: Dict[str, ServiceDefinition] = {
    "flowkit": ServiceDefinition(
        service_id="flowkit",
        name="Video Generation Service (FlowKit)",
        description="Google Flow (flow.google.com) AI bridge for Veo shot clips",
        category="Video Bridge",
        port=8100,
        health_url="http://127.0.0.1:8100/health",
        log_filename="flowkit.log",
        pattern=r"python.*agent\.main",
    ),
    "chatgpt_web": ServiceDefinition(
        service_id="chatgpt_web",
        name="ChatGPT Web Bridge",
        description="Playwright browser automation for storyboard frame generation",
        category="Image Bridge",
        port=8791,
        health_url="http://127.0.0.1:8791/health",
        log_filename="chatgpt.log",
        pattern=r"chatgpt_web_provider",
    ),
    "movie_worker": ServiceDefinition(
        service_id="movie_worker",
        name="Cutline Movie Worker",
        description="Persistent background task runner for storyboard images and video jobs",
        category="Worker",
        log_filename="worker.log",
        pattern=r"(movie-worker|huey.*backend\.tasks)",
    ),
    "gemini_web_image": ServiceDefinition(
        service_id="gemini_web_image",
        name="Gemini Web Image Bridge",
        description="Browser-backed image generation bridge with reference portrait attachments",
        category="Image Bridge",
        port=8792,
        health_url="http://127.0.0.1:8792/health",
        log_filename="gemini-image.log",
        pattern=r"gemini_web_provider",
    ),
    "gemini_web": ServiceDefinition(
        service_id="gemini_web",
        name="Gemini Web Bridge",
        description="Secondary fallback Docker bridge for image generation",
        category="Image Bridge",
        port=4981,
        health_url="http://127.0.0.1:4981/health",
        log_filename="gemini.log",
    ),
}


def list_services() -> List[Dict[str, Any]]:
    return [svc.check_health() for svc in SERVICES.values()]


def get_service(service_id: str) -> Optional[ServiceDefinition]:
    return SERVICES.get(service_id)
