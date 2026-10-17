"""Builder: executa um passo do plano escrevendo arquivos na pasta de saída da tarefa."""

import json
import os
import re

from core.llm import LLMClient
from core.packages import allowed_packages
from core.storage import TaskStore

DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

# Código dentro de strings JSON sai com "\n" literal e aspas quebradas, então o Builder
# responde em blocos de texto puro. Mantido idêntico entre chamadas para o prefix cache.
SYSTEM_PROMPT = """You are the Builder in ProofLoop AI, a team of autonomous agents.
You receive the overall goal, the full plan, the files produced so far, and ONE step to execute now.
Execute only that step by creating or rewriting files.

Rules:
- Output complete file contents (never diffs, never "..." placeholders).
- Paths are relative to the project root, use forward slashes, never ".." or absolute paths.
- Only include files you create or change in this step; existing files you leave out are kept.
- Make the result satisfy the step's "done_when" criterion.
- requirements.txt may list ONLY these packages (plain names, optional version pins), nothing else:
  <ALLOWED_PACKAGES>
  Anything else is rejected before installation; prefer the standard library.
- Reply in EXACTLY this plain-text format and nothing else (no JSON, no markdown fences):

SUMMARY: <what you did, one or two sentences>
=== FILE: relative/path.ext ===
<full file content, written exactly as it should appear on disk>
=== END FILE ===

Repeat the FILE block once per file."""

FILE_BLOCK = re.compile(r"^=== FILE: (.+?) ===[ \t]*\r?\n(.*?)^=== END FILE ===[ \t]*$", re.DOTALL | re.MULTILINE)
SUMMARY_LINE = re.compile(r"^SUMMARY:[ \t]*(.+)$", re.MULTILINE)


class Builder:
    def __init__(self, llm: LLMClient | None = None, model: str | None = None, max_attempts: int = 3):
        self.llm = llm or LLMClient()
        self.model = model or os.environ.get("BUILDER_MODEL", DEFAULT_MODEL)
        self.max_attempts = max_attempts
        # Montado depois do LLMClient carregar o .env, que pode ter ALLOWED_PACKAGES.
        self.system_prompt = SYSTEM_PROMPT.replace("<ALLOWED_PACKAGES>", ", ".join(sorted(allowed_packages())))

    def build(self, step: dict, plan: dict, store: TaskStore, feedback: str | None = None) -> dict:
        """Executa `step`, grava os arquivos e devolve {"step_id", "summary", "files", "attempts"}.

        `feedback` é para o Reviewer pedir uma nova tentativa explicando o que faltou.
        """
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self._prompt(step, plan, store, feedback)},
        ]
        error = None
        for attempt in range(1, self.max_attempts + 1):
            reply = self.llm.chat(self.model, messages, max_tokens=16384, temperature=1.0, top_p=0.95)
            try:
                result = parse_build(reply)
                for f in result["files"]:
                    store.output_path(f["path"])  # recusa caminhos fora da pasta de saída
                    check_file(f["path"], f["content"])
                break
            except ValueError as exc:  # formato errado ou código que não compila: pede de novo explicando
                error = exc
                messages += [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": f"Your reply was rejected: {exc}\n"
                                                "Fix it and reply again with ALL files for this step, "
                                                "in the exact SUMMARY / FILE block format."},
                ]
        else:
            raise RuntimeError(f"Builder falhou no passo {step['id']} após {self.max_attempts} tentativas: {error}")

        written = [store.write_output(f["path"], f["content"]) for f in result["files"]]
        return {
            "step_id": step["id"],
            "summary": result["summary"],
            "files": [p.relative_to(store.output_dir).as_posix() for p in written],
            "attempts": attempt,
        }

    @staticmethod
    def _prompt(step: dict, plan: dict, store: TaskStore, feedback: str | None) -> str:
        # Partes estáveis primeiro (objetivo, plano) e as que mudam no fim, para o cache de prefixo.
        plan_view = [{k: s[k] for k in ("id", "title", "description", "done_when")} for s in plan["steps"]]
        files = store.read_outputs()
        files_view = "\n\n".join(f"=== FILE: {path} ===\n{text}\n=== END FILE ===" for path, text in files.items())
        prompt = (
            f"Goal: {plan['goal']}\n\n"
            f"Plan:\n{json.dumps(plan_view, ensure_ascii=False, indent=2)}\n\n"
            f"Files so far:\n{files_view or '(none yet)'}\n\n"
            f"Execute now step {step['id']}: {step['title']}\n"
            f"Description: {step['description']}\n"
            f"Done when: {step['done_when']}"
        )
        if feedback:
            prompt += f"\n\nA reviewer rejected the previous attempt at this step:\n{feedback}"
        return prompt


def parse_build(text: str) -> dict:
    """Lê a resposta em blocos SUMMARY / FILE; precisa ter pelo menos um arquivo."""
    files = [{"path": m.group(1).strip(), "content": m.group(2)} for m in FILE_BLOCK.finditer(text or "")]
    if not files:
        raise ValueError("no '=== FILE: path ===' ... '=== END FILE ===' blocks found")
    for f in files:
        if not f["path"]:
            raise ValueError("FILE block without a path")
    summary = SUMMARY_LINE.search(text)
    return {"summary": summary.group(1).strip() if summary else "", "files": files}


def check_file(path: str, content: str) -> None:
    """Checagem barata antes de gravar: Python precisa compilar."""
    if not path.endswith(".py"):
        return
    if "\n" not in content.strip() and "\\n" in content:
        raise ValueError(f"{path}: newlines were written as literal \\n; write real line breaks")
    try:
        compile(content, path, "exec")
    except SyntaxError as exc:
        raise ValueError(f"{path} line {exc.lineno}: SyntaxError: {exc.msg}") from None
