"""Testes offline (sem chamar a API). Rode com:  python -m unittest discover tests"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parent.parent))

from agents.builder import Builder, check_file, parse_build  # noqa: E402
from agents.planner import Planner, normalize_plan  # noqa: E402
from agents.reviewer import Reviewer  # noqa: E402
from core.llm import extract_json  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402
from core.packages import check_requirements  # noqa: E402
from core.storage import TaskStore  # noqa: E402


class FakeLLM:
    """Devolve as respostas da fila, uma por chamada."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.calls = []

    def chat(self, model, messages, **kwargs):
        self.calls.append((model, [dict(m) for m in messages]))
        return self.replies.pop(0)

    def chat_json(self, model, messages, **kwargs):
        return extract_json(self.chat(model, messages, **kwargs))


PLAN_REPLY = json.dumps({
    "summary": "Criar uma API de tarefas",
    "steps": [
        {"id": 7, "title": "Criar modelo", "description": "Definir Task", "done_when": "Task existe"},
        {"title": "Criar rota GET", "done_when": "GET /tasks retorna 200"},
    ],
})


def build_reply(path: str, content: str, summary: str = "feito") -> str:
    return f"SUMMARY: {summary}\n=== FILE: {path} ===\n{content}\n=== END FILE ===\n"


class ExtractJsonTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(extract_json('Aqui:\n```json\n{"a": 1}\n```'), {"a": 1})

    def test_surrounding_text(self):
        self.assertEqual(extract_json('ok {"a": [1, 2]} fim'), {"a": [1, 2]})

    def test_empty(self):
        with self.assertRaises(ValueError):
            extract_json("")


class NormalizePlanTest(unittest.TestCase):
    def test_renumbers_and_fills_defaults(self):
        plan = normalize_plan(json.loads(PLAN_REPLY))
        self.assertEqual([s["id"] for s in plan["steps"]], [1, 2])
        self.assertEqual(plan["steps"][1]["description"], "")
        self.assertTrue(all(s["status"] == "pending" for s in plan["steps"]))

    def test_accepts_list_of_strings(self):
        plan = normalize_plan(["a", "b"])
        self.assertEqual([s["title"] for s in plan["steps"]], ["a", "b"])

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            normalize_plan({"steps": []})


