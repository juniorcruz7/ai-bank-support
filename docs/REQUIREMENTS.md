# Requisitos — ai-bank-support

Assistente de suporte para banco digital com engenharia de prompt versionada.

## Visão geral e escopo

O sistema recebe **um perfil de cliente + um ticket** e gera, com prompts versionados, quatro conteúdos de suporte. Cada execução vira um registro JSON, e duas versões de prompt são comparadas com a rubrica fixa. O foco da avaliação é a engenharia de prompt (40%) e a implementação (30%), não o produto.

**Dentro do escopo**

- CLI em Python (mínimo obrigatório)
- 4 tipos de conteúdo: diagnóstico, passo a passo, perguntas de esclarecimento e fluxograma
- 5 perfis e 6 tickets de exemplo em JSON
- Versionamento de prompt (v1, v2), cache e histórico JSON
- Comparação v1 × v2 com rubrica de 3 critérios, notas e justificativas
- README, PROMPT_ENGINEERING_NOTES.md, /samples, .env.example

**Fora do escopo (ou só como extra)**

- Interface web (diferencial, depois da CLI pronta)
- Deploy em nuvem (ponto extra)
- Integração com sistemas reais de ticket; login de produção (o login com contas cliente/atendente é extra de demonstração, ver Contas e permissões)
- Guardar o raciocínio interno do modelo (o desafio pede explicitamente para não fazer)
- Banco de dados: cortado por fugir do escopo do projeto. O desafio pede o histórico em JSON, que é a persistência usada; um banco só faria diferença no deploy (persistir entre reinícios), e para isso precisaria ser hospedado fora da plataforma, com dependência e segredo novos, sem ganho para o foco da avaliação (engenharia de prompt e implementação)

## Requisitos funcionais

Prioridade **M** = obrigatório pelo desafio, **D** = diferencial.

| ID | Requisito | Origem no PDF | Prioridade |
| --- | --- | --- | --- |
| RF01 | Carregar de 3 a 5 perfis de um JSON (id/nome, nível técnico, estilo, canal) e validar os valores permitidos | 4.1 | M |
| RF02 | Selecionar um ticket de exemplo (JSON) ou digitar um ticket livre | 2, 4.5 | M |
| RF03 | Montar o prompt dinamicamente a partir de perfil + ticket + tipo de conteúdo + versão | 4.2 | M |
| RF04 | Gerar **diagnóstico**: hipóteses, evidências, dados faltantes, conclusão provável | 1 | M |
| RF05 | Gerar **passo a passo** adaptado ao nível técnico e ao estilo | 1 | M |
| RF06 | Gerar **perguntas de esclarecimento** priorizadas (só o necessário para avançar) | 1 | M |
| RF07 | Gerar **resumo visual** em fluxograma ASCII ou árvore de decisão estruturada | 1 | M |
| RF08 | Escolher a versão do prompt (v1, v2…) em cada execução | 4.2, 4.5 | M |
| RF09 | Salvar cada execução no histórico JSON com timestamp, cliente, ticket, tipo, versão, provedor/modelo, configurações e saída | 2, 4.4 | M |
| RF10 | Consultar o cache antes de chamar a API e reutilizar a saída quando a entrada for equivalente | 3, 4.4 | M |
| RF11 | Comparar 2+ versões para o mesmo perfil + ticket, exibindo as saídas lado a lado | 2 | M |
| RF12 | Registrar a rubrica (clareza, adequação, acionabilidade: nota 1–5 + justificativa) junto às execuções comparadas | 5, 4.4 | M |
| RF13 | Mostrar a saída no terminal e o caminho do JSON salvo | 4.5 | M |
| RF14 | Interface web simples (ex.: Streamlit) sobre o mesmo núcleo | 3 | D |
| RF15 | Cada ticket pertence a um cliente (`customer_id`); listar todos os tickets e execuções de um cliente | Própria | D |
| RF16 | Incluir no prompt os tickets anteriores do cliente (até 3, mais recentes, com status e resolução) como contexto. Exceção: um ticket anterior **ainda aberto e com o mesmo texto** do atual fica de fora, porque é a mesma reclamação repetida e não acrescenta informação; assim o prompt fica igual ao do primeiro ticket e a resposta é reaproveitada do cache. Se o anterior já foi resolvido, ele entra, porque a resolução é informação nova | Própria (reforça 4.2 context setting) | D |
| RF17 | Ticket digitado na CLI é anexado ao `tickets.json` com ID sequencial (T007, T008…), `customer_id`, `created_at` e `status: "aberto"`; nenhum ticket é apagado. Cliente inexistente → erro claro, sem ticket órfão | Própria | D |
| RF18 | Fechar ticket = atualizar `status` para `resolvido` e gravar `resolution` (ex.: `close T001 --resolution "..."`); a resolução passa a alimentar o contexto dos próximos pedidos do cliente | Própria | D |

