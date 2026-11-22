## Tarefa: perguntas de esclarecimento (enviadas ao cliente $customer_name)

Regras de comunicação para este cliente:
$profile_rules

Escreva só as perguntas necessárias para avançar no atendimento:

- `questions`: no máximo 3. Cada pergunta deve confirmar ou descartar uma hipótese específica sobre a causa do problema.
- `question`: simples, uma coisa por vez, seguindo as regras acima.
- `why_it_matters`: para o atendente, qual hipótese a resposta confirma ou descarta.
- `priority`: de 1 (mais importante) a 3.
- Não pergunte o que já está no ticket ou no histórico.
- Nunca peça senha, código de SMS ou número completo do cartão.

Formato: JSON no schema definido.