class StorageTest(unittest.TestCase):
    def test_rejects_paths_outside_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore("t", Path(tmp))
            for bad in ("../x.py", "/etc/x", "C:/x.py", "a/../../x.py", "\\x.py"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    store.write_output(bad, "x")
            store.write_output("pkg/a.py", "print(1)")
            self.assertEqual(store.read_outputs(), {"pkg/a.py": "print(1)"})


class PackagesTest(unittest.TestCase):
    ALLOWED = frozenset({"fastapi", "httpx", "pytest", "python-dotenv"})

    def test_accepts_allowed_names_with_pins_extras_and_comments(self):
        text = "# deps\nFastAPI>=0.110\nhttpx[http2]==0.27 ; python_version>='3.9'\npython_dotenv\n\npytest  # testes\n"
        self.assertEqual(check_requirements(text, self.ALLOWED), [])

    def test_rejects_with_suggestion(self):
        problems = check_requirements("httpx2\nfastapii\ndotenv\nsqlite3\nleftpad", self.ALLOWED)
        self.assertIn("use 'httpx' instead", problems[0])
        self.assertIn("use 'fastapi' instead", problems[1])
        self.assertIn("use 'python-dotenv' instead", problems[2])
        self.assertIn("standard library", problems[3])
        self.assertIn("only allowed packages", problems[4])

    def test_rejects_pip_options_urls_and_paths(self):
        lines = ["-r other.txt", "--index-url https://evil.example/simple", "-e .",
                 "git+https://github.com/x/y.git", "https://x/y.whl", "./local_pkg"]
        self.assertEqual(len(check_requirements("\n".join(lines), self.ALLOWED)), len(lines))

    def test_env_adds_packages(self):
        old = os.environ.get("ALLOWED_PACKAGES")
        os.environ["ALLOWED_PACKAGES"] = "Leftpad, other_pkg"
        try:
            self.assertEqual(check_requirements("leftpad\nother-pkg"), [])
        finally:
            os.environ.pop("ALLOWED_PACKAGES") if old is None else os.environ.__setitem__("ALLOWED_PACKAGES", old)


class BuilderTest(unittest.TestCase):
    def test_system_prompt_lists_allowed_packages(self):
        prompt = Builder(llm=FakeLLM(), model="fake").system_prompt
        self.assertNotIn("<ALLOWED_PACKAGES>", prompt)
        self.assertIn("fastapi", prompt)

    def test_parse_multiple_files(self):
        text = (build_reply("a.py", "x = 1\ny = 2", "dois arquivos")
                + "=== FILE: b/c.txt ===\nlinha\n=== END FILE ===")
        result = parse_build(text)
        self.assertEqual(result["summary"], "dois arquivos")
        self.assertEqual([f["path"] for f in result["files"]], ["a.py", "b/c.txt"])
        self.assertEqual(result["files"][0]["content"], "x = 1\ny = 2\n")

    def test_parse_rejects_missing_files(self):
        with self.assertRaises(ValueError):
            parse_build("SUMMARY: nada")

    def test_check_file_rejects_literal_newlines_and_syntax_errors(self):
        for bad in ("import os\\nprint(os.name)", "def f(:\n    pass"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check_file("a.py", bad)
        check_file("a.py", "x = 1\n")
        check_file("notes.txt", "def f(:")  # só .py é compilado

    def test_retries_after_invalid_reply(self):
        llm = FakeLLM("SUMMARY: sem arquivos", build_reply("main.py", "app = (1\n"),
                      build_reply("main.py", "app = 1"))
        plan = {"goal": "g", **normalize_plan(json.loads(PLAN_REPLY))}
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore("t", Path(tmp))
            result = Builder(llm=llm, model="fake").build(plan["steps"][0], plan, store)
            self.assertEqual(result["files"], ["main.py"])
            self.assertEqual(result["attempts"], 3)
            self.assertEqual((store.output_dir / "main.py").read_text(), "app = 1\n")
        self.assertIn("rejected", llm.calls[1][1][-1]["content"])
        self.assertIn("SyntaxError", llm.calls[2][1][-1]["content"])

    def test_bad_path_counts_as_invalid_reply(self):
        llm = FakeLLM(*[build_reply("../fora.py", "x")] * 3)
        plan = {"goal": "g", **normalize_plan(json.loads(PLAN_REPLY))}
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore("t", Path(tmp))
            with self.assertRaises(RuntimeError):
                Builder(llm=llm, model="fake").build(plan["steps"][0], plan, store)
            self.assertFalse((Path(tmp) / "t" / "fora.py").exists())


class ScriptedReviewer(Reviewer):
    """Reviewer com comandos falsos: devolve (código, saída) da fila e registra os comandos."""

    def __init__(self, *runs):
        super().__init__(venv_root=Path(tempfile.gettempdir()))
        self.runs = list(runs)
        self.commands = []

    def _ensure_venv(self, store):
        return store.dir / "venv" / "Scripts" / "python.exe"

    def _run(self, cmd, cwd):
        self.commands.append(cmd)
        return self.runs.pop(0)


class FakeReviewer:
    def __init__(self, *verdicts: bool):
        self.verdicts = list(verdicts)

    def review(self, step, result, store):
        ok = self.verdicts.pop(0)
        return {"approved": ok, "stage": "pytest", "feedback": "" if ok else "FAILED test_x - assert 422 == 201"}


class ReviewerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore("t", Path(self.tmp.name))
        (self.store.dir / "venv" / "Scripts").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_approves_when_there_are_no_tests(self):
        self.store.write_output("main.py", "x = 1")
        review = ScriptedReviewer().review({}, {}, self.store)
        self.assertEqual((review["approved"], review["stage"]), (True, "no_tests"))

    def test_rejects_failing_tests_with_pytest_output(self):
        self.store.write_output("test_main.py", "def test_x(): assert False")
        reviewer = ScriptedReviewer((1, "FAILED test_main.py::test_x\n1 failed"))
        review = reviewer.review({}, {}, self.store)
        self.assertFalse(review["approved"])
        self.assertIn("1 failed", review["feedback"])
        self.assertIn("pytest", reviewer.commands[0])

    def test_installs_requirements_once_and_rejects_pip_errors(self):
        self.store.write_output("requirements.txt", "fastapi")
        self.store.write_output("test_main.py", "def test_x(): pass")
        reviewer = ScriptedReviewer((0, ""), (0, "1 passed"), (0, "1 passed"))
        self.assertTrue(reviewer.review({}, {}, self.store)["approved"])
        self.assertTrue(reviewer.review({}, {}, self.store)["approved"])
        self.assertEqual([c[2] for c in reviewer.commands], ["pip", "pytest", "pytest"])  # pip só na 1ª vez

        self.store.write_output("requirements.txt", "fastapi==999.0")  # permitido, mas a versão não existe
        reviewer.runs = [(1, "ERROR: No matching distribution")]
        review = reviewer.review({}, {}, self.store)
        self.assertEqual((review["approved"], review["stage"]), (False, "pip"))

    def test_rejects_unlisted_package_before_installing(self):
        self.store.write_output("requirements.txt", "fastapi\nhttpx2\n")
        self.store.write_output("test_main.py", "def test_x(): pass")
        reviewer = ScriptedReviewer()  # nenhuma execução esperada
        review = reviewer.review({}, {}, self.store)
        self.assertEqual((review["approved"], review["stage"]), (False, "packages"))
        self.assertIn("use 'httpx' instead", review["feedback"])
        self.assertEqual(reviewer.commands, [])

    def test_crusoe_key_is_not_passed_to_generated_code(self):
        old = os.environ.get("CRUSOE_API_KEY")
        os.environ["CRUSOE_API_KEY"] = "segredo"
        self.addCleanup(lambda: os.environ.pop("CRUSOE_API_KEY") if old is None
                        else os.environ.__setitem__("CRUSOE_API_KEY", old))
        code, out = Reviewer()._run([sys.executable, "-c", "import os; print(os.environ.get('CRUSOE_API_KEY'))"],
                                    self.store.dir)
        self.assertEqual((code, out), (0, "None"))


class WorkflowTest(unittest.TestCase):
    def test_plans_builds_and_reviews_every_step(self):
        llm = FakeLLM(PLAN_REPLY, build_reply("models.py", "class Task: ..."),
                      build_reply("main.py", "from models import Task"))
        with tempfile.TemporaryDirectory() as tmp:
            orch = Orchestrator(Planner(llm=llm, model="fake-planner"), reviewer=FakeReviewer(True, True),
                                root=Path(tmp))
            plan, store = orch.run("API de tarefas")
            self.assertEqual([s["status"] for s in plan["steps"]], ["approved", "approved"])
            self.assertEqual(store.load("plan"), plan)
            self.assertEqual(sorted(store.read_outputs()), ["main.py", "models.py"])
            self.assertEqual(store.load("step-2")["rounds"][0]["build"]["files"], ["main.py"])
            events = [json.loads(l)["event"] for l in (store.dir / "log.jsonl").read_text().splitlines()]
            self.assertEqual(events, ["goal", "planned", "step_reviewed", "step_reviewed"])
        # o segundo passo vê o arquivo criado no primeiro
        self.assertIn("class Task: ...", llm.calls[2][1][1]["content"])

    def test_review_failure_goes_back_to_builder_on_same_step(self):
        llm = FakeLLM(PLAN_REPLY, build_reply("main.py", "a = 1"), build_reply("main.py", "a = 2"),
                      build_reply("main.py", "b = 1"))
        with tempfile.TemporaryDirectory() as tmp:
            orch = Orchestrator(Planner(llm=llm, model="fake"), reviewer=FakeReviewer(False, True, True),
                                root=Path(tmp))
            plan, store = orch.run("API de tarefas")
            self.assertEqual([s["status"] for s in plan["steps"]], ["approved", "approved"])
            self.assertEqual(len(store.load("step-1")["rounds"]), 2)
        retry_prompt = llm.calls[2][1][1]["content"]
        self.assertIn("Execute now step 1", retry_prompt)
        self.assertIn("assert 422 == 201", retry_prompt)

    def test_stops_after_max_rejected_rounds(self):
        llm = FakeLLM(PLAN_REPLY, *[build_reply("main.py", "a = 1")] * 2)
        with tempfile.TemporaryDirectory() as tmp:
            orch = Orchestrator(Planner(llm=llm, model="fake"), reviewer=FakeReviewer(False, False),
                                root=Path(tmp), max_rounds=2)
            plan, store = orch.run("API de tarefas")
            self.assertEqual([s["status"] for s in plan["steps"]], ["failed", "pending"])
            self.assertEqual(store.load("plan"), plan)

    def test_stops_when_builder_fails(self):
        llm = FakeLLM(PLAN_REPLY, "{}", "{}", "{}")
        with tempfile.TemporaryDirectory() as tmp:
            orch = Orchestrator(Planner(llm=llm, model="fake"), reviewer=FakeReviewer(), root=Path(tmp))
            plan, store = orch.run("API de tarefas")
            self.assertEqual([s["status"] for s in plan["steps"]], ["failed", "pending"])


if __name__ == "__main__":
    unittest.main()
