"""Cliente da Crusoe Managed Inference (API compatível com OpenAI)."""

import json
import os
import random
import re
import time
from pathlib import Path

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

ROOT = Path(__file__).absolute().parent.parent
DEFAULT_BASE_URL = "https://api.inference.crusoecloud.com/v1"


def load_env(path: Path = ROOT / ".env") -> None:
    """Carrega KEY=VALUE do .env sem sobrescrever variáveis já definidas."""
    if not path.exists():
        return
    # utf-8-sig: editores do Windows (e o PowerShell 5.1) gravam um BOM no início do arquivo
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def extract_json(text: str):
    """Extrai o primeiro objeto/array JSON de uma resposta (tolera ```json``` e texto em volta)."""
    if not text:
        raise ValueError("resposta vazia do modelo")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError(f"nenhum JSON na resposta: {text[:200]!r}")
    obj, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    return obj


class LLMClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, max_tries: int = 6):
        load_env()
        api_key = api_key or os.environ.get("CRUSOE_API_KEY")
        if not api_key:
            raise RuntimeError("CRUSOE_API_KEY não definida (crie o .env a partir do .env.example)")
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url or os.environ.get("CRUSOE_BASE_URL", DEFAULT_BASE_URL),
        )
        self.max_tries = max_tries

    def chat(self, model: str, messages: list[dict], max_tokens: int = 4096,
             json_mode: bool = False, **kwargs) -> str:
        """Faz uma chamada com backoff exponencial em 429/503 e devolve o texto da resposta."""
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        for attempt in range(self.max_tries):
            try:
                resp = self.client.chat.completions.create(
                    model=model, messages=messages, max_tokens=max_tokens, **kwargs
                )
                break
            except (RateLimitError, InternalServerError, APIConnectionError):
                if attempt == self.max_tries - 1:
                    raise
                time.sleep(min(60.0, 2 ** attempt) + random.uniform(0, 0.5))
        choice = resp.choices[0]
        content = choice.message.content
        if not content and choice.finish_reason == "length":
            raise RuntimeError(f"{model} gastou max_tokens={max_tokens} raciocinando; aumente max_tokens")
        return content or ""

    def chat_json(self, model: str, messages: list[dict], **kwargs):
        return extract_json(self.chat(model, messages, json_mode=True, **kwargs))