Fluxo básico na CLI: escolher perfil → escolher ticket → escolher tipo (ou "todos") → escolher versão → ver saída + caminho do JSON. O modo comparação roda o mesmo cenário com duas versões e pede as notas da rubrica no final.

## Requisitos não funcionais

| ID | Requisito | Como verificar |
| --- | --- | --- |
| RNF01 | Segredos só no `.env`; `.env.example` sem chaves; `.env` no `.gitignore` | `git log -p` não mostra chave nenhuma |
| RNF02 | Chave de cache = hash SHA-256 de (perfil, ticket normalizado, tipo, versão do prompt, provedor, modelo, temperatura, max_tokens, IDs + hash dos tickets anteriores usados como contexto) | 2ª execução idêntica não chama a API e o log diz "cache hit" |
| RNF03 | Saídas estruturadas validadas por schema (Pydantic); JSON inválido → 1 retry, depois erro claro | Teste com resposta malformada mockada |
| RNF04 | Erros da API tratados: chave ausente, timeout, rate limit (429) com backoff, resposta vazia/bloqueada | Mensagem legível na CLI, sem stack trace cru |
| RNF05 | Entradas validadas: perfil inexistente, nível/estilo/canal fora da lista, ticket vazio ou longo demais | Teste unitário por caso |
| RNF06 | Reprodutibilidade: temperatura fixa e baixa na comparação; modelo e configs registrados em cada execução | Registro no JSON tem `model` e `config` |
| RNF07 | Prompts fora do código (arquivos de template por versão), para editar sem mexer na lógica | Criar v3 = adicionar arquivos, sem mudar Python |
| RNF08 | Provedor de LLM atrás de uma interface única (trocar Gemini/OpenAI/Anthropic sem mexer no resto) | Mock do cliente nos testes |
| RNF09 | Dependências mínimas, listadas no `requirements.txt` com versões fixadas | Instalação limpa só com o README |
| RNF10 | Escrita segura do histórico e do `tickets.json` (escreve em arquivo temporário e renomeia), para não corromper o JSON | Interromper no meio não quebra o arquivo |
| RNF11 | Log simples de cada chamada: latência, tokens (se o provedor informar), cache hit/miss | Extra de observabilidade |
| RNF12 | Ambiente virtual obrigatório: `.venv` na raiz, Python 3.11+, nada instalado no Python global; `.venv/` no `.gitignore`; README com criação e ativação para Linux/macOS e Windows | `python -m venv .venv` + `pip install -r requirements.txt` num clone limpo roda o projeto |

## Modelos de dados

Quatro entidades: perfil, ticket, execução e comparação. A execução segue a estrutura sugerida no PDF e acrescenta `cache_key`, `model`, `config` e `context_ticket_ids`.

**Perfil** (`data/profiles.json`)

```json
{ "id": "C001", "name": "Maria", "technical_level": "leigo", "communication_style": "didatico", "channel": "telefone" }
```

IDs de cliente são opacos e sequenciais (`C001`, `C002`…), no mesmo padrão dos tickets; o nome fica em `name`. O ID nunca muda, mesmo que outros dados do cliente mudem.

Valores permitidos: `technical_level` ∈ {leigo, intermediario, avancado}; `communication_style` ∈ {direto, detalhado, didatico}; `channel` ∈ {chat, email, telefone}.

