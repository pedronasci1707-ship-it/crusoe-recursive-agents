"""Persistência simples em disco: uma pasta por tarefa dentro de workspace/."""

import json
import re
from datetime import datetime
from pathlib import Path

# absolute() e não resolve(): no Windows resolve() pode trocar o caminho por um alias longo
# que passa do limite de 260 caracteres.
WORKSPACE = Path(__file__).absolute().parent.parent / "workspace"


class TaskStore:
    def __init__(self, task_id: str, root: Path = WORKSPACE):
        self.dir = root / task_id
        self.dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def new(cls, goal: str, root: Path = WORKSPACE) -> "TaskStore":
        slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:24].strip("-") or "task"
        return cls(f"{datetime.now():%Y%m%d-%H%M%S}-{slug}", root)

    def save(self, name: str, data) -> Path:
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, name: str):
        return json.loads((self.dir / f"{name}.json").read_text(encoding="utf-8"))

    @property
    def output_dir(self) -> Path:
        """Pasta onde o Builder escreve os arquivos que produz."""
        path = self.dir / "output"
        path.mkdir(exist_ok=True)
        return path

    def output_path(self, rel: str) -> Path:
        rel_path = Path(rel.replace("\\", "/"))
        # anchor pega "C:", "/x" e "\\server": no Windows "/x" não é is_absolute() mas sai da pasta
        if rel_path.anchor or ".." in rel_path.parts:
            raise ValueError(f"caminho fora da pasta de saída: {rel!r}")
        return self.output_dir / rel_path

    def write_output(self, rel: str, content: str) -> Path:
        path = self.output_path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read_outputs(self, max_chars: int = 40_000) -> dict[str, str]:
        """Arquivos já produzidos, {caminho relativo: conteúdo}, cortando no total de max_chars."""
        files, used = {}, 0
        for path in sorted(p for p in self.output_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(self.output_dir).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            if used + len(text) > max_chars:
                files[rel] = "[conteúdo omitido: limite de contexto]"
                continue
            files[rel] = text
            used += len(text)
        return files

    def log(self, event: str, **data) -> None:
        """Acrescenta um evento ao histórico da tarefa (append-only)."""
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **data}
        with (self.dir / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
