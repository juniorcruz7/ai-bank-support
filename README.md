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

Todos os comandos abaixo assumem o `.venv` ativado. Todo comando pede login: o usuário vai em `--user` e a senha é pedida no terminal, sem eco. Com a entrada redirecionada (pipe), a senha é lida da primeira linha. Sem os outros argumentos, a CLI abre menus numerados para escolher cliente, ticket, tipo e versão:

```bash
python main.py generate --user atendente
```

### Contas e permissões

Há dois tipos de conta: **atendente**, que vê e faz tudo, e **cliente**, que só acessa os próprios tickets e só recebe o texto voltado para ele. As regras ficam na camada de serviço (`app/service.py` + `app/auth.py`), então valem igual na CLI e na interface web.

| O que | Cliente | Atendente |
| --- | --- | --- |
| Abrir ticket | Só para si mesmo | Para qualquer cliente |
| Passo a passo e perguntas | Dos próprios tickets | De todos |
| Diagnóstico e fluxograma | Não | Sim |
| Histórico | Só os próprios tickets e respostas (sem diagnóstico, fluxograma nem notas da rubrica) | Todos os clientes e execuções |
| Fechar ticket, comparar versões e dar notas | Não | Sim |
| Escolher a versão do prompt | Não (usa a `v2`) | Sim |

**Por que o atendente abre ticket em nome do cliente.** Nem todo problema chega pelo app. O atendente registra no sistema o que chega por outros canais, para que o caso entre no mesmo fluxo:

- **Telefone e e-mail.** O cliente conta o problema e o atendente abre o ticket com o relato dele, por isso o campo se chama "Descrição (como o cliente escreveu)". É o caso da Maria (C001), leiga e atendida por telefone: o passo a passo da `v2` sai em frases curtas para ser lido em voz alta.
- **Cliente sem acesso ao app.** Problemas de acesso impedem o cliente de abrir o ticket sozinho, como no T003: a Ana trocou de celular e o SMS de validação não chega.
- **Problema detectado pelo banco**, registrado antes de o cliente reclamar.

Registrar, em vez de só responder na hora, garante três coisas: o caso e a resolução entram no histórico usado como contexto nos próximos pedidos do cliente; diagnóstico e fluxograma, que só funcionam sobre um ticket, ficam disponíveis ao atendente; e fica o rastro de quando o problema chegou e como foi resolvido. O ticket aberto pelo atendente é igual a um aberto pelo cliente, que o vê na própria conta com o próximo número (nº 1, nº 2…).

**Quem marca um ticket como resolvido.** Só o atendente, com o texto da resolução (web: página Tickets; CLI: `ticket close`). Receber o passo a passo não fecha o ticket, e o cliente não confirma nem reabre: se o problema voltar, abre-se um ticket novo, e o anterior entra como histórico.

Contas de demonstração em `data/users.json`. As senhas são fictícias e só existem para a demonstração; o arquivo guarda apenas o hash PBKDF2-SHA256 com salt.

| Usuário | Senha | Conta |
| --- | --- | --- |
| `atendente` | `atendente-demo` | atendente |
| `maria` | `maria-demo` | cliente C001 |
| `lucas` | `lucas-demo` | cliente C002 |
| `carlos` | `carlos-demo` | cliente C003 |
| `renata` | `renata-demo` | cliente C004 |
| `ana` | `ana-demo` | cliente C005 |

Para criar uma conta, gere o hash com `python -m app.auth` (pede a senha duas vezes) e adicione o registro ao `data/users.json`.

### Gerar conteúdo

```bash
python main.py generate --user atendente --customer C001 --ticket T001 --type diagnosis --version v2
python main.py generate --user atendente --ticket T001 --type all   # os 4 tipos; o cliente vem do ticket
python main.py generate --user maria --ticket T001 --type all       # cliente: só passo a passo e perguntas
```