**Ticket** (`data/tickets.json`)

```json
{ "id": "T001", "customer_id": "C001", "created_at": "2026-11-20T21:05:00-03:00",
  "title": "Pix não chegou", "description": "...",
  "status": "aberto", "resolution": null }
```

**Execução** (um arquivo em `runs/<customer_id>/<ticket_id>/`)

```json
{
  "run_id": "uuid",
  "timestamp": "2026-11-21T15:30:00-03:00",
  "customer_id": "C001",
  "ticket": { "id": "T001", "text": "..." },
  "content_type": "diagnosis",
  "prompt_version": "v2",
  "provider": "gemini",
  "model": "<modelo>",
  "config": { "temperature": 0.2, "max_tokens": 1024 },
  "context_ticket_ids": ["T005"],
  "cache_key": "sha256...",
  "cache_hit": false,
  "output": { },
  "evaluation": null
}
```

O `output` muda por tipo de conteúdo:

| content_type | Campos do output |
| --- | --- |
| diagnosis | `hypotheses[]` (descrição, probabilidade alta/média/baixa), `evidence[]`, `missing_info[]`, `likely_conclusion` |
| resolution_steps | `steps[]` (ordem, ação, resultado esperado, o que fazer se falhar), `escalation_note` |
| clarifying_questions | `questions[]` (pergunta, por que importa, prioridade 1–3) |
| flow_summary | `decision_tree` (nós com condição → próximo passo), renderizado em ASCII pelo código |

**Comparação / avaliação:** a rubrica fica no campo `evaluation` de cada execução comparada, com `clarity`, `customer_fit` e `actionability` (cada um com `score` de 1 a 5 e `justification`), mais um `comparison_id` ligando as duas execuções.

## Dados de exemplo: banco digital

Suporte de um banco digital fictício. Dados 100% fictícios, IDs internos (nada de CPF ou dado real). 5 perfis cobrindo os 3 níveis, os 3 estilos e os 3 canais; 6 tickets ligados a clientes (4 abertos, com informação faltando de propósito, e 2 resolvidos que servem de histórico).

| ID | Nome | Nível | Estilo | Canal | Quem é |
| --- | --- | --- | --- | --- | --- |
| C001 | Maria | leigo | didático | telefone | Aposentada, usa o app só para Pix e extrato |
| C002 | Lucas | leigo | direto | chat | Estudante, quer resolver rápido pelo celular |
| C003 | Carlos | intermediário | direto | chat | Microempreendedor, recebe vendas por Pix |
| C004 | Renata | intermediário | detalhado | e-mail | Contadora, precisa de registro formal para clientes |
| C005 | Ana | avançado | detalhado | e-mail | Desenvolvedora, entende IDs de transação e status |

| ID | Cliente | Data | Status | Ticket (como o cliente escreve) | Faltando de propósito / resolução |
| --- | --- | --- | --- | --- | --- |
| T001 | C001 (Maria) | 20/11/2026 21:05 | aberto | "Fiz um Pix de R$ 350 pro meu filho ontem às 21h, saiu da conta mas ele não recebeu. No app aparece 'em processamento'." | Tipo de chave, ID da transação, banco do destinatário |
| T005 | C001 (Maria) | 15/10/2026 22:10 | resolvido | "Não consigo fazer Pix de noite, dá erro." | Resolução: limite noturno de R$ 200; cliente orientada a ajustar no app |
| T002 | C002 (Lucas) | 18/11/2026 14:30 | aberto | "Meu cartão é recusado em compra internacional, mas no mercado funciona normal." | Cartão físico ou virtual, compras internacionais habilitadas, mensagem de erro |
| T003 | C005 (Ana) | 19/11/2026 09:12 | aberto | "Troquei de celular e o app pede pra validar o aparelho, mas o SMS com o código nunca chega." | Número cadastrado atualizado, operadora, quantas tentativas |
| T004 | C003 (Carlos) | 06/11/2026 10:00 | aberto | "Agendei um Pix pro dia 5 e ele não foi feito. O aluguel atrasou." | Saldo no dia, se o dia 5 caiu em fim de semana, notificação de falha |
| T006 | C003 (Carlos) | 02/09/2026 16:40 | resolvido | "Recebi Pix de cliente e o valor não apareceu no saldo." | Resolução: pagamento caiu na conta PJ, não na PF |

