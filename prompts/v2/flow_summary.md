## Tarefa: fluxograma de atendimento (uso interno do atendente)

Monte a árvore de decisão que o atendente segue para conduzir este caso:

- `decision_tree.nodes`: lista de nós. Cada nó tem `id` único (n1, n2…), `type` e `text`.
- `type` "decision": `text` é uma pergunta de sim ou não; `yes` e `no` apontam para ids de outros nós.
- `type` "action": folha da árvore; `text` é a ação a tomar, sem `yes` nem `no`.
- O nó n1 é a raiz. Sem ciclos; todo caminho termina numa ação.
- Entre 5 e 9 nós, com textos de no máximo 12 palavras.

Exemplo só de formato (outro caso: cartão bloqueado):

```json
{"decision_tree": {"nodes": [
  {"id": "n1", "type": "decision", "text": "O cartão aparece bloqueado no app?", "yes": "n2", "no": "n3"},
  {"id": "n2", "type": "decision", "text": "O cliente reconhece o motivo do bloqueio?", "yes": "n4", "no": "n5"},
  {"id": "n3", "type": "action", "text": "Verificar limite disponível e mensagem de erro da compra"},
  {"id": "n4", "type": "action", "text": "Orientar o desbloqueio pelo app"},
  {"id": "n5", "type": "action", "text": "Tratar como suspeita de fraude e escalar para segurança"}
]}}
```

O conteúdo da sua árvore deve ser do caso atual, não do exemplo.

Formato: JSON no schema definido.
