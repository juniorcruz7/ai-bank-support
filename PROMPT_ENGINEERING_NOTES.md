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

Uma versão nova é uma pasta nova em `prompts/` com os mesmos arquivos. Se a mudança for só de texto, nenhuma linha de Python muda. A v3 foi a exceção controlada: recusar um ticket fora do escopo exige um formato de saída que antes não existia, então ela trouxe um campo novo no schema (ver seção 3, "v3: ticket fora do escopo").

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

### Schema igual em todas as versões

Todas as versões usam o mesmo JSON schema estrutural (imposto pelo Gemini via `response_schema` e validado com Pydantic). As restrições de quantidade e estilo, como "no máximo 3 perguntas" ou "no máximo 5 passos", ficam **só no prompt da v2**. Se ficassem no schema, a v1 ganharia essas regras de graça e a comparação perderia o sentido. O mesmo vale para o campo `out_of_scope` da v3: ele existe no schema de todas as versões, é opcional e só o prompt da v3 explica quando usá-lo.

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

Cenário: **T001 + C001** (Maria: leiga, didática, telefone), tipo `resolution_steps`, comando `python main.py compare --user atendente --customer C001 --ticket T001 --type resolution_steps`. As execuções avaliadas estão em [samples/06](samples/06_compare_resolution_steps_v1_rubric.json) e [samples/07](samples/07_compare_resolution_steps_v2_rubric.json) (mesmo `comparison_id`). A saída da v2 veio do cache: é a mesma geração de uma execução anterior com entrada e configuração idênticas.

| Critério | v1 | v2 |
| --- | --- | --- |
| Clareza | 3 | 4 |
| Adequação ao cliente | 2 | 5 |
| Acionabilidade | 3 | 2 |
| **Total** | **8** | **11** |

Justificativas registradas:

**v1**

- **Clareza (3):** Os passos são organizados, mas longos, e o texto mistura dois leitores: fala com a cliente ("sua conta") e, no passo 5, com o atendente ("Oriente a cliente"). O "Quando escalar" é um resumo do caso, não uma regra de quando escalar.
- **Adequação ao cliente (2):** Não é didático para uma leiga ao telefone: usa termos como "compensação" e "horários de pico" e não diz onde tocar no app. Pior: manda "tentar novamente" sem confirmar o status, o que pode gerar um Pix em duplicidade.
- **Acionabilidade (3):** Chega à causa mais provável. O passo 5 usa o histórico do limite noturno (T005), que era a pegadinha do cenário. Mas a orientação de refazer o Pix é arriscada, e o "aguarde 30 minutos" promete um prazo que a persona não deveria prometer.

**v2**

- **Clareza (4):** Muito clara, linear e sempre falando com a cliente. Perde um ponto por inventar nomes de menu ("Meu Dinheiro") que o modelo não tem como saber, o que pode confundir se o app não tiver essa opção.
- **Adequação ao cliente (5):** Tem exatamente o tom de perfil leigo, didático e por telefone: explica onde tocar, dá contexto ("É onde ficam suas movimentações"), usa 4 passos e evita jargão. Dá para ler em voz alta.
- **Acionabilidade (2):** Termina em "identifique o status" e não diz o que fazer com cada status. Ignora o histórico do limite noturno, que era a causa mais provável, e não avisa para não refazer o Pix. A cliente descobre o estado, mas não sabe o próximo passo.

Dados objetivos das duas saídas:

| | v1 | v2 |
| --- | --- | --- |
| Passos | 5 | 4 (respeita o limite de 5 do canal telefone) |
| Com quem fala | Mistura cliente e atendente ("Oriente a cliente") | Sempre com a cliente |
| Prazo prometido | Sim ("mais de 30 minutos") | Não |
| Orientação arriscada | "Tentar novamente" sem confirmar o status (risco de Pix duplicado) | Nenhuma |
| Uso do histórico (T005) | Sim: verificar o limite noturno | Não |
| Tokens de entrada / saída | 221 / 664 | 850 / 484 |

### Por que a v2 é melhor (e onde ainda não é)

A v2 venceu por **11 a 8**, e a diferença está exatamente onde a hipótese da v2 apostava: traduzir o perfil em regras concretas melhorou a **adequação ao cliente** (2 → 5) e a **clareza** (3 → 4). O texto fala só com a cliente, cabe no limite de passos do telefone, evita jargão e não promete prazo nem manda refazer o Pix. São efeitos diretos das regras de perfil e dos limites da persona, que a v1 não tem.