- `--type`: `diagnosis`, `resolution_steps`, `clarifying_questions`, `flow_summary` ou `all` (para a conta de cliente, `all` são os 2 tipos permitidos).
- `--version`: pasta em `prompts/` (padrão `v2`; só atendente).

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
python main.py compare --user atendente --customer C001 --ticket T001 --type resolution_steps --versions v1 v2
```

Roda o mesmo cenário em cada versão (mesmo perfil, ticket, histórico, modelo, `max_tokens` e temperatura fixa de 0.2; muda só o template), mostra as saídas lado a lado e pede, para cada versão, uma nota de 1 a 5 com justificativa em três critérios: **clareza**, **adequação ao cliente** e **acionabilidade**. As notas são gravadas no campo `evaluation` de cada execução, ligadas por um `comparison_id`. Para informar as notas por arquivo: `--rubric notas.json` (formato em [samples/README.md](samples/README.md)).

### Abrir um ticket novo

```bash
python main.py ticket add --user atendente --customer C003 --title "Maquininha" --description "A maquininha não conecta desde ontem."
```

O ticket é anexado a `data/tickets.json` com o próximo ID (T007, T008…), `status: "aberto"` e data/hora. No `generate` interativo também existe a opção "Digitar um novo ticket".

### Fechar um ticket

```bash
python main.py ticket close T001 --user atendente --resolution "Pix estornado pelo banco de destino."
```

Marca o ticket como `resolvido` e grava a `resolution` em `data/tickets.json` (escrita atômica; ticket inexistente, já resolvido ou resolução vazia dão erro e nada é gravado). A resolução passa a entrar no histórico dos próximos tickets do cliente e, como o conteúdo dos tickets de contexto faz parte da chave de cache, as gerações que dependem dele são refeitas. Sem argumentos, a CLI lista os tickets abertos e pede a resolução.

### Histórico de um cliente

```bash
python main.py history --user atendente --customer C001
python main.py history --user maria                  # cliente: o próprio histórico
```

Lista os tickets do cliente (mais recentes primeiro, com a resolução quando houver) e as execuções gravadas em `runs/` para cada ticket: data, tipo, versão, se veio do cache, nota da rubrica e caminho do JSON.

### Interface web (Streamlit)

```bash
streamlit run streamlit_app.py
```

Abre no navegador (padrão `http://localhost:8501`) uma interface sobre o mesmo núcleo da CLI, com as mesmas regras de cache, histórico e validação:

- **Gerar conteúdo**: cliente, ticket, um ou mais tipos e versão do prompt; mostra o histórico usado como contexto, a saída, o caminho do JSON, a latência e os tokens.
- **Comparar versões**: saídas lado a lado e formulário da rubrica (nota de 1 a 5 e justificativa por critério), gravada nas execuções como no `compare`.
- **Histórico do cliente**: tickets e execuções gravadas, com o JSON de cada uma.
- **Tickets**: abrir um ticket novo e fechar um ticket aberto com a resolução.

A interface abre numa tela de login (mesmas contas da CLI), com uma aba que lista as contas de demonstração. O atendente vê as 4 páginas acima. O cliente vê uma interface própria, sem escolher cliente nem versão: **Ajuda com um ticket** (passo a passo e perguntas), **Meu histórico** (os próprios tickets e as respostas já recebidas, sem dados técnicos) e **Abrir ticket**. Para o cliente, os tickets aparecem numerados na conta dele (nº 1, nº 2…, por ordem de abertura); o ID interno continua global e sequencial (T007, T008…), como pede o RF17. A chave continua vindo do `.env`.

## Deploy (Streamlit Community Cloud)

