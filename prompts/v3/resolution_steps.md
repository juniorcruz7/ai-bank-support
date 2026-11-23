## Tarefa: passo a passo (texto enviado ao cliente $customer_name)

Regras de comunicação para este cliente:
$profile_rules

Escreva os passos que o cliente pode seguir agora:

- `steps`: cada passo com `order`, `action` (uma única ação por passo), `expected_result` (o que o cliente deve ver se der certo) e `if_fails` (o que fazer se não der certo).
- Comece pelo que é mais simples e seguro de verificar.
- Não inclua ações que só o banco pode fazer; elas vão em `escalation_note`.
- `escalation_note`: quando e como o caso deve ser escalado se os passos não resolverem, sem prometer prazo nem estorno.
- Respeite o limite de passos das regras acima, se houver.

Se o ticket estiver fora do escopo, `out_of_scope` é a mensagem enviada ao cliente: diga com educação, seguindo as regras de comunicação acima, que o pedido não parece ser sobre a conta ou os serviços do banco e peça que ele descreva o problema com o banco, se houver. Não dê orientação sobre o assunto fora do escopo.

Formato: JSON no schema definido.
