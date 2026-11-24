# Samples

Execuções reais copiadas de `runs/` (geradas com `gemini-2.5-flash`, temperatura 0.2, `max_tokens` 2048, pensamento desligado). Cenário: ticket **T001** da cliente **C001 (Maria: leiga, didática, telefone)**, com o T005 (limite noturno de Pix) como histórico.

| Arquivo | O que mostra |
| --- | --- |
| `01_diagnosis_v2.json` | Diagnóstico gerado pela API (`cache_hit: false`, com `usage`: tokens e latência) |
| `02_diagnosis_v2_cache_hit.json` | A mesma entrada repetida: mesma `cache_key`, `cache_hit: true`, sem `usage` (não chamou a API) |
| `03_resolution_steps_v2.json` | Passo a passo para a cliente |
| `04_clarifying_questions_v2.json` | Perguntas de esclarecimento priorizadas |
| `05_flow_summary_v2.json` | Árvore de decisão (o desenho ASCII é feito pela CLI) |
| `06_compare_resolution_steps_v1_rubric.json` | Comparação v1 × v2 do passo a passo: execução da **v1** com a rubrica no campo `evaluation` |
| `07_compare_resolution_steps_v2_rubric.json` | A mesma comparação: execução da **v2** (veio do cache) com a rubrica; mesmo `comparison_id` da 06 |
| `08_out_of_scope_resolution_steps_v2.json` | Ticket fora do escopo ("Meu gato comeu pedra e eu não sei o que fazer", cliente C004) na **v2**: o modelo inventa passos de cuidado com o gato |
| `09_out_of_scope_resolution_steps_v3.json` | O mesmo ticket na **v3**: `out_of_scope` com a mensagem para a cliente e conteúdo vazio |
| `cache_log.txt` | Trecho do `logs/app.log` com o cache miss e, depois, o cache hit da mesma entrada |

Os samples 08 e 09 usam um ticket temporário (T007 da Renata), criado só para o teste e que não está em `data/tickets.json`.

Resultado da rubrica (notas de 1 a 5; justificativas completas nos arquivos 06 e 07 e no [PROMPT_ENGINEERING_NOTES.md](../PROMPT_ENGINEERING_NOTES.md#3-resultados)):

| Critério | v1 | v2 |
| --- | --- | --- |
| Clareza | 3 | 4 |
| Adequação ao cliente | 2 | 5 |
| Acionabilidade | 3 | 2 |
| **Total** | **8** | **11** |

## Formato do arquivo de rubrica (`compare --rubric`)

```json
{
  "v1": {
    "clarity":       {"score": 3, "justification": "..."},
    "customer_fit":  {"score": 2, "justification": "..."},
    "actionability": {"score": 3, "justification": "..."}
  },
  "v2": {
    "clarity":       {"score": 4, "justification": "..."},
    "customer_fit":  {"score": 5, "justification": "..."},
    "actionability": {"score": 2, "justification": "..."}
  }
}
```

Notas inteiras de 1 a 5; a justificativa não pode ficar vazia.