A interface web roda no [Streamlit Community Cloud](https://share.streamlit.io) direto do repositório do GitHub, sem servidor próprio. A chave do Gemini fica nos *secrets* da plataforma, nunca no repositório.

**Passo a passo**

1. Entre em [share.streamlit.io](https://share.streamlit.io) com a conta do GitHub que tem acesso de administrador ao repositório.
2. **Create app** → **Yup, I have an app** e preencha:
   - **Repository:** `juniorcruz7/ai-bank-support`
   - **Branch:** `main` (a branch onde as fases foram mescladas)
   - **Main file path:** `streamlit_app.py`
   - **App URL** (opcional): o endereço público do app.
3. Abra **Advanced settings**:
   - **Python version:** `3.13`, a versão usada no desenvolvimento e nos testes. O padrão da plataforma é 3.12, que também atende o requisito (3.11+).
   - **Secrets:** cole a chave em formato TOML:

     ```toml
     GEMINI_API_KEY = "sua-chave-aqui"
     # opcionais, como no .env:
     # GEMINI_MODEL = "gemini-2.5-flash"
     # GEMINI_TEMPERATURE = "0.2"
     # GEMINI_MAX_TOKENS = "2048"
     ```
4. **Deploy**. A plataforma instala o `requirements.txt` da raiz (o `pyproject.toml` só guarda a configuração do ruff, e o `requirements.txt` tem prioridade sobre ele) e abre a tela de login.

Os secrets podem ser trocados depois em **Settings → Secrets**, sem novo deploy do código. Na inicialização, o app copia `GEMINI_API_KEY` e as opcionais de `st.secrets` para variáveis de ambiente; o núcleo continua lendo só o ambiente/`.env` (`app/config.py`), sem depender do Streamlit. Se a variável já existir no ambiente, ela tem prioridade. Para testar os secrets localmente, use `.streamlit/secrets.toml`, que está no `.gitignore`.

**Como o app se comporta na nuvem**

- **Os dados não são persistentes.** Tickets abertos, tickets fechados e execuções em `runs/` são gravados no disco do servidor, que não é garantido entre reinícios. A cada novo deploy ou reinício, o app volta aos dados de exemplo do repositório. A persistência de verdade fica para a fase de banco de dados.
- **Os dados são compartilhados.** Todos os visitantes usam o mesmo `tickets.json`: um ticket aberto ou fechado por alguém aparece para todos. Para restaurar os dados de exemplo, reinicie o app em **Manage app → Reboot app**.
- **O app dorme** depois de 12 horas sem acesso e acorda na próxima visita, que demora um pouco mais.

**Custo e acesso**

As contas de demonstração aparecem na tela de login, então qualquer pessoa com o link pode gerar conteúdo e gastar a cota da chave. Para limitar isso:

- Use uma chave de um projeto do Google AI Studio **sem faturamento ativado** (nível gratuito): ao bater no limite, a API recusa novas chamadas (erro 429, que o app mostra como mensagem legível) em vez de cobrar.
- Se a chave vazar ou for abusada, gere outra no AI Studio e troque em **Settings → Secrets**.
- Para restringir quem acessa, deixe o app privado em **Settings → Sharing** ("Only specific people can view this app") e convide os avaliadores por e-mail. A conta gratuita permite um app privado por vez.

## Arquitetura

```text
CLI (main.py) / interface web (streamlit_app.py)
  └─> service.run_content
        ├─> prompt_builder: templates de prompts/<versão>/ + perfil + ticket + histórico do cliente
        ├─> cache: chave SHA-256 → já existe em runs/? ──sim──> reaproveita (cache hit)
        └─não─> generators → llm_client (Gemini) → validação Pydantic (1 retry) 
                  └─> storage: grava runs/<cliente>/<ticket>/<...>.json (escrita atômica)
```

| Módulo | Responsabilidade |
| --- | --- |
| `main.py` | CLI: subcomandos `generate`, `compare`, `ticket add`, `ticket close` e `history`, menus e mensagens de erro |
| `streamlit_app.py` | Interface web: geração, comparação com rubrica, histórico e tickets |
| `app/runtime.py` | Cliente LLM e logging compartilhados pela CLI e pela interface |
| `app/auth.py` | Hash e verificação de senha (PBKDF2), login e regras de permissão |
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

**Tickets repetidos.** A chave não inclui o ID do ticket, e a busca no cache olha todos os tickets do mesmo cliente. Se um cliente abre um segundo ticket com o mesmo texto de um anterior ainda aberto, o ticket anterior fica fora do histórico, porque é a mesma reclamação repetida e não acrescenta informação. A chave fica igual e a resposta já gerada é reaproveitada sem chamar a API; a execução é gravada na pasta do ticket novo, com `cache_hit: true`. Se o ticket anterior já foi **resolvido**, ele entra no histórico, porque a resolução é informação nova, e a resposta é gerada de novo. O cache nunca é compartilhado entre clientes: a chave inclui o perfil e a busca fica na pasta do cliente.

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

- O login é um controle de acesso de demonstração: contas num JSON, sem recuperação de senha, sem bloqueio por tentativas e sem HTTPS próprio. Num produto real, a identidade viria de um provedor de autenticação.
- O ticket não registra quem o abriu nem por qual canal: um ticket aberto pelo atendente em nome do cliente é igual a um aberto pelo próprio cliente. Foi uma escolha para manter o modelo de dados simples; num produto real, esse registro seria necessário para auditoria.
- O cliente não confirma a solução nem reabre um ticket: o fechamento é decisão do atendente.
- A avaliação da rubrica é manual e feita sobre poucos cenários.
- Mais detalhes e próximos passos em [PROMPT_ENGINEERING_NOTES.md](PROMPT_ENGINEERING_NOTES.md#limitações-e-próximos-passos).

## Ferramentas utilizadas

- Python 3, Pydantic, python-dotenv e o SDK `google-genai` (modelo `gemini-2.5-flash`).
- Streamlit para a interface web e Streamlit Community Cloud para o deploy.
- pytest para os testes.
- O Claude (Anthropic) foi usado como auxílio na parte de código.
