"""Run with python3 scripts/test_dev.py; no real services or credentials used."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


class DevStartupTest(unittest.TestCase):
    def test_clean_setup_and_optional_bridges(self):
        for args in ([], ["--bridges"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "scripts").mkdir()
                (root / "backend").mkdir()
                (root / "frontend").mkdir()
                (root / "bridges").mkdir()
                shutil.copy(Path(__file__).with_name("dev"), root / "scripts/dev")
                binaries = root / "bin"
                binaries.mkdir()
                commands = {
                    "uv": 'mkdir -p .venv/bin; printf "#!/bin/sh\\nsleep 60\\n" > .venv/bin/movie-worker; chmod +x .venv/bin/movie-worker',
                    "npm": "mkdir -p node_modules",
                    "curl": "exit 0",
                    "lsof": 'case "$*" in *:8791*) exit 1;; *) exit 0;; esac',
                    "pgrep": "exit 1",
                    "docker": 'if [ "$1" = info ]; then exit 0; fi; echo unexpected-docker-build >&2; exit 1',
                }
                for name, body in commands.items():
                    path = binaries / name
                    path.write_text("#!/bin/sh\n" + body + "\n")
                    path.chmod(0o755)
                if args:
                    (root / "bridges/chatgpt.env").write_text("CHATGPT_WEB_API_KEYS=test\n")
                    bridge = root / "bridges/chatgpt-web-provider/.venv/bin/chatgpt-web-provider"
                    bridge.parent.mkdir(parents=True)
                    bridge.write_text("#!/bin/sh\nexit 1\n")
                    bridge.chmod(0o755)
                env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"])
                with subprocess.Popen(["bash", str(root / "scripts/dev"), *args], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True) as process:
                    try:
                        output = ""
                        for line in process.stdout:
                            output += line
                            if "all available services are ready" in line:
                                break
                        self.assertIn("all available services are ready", output)
                        self.assertNotIn("unexpected-docker-build", output)
                        time.sleep(3)
                        self.assertIsNone(process.poll(), "An optional bridge must not stop core services")
                        self.assertTrue((root / "frontend/node_modules").is_dir())
                        self.assertTrue((root / "backend/.venv/bin/movie-worker").is_file())
                    finally:
                        process.terminate()
                        process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