T005 e T006 são histórico: não entram na comparação, só servem de contexto. O T005 é proposital: um Pix às 21h de R$ 350 com limite noturno de R$ 200 é uma hipótese que só aparece se o modelo olhar o histórico.

**Cenário da comparação v1 × v2:** T001 + C001 (Maria: leigo, didático, telefone), o perfil em que uma resposta genérica mais falha.

**Limites da persona neste domínio:** nunca pedir senha, código de SMS ou número completo do cartão; não prometer estorno nem prazo que dependa de análise; orientar a contestar pelo canal oficial (MED do Pix) quando houver suspeita de golpe.

## Estratégia de prompts e versões

Cada prompt = **base comum + bloco do tipo de conteúdo**. A comparação testa uma hipótese só: traduzir o perfil em regras concretas melhora a adequação ao cliente e a acionabilidade.

Camadas do prompt (cobrem os 5 elementos exigidos em 4.2):

1. **Persona** (system): analista de suporte de um banco digital, responsabilidades e limites (não inventar dados do cliente, não prometer prazos, escalar quando faltar informação)
2. **Contexto**: perfil (nível, estilo, canal) + ticket + histórico do cliente, delimitados com tags
3. **Tarefa específica**: um bloco por tipo de conteúdo (diagnóstico decomposto em hipóteses → evidências → faltantes → conclusão; passos; perguntas; fluxo)
4. **Formato de saída**: JSON schema do tipo + restrições (tamanho, vocabulário)
5. **Versão**: identificador gravado no template e no registro da execução

| Aspecto | v1 (baseline) | v2 (proposta) |
| --- | --- | --- |
| Persona | "Você é um assistente de suporte" | Papel + responsabilidades + limites explícitos |
| Perfil | Valores colados ("nível: leigo") | Perfil traduzido em regras **só no passo a passo e nas perguntas** (texto para o cliente): leigo → sem jargão, analogias; telefone → frases curtas para ler em voz alta, ≤ 5 passos. Diagnóstico e fluxograma usam linguagem técnica para o atendente |
| Diagnóstico | "Analise o problema" | Separar o que o ticket diz (evidência) do que é suposição; probabilidade por hipótese |
| Perguntas | "Faça perguntas" | No máximo 3, cada uma ligada a uma hipótese que ela confirma ou descarta |
| Histórico | Bloco colado sem instrução | Evidência fraca: citar quando sustentar hipótese, nunca assumir que é o mesmo problema sem confirmar |
| Saída | Mesmo JSON schema (imposto pelo Gemini e validado com Pydantic), sem restrições no prompt | Mesmo schema + restrições de quantidade, tamanho e vocabulário no prompt |
| Exemplos | Nenhum | 1 exemplo curto (few-shot) só onde o formato é difícil (fluxograma) |

Para a comparação ser justa: mesmo perfil, ticket, histórico, provedor, modelo, temperatura e max_tokens; muda só o template.

**Contexto do histórico do cliente.** O montador busca até 3 tickets anteriores do mesmo `customer_id` (data menor que a do ticket atual, mais recentes primeiro) e injeta título, data, status e resolução num bloco `<historico_cliente>`. Os dados vêm do `tickets.json`, não de saídas geradas, para o contexto ser determinístico.

## Arquitetura e estrutura de pastas

Toda execução passa pelo cache antes de chamar o LLM; só saída validada entra no histórico.

```text
CLI (perfil, ticket, tipo, versão)
  └─> Montador de prompt  <── Templates (prompts/v1, v2) + histórico do cliente
        └─> Cache hit? ──sim──> lê de runs/ ──────────────┐
              └─não─> Cliente LLM (Gemini)                │
                        └─> Validação (Pydantic, 1 retry) │
                              └─> grava em runs/ ─────────┤
                                                          v
                                   CLI mostra saída + caminho do JSON
```

