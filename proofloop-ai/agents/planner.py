"""Planner: transforma um objetivo em uma lista ordenada de passos verificáveis."""

import os

from core.llm import LLMClient

DEFAULT_MODEL = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"

# Mantido idêntico entre chamadas para aproveitar o prefix cache da Crusoe.
SYSTEM_PROMPT = """You are the Planner in ProofLoop AI, a team of autonomous agents.
Break the user's goal into a short ordered list of concrete steps that a Builder agent can execute
one at a time and a Reviewer agent can check objectively.

Rules:
- 3 to 8 steps. Each step is small, concrete and independently checkable.
- "done_when" must be an objective acceptance criterion (something a reviewer can verify).
- Write titles, descriptions and criteria in the same language as the goal.
- Reply with ONLY a JSON object, no prose, in this exact shape:
{"summary": "<one sentence>", "steps": [{"id": 1, "title": "...", "description": "...", "done_when": "..."}]}"""


class Planner:
    def __init__(self, llm: LLMClient | None = None, model: str | None = None):
        self.llm = llm or LLMClient()
        self.model = model or os.environ.get("PLANNER_MODEL", DEFAULT_MODEL)

    def plan(self, goal: str, feedback: str | None = None) -> dict:
        """Devolve {"goal", "summary", "steps": [...]}; `feedback` permite replanejar após uma falha."""
        user = f"Goal: {goal}"
        if feedback:
            user += f"\n\nThe previous plan failed. Feedback to take into account:\n{feedback}"
        raw = self.llm.chat_json(
            self.model,
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
            max_tokens=8192,
            temperature=1.0,
            top_p=0.95,
        )
        return {"goal": goal, **normalize_plan(raw)}


def normalize_plan(raw) -> dict:
    """Valida a saída do modelo e numera os passos de 1 a N."""
    if isinstance(raw, list):
        raw = {"steps": raw}
    steps = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"plano sem passos: {raw!r}")
    clean = []
    for i, step in enumerate(steps, start=1):
        if isinstance(step, str):
            step = {"title": step}
        title = str(step.get("title", "")).strip()
        if not title:
            raise ValueError(f"passo {i} sem título: {step!r}")
        clean.append({
            "id": i,
            "title": title,
            "description": str(step.get("description", "")).strip(),
            "done_when": str(step.get("done_when", "")).strip(),
            "status": "pending",
        })
    return {"summary": str(raw.get("summary", "")).strip(), "steps": clean}