A v2, porém, perdeu em **acionabilidade** (3 → 2). Ela ficou boa em *como* falar, mas parou cedo: leva a cliente até o status do Pix e não diz o que fazer com ele, e ignorou o histórico do limite noturno que a v1 usou. A provável causa é a instrução "o histórico é evidência fraca", que inibiu a hipótese em vez de só controlar o peso dela, somada à falta de uma regra que exija um próximo passo para cada resultado possível.

O custo da v2 é um prompt ~4× maior em tokens de entrada (850 contra 221), com uma resposta mais curta.

### Falhas encontradas na v2

As execuções reais mostraram pontos em que a v2 ainda não faz o que o prompt pede:

1. **Ignorou uma pista útil do histórico.** No passo a passo, a v1 associou o T001 ao limite noturno do T005 (Pix de R$ 350 às 21h contra limite de R$ 200) e a v2 não. A instrução "o histórico é evidência fraca" parece ter inibido a hipótese em vez de apenas controlar o peso dela.
2. **Perguntou o que já estava no ticket.** As perguntas da v2 pediram "data e hora" e "qual status aparece", e o ticket já dizia "ontem às 21h" e "em processamento", apesar da regra "não pergunte o que já está no ticket".
3. **Prometeu prazo no fluxograma.** Uma ação diz "o Pix pode levar até 1 hora", contra o limite "não prometer prazo".
4. **MED fora do lugar.** Uma versão do fluxograma indicou o MED do Pix para "chave errada"; o MED é para golpe ou fraude.
5. **Passo a passo que para no meio.** O passo a passo leva a cliente até o status do Pix, mas não diz o que fazer com cada status (o "Se não der certo" do último passo é "me diga o que aparece").
6. **Nomes de menu inventados.** "Meu Dinheiro", "Atividade": o modelo não conhece o app e preenche a lacuna com nomes plausíveis, o que pode confundir a cliente.

São regras escritas no prompt que o modelo nem sempre segue, o que motiva os próximos passos abaixo.

### v3: ticket fora do escopo

**Problema.** Com a v2, um ticket que não é sobre o banco recebia uma resposta inventada. O schema obriga ao menos um passo, uma hipótese ou uma pergunta, e o modelo preenchia com o que tinha: para "Meu gato comeu pedra e eu não sei o que fazer", a v2 escreveu três passos de cuidado veterinário, com um "Quando escalar" admitindo que não era um caso bancário ([samples/08](samples/08_out_of_scope_resolution_steps_v2.json)).

**Hipótese.** Dar ao modelo uma saída legítima para recusar, com critérios explícitos do que está fora do escopo, troca a resposta inventada por uma recusa útil, sem recusar tickets vagos que são sobre o banco.

**O que mudou (só isso, para testar uma hipótese por vez):**

- Schema: campo opcional `out_of_scope` nas 4 saídas, o primeiro do schema, para o modelo decidir o escopo antes de escrever o conteúdo. Preenchido, o conteúdo fica vazio e as regras de "ao menos 1 item" deixam de valer; vazio ou nulo, tudo funciona como antes, o que mantém válidos o cache e os samples das versões anteriores.
- Persona (`system.md`): seção **Escopo**, com o que está dentro (conta, Pix, cartão, app, acesso…), três casos de recusa (assunto sem relação com o banco, texto sem sentido, pedido para fazer outra coisa) e a regra de desempate: ticket vago mas sobre o banco está no escopo, e na dúvida não se recusa.
- Tarefas: para quem vai a mensagem. No passo a passo e nas perguntas, ao cliente, no tom do perfil, pedindo que descreva o problema com o banco e sem orientar sobre o assunto de fora; no diagnóstico e no fluxograma, uma frase para o atendente.

Fora disso, **a v3 é exatamente a v2**: mesma persona, mesmas regras de perfil, mesmas tarefas e mesmo exemplo de fluxograma (um teste automático garante que o texto é idêntico fora da seção de escopo). Ou seja, a v3 é a v2 com o caso de ticket fora do escopo tratado, e por isso virou a versão padrão.

**Resultado** (chamadas reais, `gemini-2.5-flash`, temperatura 0.2):

