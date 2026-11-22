# ai-bank-support

Assistente de suporte para um banco digital fictício, construído com **engenharia de prompt versionada**. A partir de um **perfil de cliente** e de um **ticket**, o sistema gera quatro conteúdos de suporte com o Gemini, valida cada saída com Pydantic, guarda tudo num histórico JSON com cache e permite comparar versões de prompt (v1 × v2) com uma rubrica.

| Conteúdo | Para quem | O que traz |
| --- | --- | --- |
| `diagnosis` | Atendente | Hipóteses com probabilidade, evidências, dados faltantes e conclusão provável |
| `resolution_steps` | Cliente | Passo a passo adaptado ao nível técnico, ao estilo e ao canal do cliente |
| `clarifying_questions` | Cliente | Perguntas priorizadas, só as necessárias para avançar |
| `flow_summary` | Atendente | Árvore de decisão desenhada em ASCII pelo código |

A estratégia de prompt, a comparação entre versões e as limitações estão em [PROMPT_ENGINEERING_NOTES.md](PROMPT_ENGINEERING_NOTES.md).

## Setup do zero

Requisitos: **Python 3.11+** e uma chave da API do Gemini (gratuita em [Google AI Studio](https://aistudio.google.com/apikey)).

**1. Clonar e criar o ambiente virtual** (nada é instalado no Python global)

Windows (PowerShell):

```powershell
git clone https://github.com/juniorcruz7/ai-bank-support.git
cd ai-bank-support
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> Se o PowerShell bloquear o script de ativação, rode antes `Set-ExecutionPolicy -Scope Process RemoteSigned`, ou pule a ativação e use `.venv\Scripts\python` no lugar de `python` em todos os comandos.

Linux / macOS:

```bash
git clone https://github.com/juniorcruz7/ai-bank-support.git
cd ai-bank-support
python3 -m venv .venv
source .venv/bin/activate
```

**2. Instalar as dependências** (versões fixadas)

```bash
pip install -r requirements.txt        # para rodar
pip install -r requirements-dev.txt    # para rodar os testes (inclui pytest)
```

**3. Configurar a chave**

Copie `.env.example` para `.env` e preencha `GEMINI_API_KEY`. O `.env` está no `.gitignore` e nunca é versionado.

```ini
GEMINI_API_KEY=sua-chave-aqui
GEMINI_MODEL=gemini-2.5-flash     # opcional (esse é o padrão)
GEMINI_TEMPERATURE=               # opcional (padrão 0.2)
GEMINI_MAX_TOKENS=                # opcional (padrão 2048)
```

## Como usar

Todos os comandos abaixo assumem o `.venv` ativado. Sem argumentos, a CLI abre menus numerados para escolher cliente, ticket, tipo e versão:

```bash
python main.py generate
```

### Gerar conteúdo

```bash
python main.py generate --customer C001 --ticket T001 --type diagnosis --version v2
python main.py generate --ticket T001 --type all            # os 4 tipos; o cliente vem do ticket
```

- `--type`: `diagnosis`, `resolution_steps`, `clarifying_questions`, `flow_summary` ou `all`.
- `--version`: pasta em `prompts/` (padrão `v2`).

A saída aparece formatada no terminal, seguida do caminho do JSON salvo. Exemplo real de fluxograma (T001, cliente Maria, v2):

```text
=== Fluxograma · T001 · Maria · v2 ===

[n1] O Pix foi feito para a chave correta?
 +-- sim: [n2] O status da transação é 'em processamento'?
 |    +-- sim: [n4] (ação) Informar que o Pix pode levar até 1 hora para ser processado
 |    `-- não: [n5] O Pix foi estornado para a conta de origem?
 |         +-- sim: [n6] (ação) Informar que o valor foi estornado e está disponível na conta
 |         `-- não: [n7] (ação) Escalar para a equipe de suporte técnico para análise interna
 `-- não: [n3] (ação) Orientar o cliente a verificar o comprovante e a chave Pix

JSON salvo em: runs\C001\T001\20261122T145258_flow_summary_v2_9a7a2365_691ad6efac2658e8.json (gerado agora)
```

### Comparar versões e registrar a rubrica

```bash
python main.py compare --customer C001 --ticket T001 --type resolution_steps --versions v1 v2
```

Roda o mesmo cenário em cada versão (mesmo perfil, ticket, histórico, modelo, `max_tokens` e temperatura fixa de 0.2; muda só o template), mostra as saídas lado a lado e pede, para cada versão, uma nota de 1 a 5 com justificativa em três critérios: **clareza**, **adequação ao cliente** e **acionabilidade**. As notas são gravadas no campo `evaluation` de cada execução, ligadas por um `comparison_id`. Para informar as notas por arquivo: `--rubric notas.json` (formato em [samples/README.md](samples/README.md)).

### Abrir um ticket novo

```bash
python main.py ticket add --customer C003 --title "Maquininha" --description "A maquininha não conecta desde ontem."
```

O ticket é anexado a `data/tickets.json` com o próximo ID (T007, T008…), `status: "aberto"` e data/hora. No `generate` interativo também existe a opção "Digitar um novo ticket".

## Arquitetura

```text
CLI (main.py)
  └─> service.run_content
        ├─> prompt_builder: templates de prompts/<versão>/ + perfil + ticket + histórico do cliente
        ├─> cache: chave SHA-256 → já existe em runs/? ──sim──> reaproveita (cache hit)
        └─não─> generators → llm_client (Gemini) → validação Pydantic (1 retry) 
                  └─> storage: grava runs/<cliente>/<ticket>/<...>.json (escrita atômica)
```

| Módulo | Responsabilidade |
| --- | --- |
| `main.py` | CLI: subcomandos `generate`, `compare` e `ticket add`, menus e mensagens de erro |
| `app/config.py` | Lê o `.env` (chave, modelo, temperatura, `max_tokens`) |
| `app/models.py` | Modelos Pydantic: perfil, ticket, as 4 saídas, execução e rubrica |
| `app/prompt_builder.py` | Monta o prompt em camadas a partir dos templates da versão |
| `app/llm_client.py` | Interface única de LLM e implementação Gemini (schema JSON, backoff, erros) |
| `app/generators.py` | Chama o LLM e valida a saída, com 1 retry em caso de JSON inválido |
| `app/cache.py` | Chave de cache e busca de execuções anteriores |
| `app/service.py` | Orquestra prompt → cache → LLM → histórico |
| `app/compare.py` | Comparação entre versões e gravação da rubrica |
| `app/render.py` | Formatação no terminal, fluxograma ASCII e colunas lado a lado |
| `app/storage.py` | Leitura e escrita atômica de perfis, tickets e execuções |
| `prompts/v1`, `prompts/v2` | Templates de cada versão; criar uma v3 é só adicionar uma pasta |

## Cache e histórico

Cada execução vira um JSON em `runs/<customer_id>/<ticket_id>/` (fora do git) com timestamp, cliente, ticket, tipo, versão, provedor, modelo, configuração, tickets usados como contexto, chave de cache, uso de tokens e a saída validada.

A chave de cache é um SHA-256 de: perfil, ticket (com espaços normalizados), tipo, versão, **hash dos templates usados**, provedor, modelo, configuração e o conteúdo dos tickets anteriores usados como contexto. Então editar um template ou a resolução de um ticket antigo invalida só o que depende dele. Uma segunda execução idêntica não chama a API:

```text
INFO app.service: cache miss: T001 resolution_steps v2 (chave eab1dc219df0); chamando gemini/gemini-2.5-flash
INFO app.service: LLM ok: T001 resolution_steps v2 em 2959 ms, tokens entrada/saída 850/484, chamadas 1
INFO app.service: cache hit: T001 resolution_steps v2 (chave eab1dc219df0)
```

O log aparece no terminal e é gravado em `logs/app.log` (fora do git).

## Dados de exemplo

Dados 100% fictícios em `data/`. Os 5 perfis cobrem os 3 níveis técnicos, os 3 estilos e os 3 canais:

| ID | Nome | Nível | Estilo | Canal |
| --- | --- | --- | --- | --- |
| C001 | Maria | leigo | didático | telefone |
| C002 | Lucas | leigo | direto | chat |
| C003 | Carlos | intermediário | direto | chat |
| C004 | Renata | intermediário | detalhado | e-mail |
| C005 | Ana | avançado | detalhado | e-mail |

São 6 tickets: 4 abertos (com informação faltando de propósito) e 2 resolvidos, que entram como histórico no prompt dos pedidos seguintes do mesmo cliente. O cenário da comparação é **T001 + C001**: um Pix de R$ 350 feito às 21h que não chegou, para uma cliente leiga atendida por telefone, que já teve um problema com o limite noturno de Pix (T005).

A pasta [samples/](samples/) tem execuções reais de exemplo, incluindo um cache hit e a comparação com rubrica.

## Testes

```bash
python -m pytest
```

Os testes não precisam de chave nem de rede: o Gemini é substituído por um cliente falso que implementa a mesma interface.

Lint e formatação com [ruff](https://docs.astral.sh/ruff/) (configuração em `pyproject.toml`):

```bash
ruff check .
ruff format --check .
```

## Limitações

- O login de produção está fora do escopo: a CLI é a ferramenta do atendente, que escolhe o cliente.
- A avaliação da rubrica é manual e feita sobre poucos cenários.
- Mais detalhes e próximos passos em [PROMPT_ENGINEERING_NOTES.md](PROMPT_ENGINEERING_NOTES.md#limitações-e-próximos-passos).

## Ferramentas utilizadas

- Python 3, Pydantic, python-dotenv e o SDK `google-genai` (modelo `gemini-2.5-flash`).
- pytest para os testes.
- O Claude (Anthropic) foi usado como auxílio na parte de código.
