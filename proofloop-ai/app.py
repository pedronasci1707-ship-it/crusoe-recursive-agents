"""ProofLoop AI: ponto de entrada.

Uso:  python -X utf8 app.py "seu objetivo aqui"
"""

import sys

from core.orchestrator import Orchestrator


def show(event: str, **data) -> None:
    if event == "planned":
        plan = data["plan"]
        print(f"\n{plan['summary']}\n")
        for step in plan["steps"]:
            print(f"{step['id']}. {step['title']}")
            if step["done_when"]:
                print(f"   ✔ pronto quando: {step['done_when']}")
        print()
    elif event == "step_start":
        print(f"▶ Passo {data['step']['id']}: {data['step']['title']}", flush=True)
    elif event == "step_reviewed":
        result, review = data["result"], data["review"]
        retries = f" ({result['attempts']} tentativas)" if result.get("attempts", 1) > 1 else ""
        print(f"  [rodada {data['round']}] {result['summary']}{retries}")
        for path in result["files"]:
            print(f"    + {path}")
        if review["approved"]:
            label = "sem testes ainda" if review["stage"] == "no_tests" else "testes passaram"
            print(f"  ✔ Reviewer aprovou ({label})", flush=True)
        else:
            last = review["feedback"].strip().splitlines()[-1] if review["feedback"].strip() else ""
            print(f"  ✖ Reviewer reprovou no {review['stage']}: {last}", flush=True)
    elif event == "step_failed":
        print(f"  ✖ falhou: {data['error']}")


def main() -> int:
    goal = " ".join(sys.argv[1:]).strip() or input("Objetivo: ").strip()
    if not goal:
        print("Informe um objetivo.")
        return 1
    plan, store = Orchestrator(on_event=show).run(goal)
    done = sum(s["status"] == "approved" for s in plan["steps"])
    print(f"\n{done}/{len(plan['steps'])} passos aprovados. Arquivos em {store.output_dir}")
    return 0 if done == len(plan["steps"]) else 1


if __name__ == "__main__":
    sys.exit(main())
