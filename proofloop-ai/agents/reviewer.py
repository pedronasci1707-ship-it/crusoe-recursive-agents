"""Reviewer: instala o projeto gerado num venv isolado e roda os testes dele.

A revisão é objetiva (lista de pacotes permitidos, código de saída do pip e do pytest), sem chamar o modelo.
Atenção: isso executa código gerado pelo modelo na sua máquina. O venv isola os pacotes,
não é um sandbox.
"""

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from core.packages import allowed_packages, check_requirements
from core.storage import TaskStore

# Fora da pasta do projeto para não estourar o limite de 260 caracteres do Windows.
VENV_ROOT = Path(tempfile.gettempdir()) / "proofloop-venvs"
FEEDBACK_CHARS = 6000
PYTEST_NO_TESTS = 5


class Reviewer:
    def __init__(self, venv_root: Path = VENV_ROOT, timeout: int = 300):
        self.venv_root = venv_root
        self.timeout = timeout

    def review(self, step: dict, result: dict, store: TaskStore) -> dict:
        """Devolve {"approved", "feedback", "stage"}; `feedback` vai para o Builder quando reprova."""
        out = store.output_dir
        python = self._ensure_venv(store)

        req = out / "requirements.txt"
        if req.exists():
            problems = check_requirements(req.read_text(encoding="utf-8", errors="replace"))
            if problems:
                return self._reject("packages", "requirements.txt was rejected before installing:\n- "
                                    + "\n- ".join(problems)
                                    + "\nAllowed packages: " + ", ".join(sorted(allowed_packages())))
            digest = hashlib.sha256(req.read_bytes()).hexdigest()
            marker = python.parent.parent / "requirements.sha256"
            if not marker.exists() or marker.read_text() != digest:
                code, log = self._run([str(python), "-m", "pip", "install", "-q",
                                       "--disable-pip-version-check", "-r", str(req)], out)
                if code != 0:
                    return self._reject("pip", "pip install -r requirements.txt failed:\n" + log)
                marker.write_text(digest)

        if not any(out.rglob("test_*.py")) and not any(out.rglob("*_test.py")):
            return {"approved": True, "stage": "no_tests", "feedback": "Nenhum teste no projeto ainda."}

        code, log = self._run([str(python), "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider"], out)
        if code in (0, PYTEST_NO_TESTS):
            return {"approved": True, "stage": "pytest", "feedback": _tail(log, 1500)}
        return self._reject("pytest", f"pytest failed (exit code {code}):\n" + log)

    def _ensure_venv(self, store: TaskStore) -> Path:
        """Um venv por tarefa, criado na primeira revisão, já com pytest."""
        venv = self.venv_root / hashlib.sha1(str(store.dir).encode()).hexdigest()[:12]
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.exists():
            self._check(self._run([sys.executable, "-m", "venv", str(venv)], store.dir), "criar o venv")
            self._check(self._run([str(python), "-m", "pip", "install", "-q",
                                   "--disable-pip-version-check", "pytest"], store.dir), "instalar pytest")
        return python

    def _run(self, cmd: list[str], cwd: Path) -> tuple[int, str]:
        # A chave da Crusoe não vai para o código gerado.
        env = {k: v for k, v in os.environ.items() if not k.startswith("CRUSOE_")}
        env["PYTHONUTF8"] = "1"
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {self.timeout}s: {' '.join(cmd)}"
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    @staticmethod
    def _check(run: tuple[int, str], what: str) -> None:
        code, log = run
        if code != 0:
            raise RuntimeError(f"Reviewer não conseguiu {what}:\n{_tail(log, 2000)}")

    @staticmethod
    def _reject(stage: str, log: str) -> dict:
        return {"approved": False, "stage": stage, "feedback": _tail(log, FEEDBACK_CHARS)}


def _tail(text: str, limit: int) -> str:
    """O fim da saída é onde o pytest põe o resumo das falhas."""
    return text if len(text) <= limit else "...\n" + text[-limit:]
