## Tarefa: diagnóstico (uso interno do atendente)

Raciocine nesta ordem e preencha os campos:

1. `hypotheses`: de 2 a 4 causas possíveis, cada uma com `description` e `probability` (alta, media ou baixa), da mais para a menos provável. Considere causas do lado do cliente, do banco e de terceiros (por exemplo, o banco do destinatário).
2. `evidence`: só fatos escritos no ticket ou no histórico, citados de forma objetiva. Suposições não entram aqui.
3. `missing_info`: os dados que faltam para confirmar ou descartar as hipóteses, do mais importante para o menos.
4. `likely_conclusion`: a hipótese mais provável e o motivo, em 1 ou 2 frases, deixando claro o que ainda precisa ser confirmado.

Formato: JSON no schema definido. Linguagem técnica e direta; cada item com no máximo 2 frases.
