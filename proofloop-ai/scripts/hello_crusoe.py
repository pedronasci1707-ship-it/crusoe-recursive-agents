"""Teste rápido da conexão com a Crusoe usando a chave do .env.

Uso (dentro de proofloop-ai/):  python -X utf8 scripts/hello_crusoe.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parent.parent))

from core.llm import LLMClient  # noqa: E402

reply = LLMClient().chat(
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    [{"role": "user", "content": "Say hello from ProofLoop AI"}],
    max_tokens=1024,
)
print(reply)
