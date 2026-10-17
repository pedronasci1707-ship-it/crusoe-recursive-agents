"""Orchestrator: loop planejar -> construir -> revisar -> verificar.

Cada passo é construído e revisado; se a revisão reprovar, o erro volta para o Builder
no mesmo passo, até `max_rounds` rodadas. O Verifier entra no próximo passo do roadmap.
"""

from pathlib import Path
from typing import Callable

from agents.builder import Builder
from agents.planner import Planner
from agents.reviewer import Reviewer
from core.storage import WORKSPACE, TaskStore


class Orchestrator:
    def __init__(self, planner: Planner | None = None, builder: Builder | None = None,
                 reviewer: Reviewer | None = None, root: Path = WORKSPACE, max_rounds: int = 3,
                 on_event: Callable[..., None] | None = None):
        self.planner = planner or Planner()
        self.builder = builder or Builder(llm=self.planner.llm)
        self.reviewer = reviewer or Reviewer()
        self.root = root
        self.max_rounds = max_rounds
        self.on_event = on_event or (lambda event, **data: None)

    def run(self, goal: str) -> tuple[dict, TaskStore]:
        store = TaskStore.new(goal, self.root)
        store.log("goal", goal=goal)

        plan = self.planner.plan(goal)
        store.save("plan", plan)
        store.log("planned", model=self.planner.model, steps=len(plan["steps"]))
        self.on_event("planned", plan=plan)

        for step in plan["steps"]:
            self.on_event("step_start", step=step)
            step["status"] = "approved" if self._run_step(step, plan, store) else "failed"
            store.save("plan", plan)
            if step["status"] == "failed":
                break

        return plan, store

    def _run_step(self, step: dict, plan: dict, store: TaskStore) -> bool:
        """Constrói e revisa o passo; devolve True se a revisão aprovou."""
        feedback = None
        rounds = []
        for round_no in range(1, self.max_rounds + 1):
            try:
                result = self.builder.build(step, plan, store, feedback=feedback)
                review = self.reviewer.review(step, result, store)
            except Exception as exc:
                store.log("step_failed", step=step["id"], round=round_no, error=str(exc))
                self.on_event("step_failed", step=step, error=str(exc))
                return False
            rounds.append({"round": round_no, "build": result, "review": review})
            store.save(f"step-{step['id']}", {"step_id": step["id"], "rounds": rounds})
            store.log("step_reviewed", step=step["id"], round=round_no, files=result["files"],
                      approved=review["approved"], stage=review["stage"])
            self.on_event("step_reviewed", step=step, round=round_no, result=result, review=review)
            if review["approved"]:
                return True
            feedback = review["feedback"]

        error = f"reprovado pelo Reviewer em {self.max_rounds} rodadas"
        store.log("step_failed", step=step["id"], error=error)
        self.on_event("step_failed", step=step, error=error)
        return False