O modo comparação roda esse mesmo fluxo uma vez por versão e grava a rubrica nas duas execuções.

```text
ai-bank-support/
├── main.py                  # CLI (argparse ou menu interativo)
├── app/
│   ├── config.py            # lê .env, modelo, temperatura
│   ├── models.py            # Pydantic: Profile, Ticket, outputs, Evaluation
│   ├── prompt_builder.py    # monta prompt por camadas + versão + histórico
│   ├── generators.py        # 4 funções, uma por tipo de conteúdo
│   ├── llm_client.py        # interface única + implementação Gemini
│   ├── cache.py             # chave SHA-256 + busca em runs/
│   ├── storage.py           # leitura/escrita segura de runs e tickets
│   ├── service.py           # orquestra prompt → cache → LLM → runs/ (e permissões)
│   ├── auth.py              # login, hash de senha, checagem de permissão
│   └── compare.py           # roda v1 × v2 e coleta a rubrica
├── prompts/
│   ├── v1/                  # base + diagnosis, steps, questions, flow
│   └── v2/
├── data/
│   ├── profiles.json
│   ├── users.json
│   └── tickets.json
├── runs/<customer_id>/<ticket_id>/   # 1 JSON por execução (fora do git)
├── samples/                 # JSONs de exemplo versionados
├── tests/
├── .env.example
├── requirements.txt
├── README.md
└── PROMPT_ENGINEERING_NOTES.md
```

## Decisões

| Decisão | Escolha | O que muda no projeto |
| --- | --- | --- |
| Provedor | Gemini | Saída estruturada via schema de resposta do Gemini; `GEMINI_API_KEY` no `.env` |
| Histórico | Um arquivo por execução em `runs/<customer_id>/<ticket_id>/` | Nome do arquivo inclui o `cache_key`; cache acha a execução sem abrir tudo; histórico por pessoa = listar a pasta |
| Fluxograma | Árvore JSON validada, desenhada em ASCII pelo código | O modelo só gera nós e condições; o desenho sai sempre alinhado |
| Ordem dos extras | Interface → deploy → testes | O banco de dados, previsto como último extra, foi cortado por fugir do escopo do projeto (ver Fora do escopo); o JSON é a persistência exigida e a usada |
| Domínio | Banco digital | 5 perfis e 6 tickets; comparação usa T001 + C001 (Maria) |

**Público das saídas:** o sistema é uma ferramenta para responder o cliente, operada pelo atendente. Passo a passo e perguntas de esclarecimento saem como texto pronto para o cliente, no nível, estilo e canal do perfil. Diagnóstico e fluxograma são apoio interno do atendente e podem usar linguagem técnica.

## Contas e permissões

Dois tipos de conta: **cliente** vê só os próprios tickets e recebe passo a passo e perguntas; **atendente** (conta da empresa) vê tudo, de todos os clientes. A regra de acesso fica no núcleo (camada de serviço), não só na tela, para valer igual na CLI e no Streamlit.

| O que | Cliente | Atendente |
| --- | --- | --- |
| Abrir ticket | Só para si mesmo | Para qualquer cliente |
| Passo a passo e perguntas | Dos próprios tickets | De todos |
| Diagnóstico e fluxograma | Não | Sim |
| Histórico de tickets e execuções | Só os próprios tickets e respostas | Todos os clientes, todas as execuções |
| Fechar ticket (RF18) | Não | Sim |
| Comparar versões e dar notas da rubrica | Não | Sim |
| Escolher versão do prompt | Não (usa a versão padrão) | Sim |

| ID | Requisito | Prioridade |
| --- | --- | --- |
| RF19 | Login com usuário e senha; cada conta tem `role` (`customer` ou `agent`); conta de cliente aponta para um `customer_id` | D |
| RF20 | Cliente logado só lista e abre os próprios tickets e só recebe `resolution_steps` e `clarifying_questions` | D |
| RF21 | Atendente logado acessa os 4 tipos de conteúdo, a comparação, a rubrica e o fechamento de tickets | D |
| RF22 | Atendente consulta o histórico completo de qualquer cliente (tickets + execuções em `runs/`) | D |
| RNF13 | Senhas só como hash (`hashlib.pbkdf2_hmac` com salt, sem dependência nova); contas de demonstração em `data/users.json` com senhas fictícias documentadas no README; acesso negado = erro claro, nunca dado vazado | D |
| RNF14 | Toda função do núcleo que lê ticket, execução ou histórico recebe o usuário e checa permissão; teste por regra da tabela acima | D |