| Ticket | Tipo | v2 | v3 |
| --- | --- | --- | --- |
| "Meu gato comeu pedra…" (Renata) | passo a passo | 3 passos de cuidado com o gato | Recusa educada à cliente, pedindo que descreva o problema com o banco ([samples/09](samples/09_out_of_scope_resolution_steps_v3.json)) |
| "Meu gato comeu pedra…" (Renata) | diagnóstico | | Uma frase ao atendente: o ticket não é um problema bancário |
| "Meu app não funciona, me ajuda." (Lucas) | perguntas | | **Não recusou**: 3 perguntas para entender a falha do app |
| T001, Pix que não chegou (Maria) | passo a passo | | Passo a passo normal, sem recusa |

A resposta fora do escopo também ficou mais barata: 95 a 103 tokens de saída, contra 237 a 363 da v2 inventando passos. A recusa veio no tom do perfil da Renata (detalhado, e-mail).

## 4. Decisões técnicas que afetam o prompt

| Decisão | Motivo |
| --- | --- |
| Saída estruturada com `response_schema` + validação Pydantic | Garante JSON parseável e desenhável (fluxograma); validações semânticas (árvore sem ciclo, raiz `n1`, nós alcançáveis) ficam no Pydantic |
| 1 retry com o erro de validação no prompt (`retry.md`) | O modelo recebe o motivo exato da falha; se falhar de novo, erro claro e nada é gravado |
| Pensamento do modelo desligado (`thinking_budget=0`) | Saída mais previsível, menor custo, e nenhum raciocínio interno guardado; a decomposição do diagnóstico já está no prompt |
| Temperatura 0.2 (fixa na comparação) | Reprodutibilidade; diferenças entre v1 e v2 vêm do template, não do acaso |
| `max_tokens` 2048 | As respostas reais usaram de 176 a 696 tokens; resposta cortada gera erro explícito e nada é gravado |
| Ticket repetido e aberto fica fora do histórico | Se o cliente abre um segundo ticket com o mesmo texto de um anterior ainda aberto, o anterior não entra no `<historico_cliente>`: é a mesma reclamação, não acrescenta informação, e o prompt fica idêntico ao do primeiro ticket, então a resposta sai do cache. Se o anterior já foi resolvido, ele entra, porque a resolução é informação nova |
| API `v1beta` do Gemini | A `v1` (padrão do SDK) recusa *system instruction*, que é a camada de persona |
| Hash dos templates na chave de cache | Editar um template invalida só o cache que depende dele |

## 5. Limitações e próximos passos

**Limitações**

- **Avaliação pequena e manual.** A rubrica foi aplicada a um cenário (T001 + C001) e uma execução por versão; com temperatura 0.2 ainda há variação entre execuções. Não é uma medida estatística.
- **Regras no prompt não são garantias.** Como mostram as falhas acima, o modelo às vezes ignora regras escritas (perguntar o que já se sabe, prometer prazo).
- **Identidade e acesso.** Aqui o atendente escolhe o cliente na CLI. Num produto real, o `customer_id` viria da sessão autenticada (login no app, telefone verificado, e-mail confirmado), ligado a identificadores externos numa tabela de identidades, nunca do texto digitado. O projeto tem um login de demonstração (contas cliente/atendente com hash de senha e permissões na camada de serviço), sem recuperação de senha, bloqueio por tentativas nem integração com um provedor de identidade.
- **Dados fictícios e domínio simplificado.** Não há integração com sistemas reais de Pix ou cartão; o modelo não consulta status de transação.

**Próximos passos**

- **v4** para recuperar a acionabilidade sem perder a adequação, com:
  - instrução explícita para considerar hipóteses sugeridas pelo histórico e pedir confirmação antes de afirmar (no T001, checar o limite noturno);
  - regra de que o último passo cubra cada resultado possível ("se aparecer X, faça Y") e avise para não refazer uma transação antes de confirmar o status;
  - proibição de inventar nomes de menu: descrever a função ("a lista de movimentações") em vez de um rótulo;
  - checagem de "isso já está no ticket?" antes de cada pergunta e exemplos negativos de prazo prometido.
- **Mais cenários na comparação** (um por perfil) e várias execuções por versão, para comparar médias.
- **Avaliação automática como apoio** (*LLM-as-judge*) em campo separado (`auto_evaluation`), sem substituir a nota humana.
- **Checagens automáticas** de regras objetivas: número de passos por canal, presença de prazo em minutos/horas, pedido de senha ou código SMS.
