# ProofLoop AI

Equipe de agentes autônomos (Planner → Builder → Reviewer → Verifier) rodando na
Crusoe Managed Inference, feita para o Crusoe Recursive Agents Hackathon.

## Status

| Parte | Estado |
|---|---|
| Cliente Crusoe (`core/llm.py`): backoff em 429/503, modo JSON | ✅ |
| `core/storage.py`: plano e log por tarefa em `workspace/` | ✅ |
| `agents/planner.py` | ✅ |
| `agents/builder.py`: escreve os arquivos de cada passo em `output/` | ✅ |
| `agents/reviewer.py`: instala `requirements.txt` num venv e roda o pytest do projeto gerado | ✅ |
| `core/orchestrator.py`: planeja, constrói e revisa cada passo; reprovação volta ao Builder | ✅ |
| `core/verifier.py` | ⬜ a implementar |

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env        # e cole sua CRUSOE_API_KEY
python -X utf8 scripts/hello_crusoe.py   # testa a conexão com a Crusoe
python -X utf8 app.py "Criar uma API REST de lista de tarefas em FastAPI"
```

Cada execução cria `workspace/<data>-<objetivo>/` com:

- `plan.json`: o plano, com o status de cada passo
- `step-N.json`: cada rodada do passo, com o que o Builder fez e o veredito do Reviewer
- `output/`: os arquivos produzidos
- `log.jsonl`: o histórico de eventos

## O loop de cada passo

1. O Builder escreve os arquivos do passo em `output/` (e refaz se um `.py` não compilar).
2. O Reviewer confere o `requirements.txt` contra a lista de pacotes permitidos (`core/packages.py`).
   Pacote fora da lista, opção do pip (`-r`, `--index-url`, `-e`), URL ou caminho local: reprova sem instalar nada
   e diz ao Builder qual pacote usar (ex.: `httpx2` → `httpx`).
3. Depois instala o `requirements.txt` num venv da tarefa (em `%TEMP%\proofloop-venvs\`) e roda `pytest`.
   Sem testes no projeto ainda, o passo é aprovado.
4. Se a lista, o pip ou o pytest reprovar, a saída do erro volta para o Builder, que tenta o mesmo passo de novo.
   Depois de 3 rodadas reprovadas, o passo fica `failed` e o loop para.

Para liberar mais pacotes, adicione no `.env`: `ALLOWED_PACKAGES=pacote1,pacote2`.
A lista vale só para os pacotes que o projeto pede; as dependências deles o pip resolve normalmente.

**Atenção:** o Reviewer executa código gerado pelo modelo na sua máquina. O venv isola os pacotes,
mas não é um sandbox. A `CRUSOE_API_KEY` não é repassada para esse código.
Use `-X utf8` no Windows para o console imprimir emojis e acentos.

## Testes

```bash
python -m unittest discover tests
```

Os testes não chamam a API.

## Modelos

Defina no `.env`. Os IDs precisam bater exatamente com `GET /v1/models`:

- Planner e Builder: `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B`
- Reviewer: não usa modelo (roda pip e pytest)

Sem forma de pagamento na conta Crusoe, cada modelo aceita só 30 requisições por minuto.
Usar modelos diferentes por papel também divide essa carga.
