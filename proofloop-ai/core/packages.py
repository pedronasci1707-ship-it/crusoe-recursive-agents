"""Lista de pacotes que o projeto gerado pode instalar.

O modelo às vezes inventa nomes parecidos com pacotes reais (ex.: "httpx2" em vez de "httpx"),
e um pacote com nome parecido pode ser malicioso. Por isso o Reviewer só instala o que está aqui.
Para liberar mais pacotes sem editar o código: ALLOWED_PACKAGES=pacote1,pacote2 no .env.
"""

import difflib
import os
import re

DEFAULT_ALLOWED = frozenset({
    # web / API
    "fastapi", "uvicorn", "starlette", "pydantic", "pydantic-settings", "email-validator",
    "python-multipart", "flask", "jinja2", "httpx", "requests", "anyio",
    # dados
    "sqlalchemy", "aiosqlite", "alembic", "numpy", "pandas",
    # utilidades
    "python-dotenv", "typer", "click", "rich",
    # testes
    "pytest", "pytest-asyncio", "pytest-cov",
})

# Nomes que o modelo costuma errar -> nome correto (quando difflib não acharia sozinho).
KNOWN_MISTAKES = {"httpx2": "httpx", "dotenv": "python-dotenv", "multipart": "python-multipart"}
STDLIB = frozenset({"sqlite3", "unittest", "json", "os", "asyncio", "typing", "dataclasses",
                    "uuid", "datetime", "logging", "pathlib"})

REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([<>=!~]=?[^;]*)?(;.*)?$")


def normalize(name: str) -> str:
    """Nome canônico do PyPI (PEP 503): minúsculas, e -, _ e . viram -."""
    return re.sub(r"[-_.]+", "-", name).lower()


def allowed_packages() -> frozenset[str]:
    extra = os.environ.get("ALLOWED_PACKAGES", "")
    return DEFAULT_ALLOWED | {normalize(p.strip()) for p in extra.split(",") if p.strip()}


def check_requirements(text: str, allowed: frozenset[str] | None = None) -> list[str]:
    """Devolve uma lista de problemas (vazia = pode instalar), cada um dizendo o que usar no lugar."""
    allowed = allowed if allowed is not None else allowed_packages()
    problems = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = REQUIREMENT.match(line)
        if not match:
            # opções do pip (-r, -e, --index-url), URLs, git+ e caminhos locais instalam código de fora da lista
            problems.append(f"'{line}': only plain package names are allowed (no pip options, URLs or paths)")
            continue
        name = normalize(match.group(1))
        if name in allowed:
            continue
        if name in STDLIB:
            problems.append(f"'{match.group(1)}' is part of the Python standard library; remove it")
            continue
        hint = KNOWN_MISTAKES.get(name) or next(iter(difflib.get_close_matches(name, allowed, n=1)), None)
        msg = f"package '{match.group(1)}' is not in the allowed list"
        msg += f"; use '{hint}' instead" if hint else "; use only allowed packages or the standard library"
        problems.append(msg)
    return problems
