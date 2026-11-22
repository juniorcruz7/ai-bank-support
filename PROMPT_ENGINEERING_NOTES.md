# Prompt Engineering Notes

Notas sobre como os prompts foram desenhados, o que muda entre as versões, o que a comparação mostrou e quais são as limitações conhecidas.

## 1. Estratégia

### Prompt em camadas

Cada prompt é montado pelo código (`app/prompt_builder.py`) a partir de arquivos de template em `prompts/<versão>/`, sem texto de prompt dentro do Python. São 5 camadas:

| Camada | Arquivo | Papel |
| --- | --- | --- |
| 1. Persona | `system.md` | Vai como *system instruction*: papel, responsabilidades e limites |
| 2. Contexto | `context.md` | Perfil, ticket e histórico do cliente, delimitados por tags (`<perfil>`, `<ticket>`, `<historico_cliente>`) |
| 3. Tarefa | `<tipo>.md` | Um bloco por tipo de conteúdo (diagnóstico, passos, perguntas, fluxo) |
| 4. Formato | `<tipo>.md` + schema | Campos esperados e restrições; o JSON schema é imposto pelo Gemini e validado com Pydantic |
| 5. Versão | nome da pasta | Gravado em cada execução (`prompt_version`) e no hash dos templates (`template_hash`) |

Criar uma v3 é só criar `prompts/v3/` com os mesmos arquivos; nenhuma linha de Python muda.

### Público de cada conteúdo

O sistema é uma ferramenta do atendente para responder o cliente. Por isso:

- **Passo a passo e perguntas** saem como texto pronto para o cliente e seguem as regras de comunicação do perfil.
- **Diagnóstico e fluxograma** são apoio interno do atendente e usam linguagem técnica.

Aplicar as regras do perfil só onde o cliente lê evita, por exemplo, um diagnóstico "simplificado" demais para o atendente.

### O perfil vira regras, não rótulos

Na v1 o perfil entra cru (`nível técnico: leigo`). Na v2, cada valor é traduzido numa regra concreta, definida em `prompts/v2/profile_rules.json` (fora do código):

| Valor | Regra injetada no prompt |
| --- | --- |
| `leigo` | Sem jargão técnico; termos do app explicados com palavras do dia a dia ou analogia |
| `didatico` | Uma coisa por vez, com exemplos concretos; confirmar o entendimento no fim |
| `telefone` | Texto lido em voz alta: frases curtas, sem links ou símbolos; no máximo 5 passos |

A hipótese testada pela comparação é esta: **regras concretas adaptam melhor a resposta ao cliente do que rótulos.**

### Schema igual nas duas versões

As duas versões usam o mesmo JSON schema estrutural (imposto pelo Gemini via `response_schema` e validado com Pydantic). As restrições de quantidade e estilo, como "no máximo 3 perguntas" ou "no máximo 5 passos", ficam **só no prompt da v2**. Se ficassem no schema, a v1 ganharia essas regras de graça e a comparação perderia o sentido.

### Proteções no prompt (v2)

- **Limites do domínio** na persona: nunca pedir senha, código SMS ou número completo do cartão; não inventar dados; não prometer estorno nem prazo; MED do Pix em suspeita de golpe; escalar quando faltar informação.
- **Texto do cliente é dado, não instrução**: o `context.md` avisa que o conteúdo de `<ticket>` foi escrito pelo cliente. É uma defesa simples contra instruções embutidas no ticket (*prompt injection*).
- **Histórico como evidência fraca**: citar um atendimento anterior só quando sustentar ou enfraquecer uma hipótese, sem assumir que é o mesmo problema.
- **Isolamento no código, não no prompt**: o histórico injetado é filtrado pelo `customer_id` antes de montar o prompt, e um ticket de outro cliente é recusado. O modelo nunca recebe dados de outro cliente, então não há o que vazar.

## 2. v1 × v2

| Aspecto | v1 (baseline) | v2 (proposta) |
| --- | --- | --- |
| Persona | "Você é um assistente de suporte." | Papel, responsabilidades e limites explícitos do domínio |
| Perfil | Valores colados | Regras concretas, só no passo a passo e nas perguntas |
| Diagnóstico | "Analise o problema" | Hipóteses → evidências (só fatos) → faltantes → conclusão, com probabilidade |
| Perguntas | "Faça perguntas" | No máximo 3, cada uma ligada a uma hipótese que confirma ou descarta |
| Histórico | Bloco sem instrução | Evidência fraca, com instrução de uso |
| Saída | Mesmo schema, sem restrições no prompt | Mesmo schema + restrições de quantidade, tamanho e vocabulário |
| Exemplos | Nenhum | 1 exemplo curto (*few-shot*) só no fluxograma, onde o formato é mais difícil |

Para a comparação ser justa, tudo é igual entre as versões: perfil, ticket, histórico, provedor, modelo, `max_tokens` e temperatura (fixa em 0.2 no modo `compare`). Só o template muda.

## 3. Resultados

### Rubrica

Cenário: **T001 + C001** (Maria: leiga, didática, telefone), tipo `resolution_steps`.

