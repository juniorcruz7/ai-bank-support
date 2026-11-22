# Samples

Execuções reais copiadas de `runs/` (geradas com `gemini-2.5-flash`, temperatura 0.2, `max_tokens` 2048, pensamento desligado). Cenário: ticket **T001** da cliente **C001 (Maria: leiga, didática, telefone)**, com o T005 (limite noturno de Pix) como histórico.

| Arquivo | O que mostra |
| --- | --- |
| `01_diagnosis_v2.json` | Diagnóstico gerado pela API (`cache_hit: false`, com `usage`: tokens e latência) |
| `02_diagnosis_v2_cache_hit.json` | A mesma entrada repetida: mesma `cache_key`, `cache_hit: true`, sem `usage` (não chamou a API) |
| `03_resolution_steps_v2.json` | Passo a passo para a cliente |
| `04_clarifying_questions_v2.json` | Perguntas de esclarecimento priorizadas |
| `05_flow_summary_v2.json` | Árvore de decisão (o desenho ASCII é feito pela CLI) |
| `cache_log.txt` | Trecho do `logs/app.log` com o cache miss e, depois, o cache hit da mesma entrada |

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
    "actionability": {"score": 4, "justification": "..."}
  }
}
```

Notas inteiras de 1 a 5; a justificativa não pode ficar vazia.