**Usuário** (`data/users.json`)

```json
{ "username": "maria", "role": "customer", "customer_id": "C001", "password_hash": "pbkdf2_sha256$..." }
{ "username": "atendente1", "role": "agent", "customer_id": null, "password_hash": "pbkdf2_sha256$..." }
```

O login aparece de verdade no Streamlit (fase de interface). A CLI continua sendo a ferramenta do atendente exigida pelo PDF, com `--user` e senha pedida no terminal. Limitação a documentar: é um controle de acesso de demonstração, sem recuperação de senha, bloqueio por tentativas ou HTTPS próprio.

## Entregáveis e critérios de aceite

- [x] Projeto roda do zero seguindo só o README (testar num clone limpo)
- [x] Nenhuma chave versionada; `.env.example` presente
- [x] `requirements.txt` com as dependências
- [x] 3 a 5 perfis em JSON
- [x] Os 4 tipos de conteúdo gerados pela CLI
- [x] Cache evitando pelo menos 1 chamada repetida (mostrar no log/sample)
- [x] Comparação v1 × v2 no mesmo cenário, registrada no histórico
- [x] 3 notas da rubrica + justificativas para cada versão
- [x] Diretório `/samples` com JSONs de exemplo
- [x] `PROMPT_ENGINEERING_NOTES.md`: estratégias, versões, por que v2 > v1, limitações
- [x] README com setup, execução, arquitetura resumida e exemplos
- [x] Histórico de commits legível (commits pequenos e por etapa)
- [x] Extras: Streamlit, deploy e testes com pytest (banco de dados cortado por fugir do escopo do projeto)

## Convenções de implementação

Estes padrões fecham decisões de implementação que ficariam em aberto; cada um tem um default que pode ser trocado.

| Tema | Padrão |
| --- | --- |
| Idioma | Saídas do modelo e mensagens da CLI em pt-BR; código, variáveis e chaves JSON em inglês |
| Modelo | `GEMINI_MODEL` no `.env`; se vazio, usa o padrão `gemini-2.5-flash`, definido só em `app/config.py`; o README diz qual foi usado nos samples |
| Templates | Arquivos `.md` em `prompts/<versao>/` com placeholders de `string.Template` (`$customer_name`), sem Jinja |
| CLI | `argparse` com subcomandos: `generate --customer --ticket --type {diagnosis,resolution_steps,clarifying_questions,flow_summary,all} --version v1`; `compare --customer --ticket --type --versions v1 v2`; `ticket add --customer --title --description`; `ticket close T001 --resolution "..."`; `history --customer C001` |
| Rubrica | Notas digitadas pelo candidato no fim do `compare` (inteiro de 1 a 5 + justificativa não vazia). LLM-as-judge só como extra, em campo separado `auto_evaluation` |
| Datas | ISO 8601 com fuso America/Recife (`zoneinfo` + pacote `tzdata`, necessário no Windows) |
| Limites de entrada | Ticket entre 10 e 2.000 caracteres; até 3 tickets de histórico no prompt |
| Retentativas | Backoff exponencial, no máximo 3 tentativas por chamada; `--type all` = 4 chamadas |
| Logs | Módulo `logging`, INFO no terminal e em `logs/app.log` (fora do git) |
| Samples | No mínimo: 1 execução v2 de cada tipo, a comparação v1 × v2 com rubrica e 1 execução com `cache_hit: true` |
| Git | Branch por fase (`feat/fase-2-storage`), merge na `main` depois de revisar; conventional commits |
| Qualidade | `ruff` para lint e formatação (dependência só de desenvolvimento) |
| Deploy | Streamlit Community Cloud, chave nos secrets da plataforma, nunca no repositório |
| Ambiente | `.venv` obrigatório (RNF12) |