> **TODO (autor):** rodar `python main.py compare --customer C001 --ticket T001 --type resolution_steps` e preencher a tabela com as notas e justificativas digitadas.

| Critério | v1 | v2 | Justificativa |
| --- | --- | --- | --- |
| Clareza | TODO | TODO | TODO |
| Adequação ao cliente | TODO | TODO | TODO |
| Acionabilidade | TODO | TODO | TODO |
| **Total** | TODO | TODO | |

### Por que a v2 é melhor

> **TODO (autor):** conclusão a partir das notas. Pontos observados que podem sustentá-la:

Observações de uma execução real do passo a passo (T001 + C001, mesma configuração):

| | v1 | v2 |
| --- | --- | --- |
| Passos | 5 | 4 (respeita o limite de 5 do canal telefone) |
| Linguagem | Mais técnica ("seção Meus Limites Pix", "status Falha/Cancelado") | Simples, uma ação por passo, pronta para ler em voz alta |
| Prazo prometido | Sim ("até 30 minutos", "mais de 1 hora") | Não |
| Uso do histórico (T005) | Sim: sugere verificar o limite noturno | Não citou o limite noturno |
| Tokens de entrada / saída | 221 / 696 | 850 / 474 |

A v2 ganhou em adequação ao cliente e em respeito aos limites do domínio, com uma resposta mais curta. O prompt maior (~4× mais tokens de entrada) é o custo dessa adaptação.

### Falhas encontradas na v2

As execuções reais mostraram pontos em que a v2 ainda não faz o que o prompt pede:

1. **Ignorou uma pista útil do histórico.** No passo a passo, a v1 associou o T001 ao limite noturno do T005 (Pix de R$ 350 às 21h contra limite de R$ 200) e a v2 não. A instrução "o histórico é evidência fraca" parece ter inibido a hipótese em vez de apenas controlar o peso dela.
2. **Perguntou o que já estava no ticket.** As perguntas da v2 pediram "data e hora" e "qual status aparece", e o ticket já dizia "ontem às 21h" e "em processamento", apesar da regra "não pergunte o que já está no ticket".
3. **Prometeu prazo no fluxograma.** Uma ação diz "o Pix pode levar até 1 hora", contra o limite "não prometer prazo".
4. **MED fora do lugar.** Uma versão do fluxograma indicou o MED do Pix para "chave errada"; o MED é para golpe ou fraude.

São regras escritas no prompt que o modelo nem sempre segue, o que motiva os próximos passos abaixo.

## 4. Decisões técnicas que afetam o prompt

| Decisão | Motivo |
| --- | --- |
| Saída estruturada com `response_schema` + validação Pydantic | Garante JSON parseável e desenhável (fluxograma); validações semânticas (árvore sem ciclo, raiz `n1`, nós alcançáveis) ficam no Pydantic |
| 1 retry com o erro de validação no prompt (`retry.md`) | O modelo recebe o motivo exato da falha; se falhar de novo, erro claro e nada é gravado |
| Pensamento do modelo desligado (`thinking_budget=0`) | Saída mais previsível, menor custo, e nenhum raciocínio interno guardado; a decomposição do diagnóstico já está no prompt |
| Temperatura 0.2 (fixa na comparação) | Reprodutibilidade; diferenças entre v1 e v2 vêm do template, não do acaso |
| `max_tokens` 2048 | As respostas reais usaram de 176 a 696 tokens; resposta cortada gera erro explícito |
| API `v1beta` do Gemini | A `v1` (padrão do SDK) recusa *system instruction*, que é a camada de persona |
| Hash dos templates na chave de cache | Editar um template invalida só o cache que depende dele |

## 5. Limitações e próximos passos

**Limitações**

- **Avaliação pequena e manual.** A rubrica foi aplicada a um cenário (T001 + C001) e uma execução por versão; com temperatura 0.2 ainda há variação entre execuções. Não é uma medida estatística.
- **Regras no prompt não são garantias.** Como mostram as falhas acima, o modelo às vezes ignora regras escritas (perguntar o que já se sabe, prometer prazo).
- **Identidade e acesso.** Aqui o atendente escolhe o cliente na CLI. Num produto real, o `customer_id` viria da sessão autenticada (login no app, telefone verificado, e-mail confirmado), ligado a identificadores externos numa tabela de identidades, nunca do texto digitado. O filtro de histórico por cliente já está no código, mas não há autenticação.
- **Dados fictícios e domínio simplificado.** Não há integração com sistemas reais de Pix ou cartão; o modelo não consulta status de transação.

**Próximos passos**

- **v3** com: instrução explícita para considerar hipóteses sugeridas pelo histórico e pedir confirmação antes de afirmar; checagem de "isso já está no ticket?" antes de cada pergunta; e exemplos negativos de prazo prometido.
- **Mais cenários na comparação** (um por perfil) e várias execuções por versão, para comparar médias.
- **Avaliação automática como apoio** (*LLM-as-judge*) em campo separado (`auto_evaluation`), sem substituir a nota humana.
- **Checagens automáticas** de regras objetivas: número de passos por canal, presença de prazo em minutos/horas, pedido de senha ou código SMS.
