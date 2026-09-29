# Lightweight tools cost ablation

- **timestamp:** `20260929T130123Z`
- **with_generate:** `False`
- **token_budget:** `1800` (RAG prune)

## Summary (lookup prompts only)

| Arm | n | tokens_injected mean | prompt tokens mean | retrieve_ms mean |
|-----|---|----------------------|--------------------|------------------|
| Hybrid (tools OFF) | 6 | 763.5 | 1231.5 | 2062.33 |
| Lightweight (tools ON) | 6 | 444.0 | 1014.83 | 904.83 |

**Token reduction (injected):** `41.8%`  
**Token reduction (final prompt):** `17.6%`  
**Routing correct:** `6/6`

## Per-case pairs

| ID | expect | path ON | hybrid tokens | light tokens | Δ tokens | % drop | hybrid retrieve_ms | light retrieve_ms | routing_ok |
|----|--------|---------|---------------|--------------|----------|--------|--------------------|-------------------|------------|
| S1_find_symbol_create_app | find_symbol | find_symbol | 317 | 1796 | 1479.0 | -466.6% | 3679 | 1513 | yes |
| S2_find_symbol_SupersetApp | find_symbol | find_symbol | 1138 | 194 | -944.0 | 83.0% | 1860 | 1556 | yes |
| R1_find_references_create_app | find_references | find_references | 632 | 336 | -296.0 | 46.8% | 1742 | 46 | yes |
| R2_find_references_init_app | find_references | find_references | 356 | 252 | -104.0 | 29.2% | 1734 | 54 | yes |
| L1_list_files_cli | list_files | list_files | 576 | 75 | -501.0 | 87.0% | 1368 | 1114 | yes |
| L2_list_files_mcp | list_files | list_files | 1562 | 11 | -1551.0 | 99.3% | 1991 | 1146 | yes |
| X1_explain_create_app | hybrid | hybrid | 684 | 684 | 0.0 | 0.0% | 1857 | 1816 | yes |

## Aggregates by bucket

### `lookup_all`
```json
{
  "n": 6,
  "routing_ok": 6,
  "hybrid_tokens_mean": 763.5,
  "lightweight_tokens_mean": 444.0,
  "tokens_pct_drop_mean": 41.8,
  "hybrid_prompt_tokens_mean": 1231.5,
  "lightweight_prompt_tokens_mean": 1014.83,
  "prompt_tokens_pct_drop_mean": 17.6,
  "hybrid_retrieve_ms_mean": 2062.33,
  "lightweight_retrieve_ms_mean": 904.83,
  "retrieve_ms_delta_mean": -1157.5
}
```

### `lookup_symbol`
```json
{
  "n": 2,
  "routing_ok": 2,
  "hybrid_tokens_mean": 727.5,
  "lightweight_tokens_mean": 995.0,
  "tokens_pct_drop_mean": -36.8,
  "hybrid_prompt_tokens_mean": 1179.5,
  "lightweight_prompt_tokens_mean": 1805.0,
  "prompt_tokens_pct_drop_mean": -53.0,
  "hybrid_retrieve_ms_mean": 2769.5,
  "lightweight_retrieve_ms_mean": 1534.5,
  "retrieve_ms_delta_mean": -1235.0
}
```

### `lookup_references`
```json
{
  "n": 2,
  "routing_ok": 2,
  "hybrid_tokens_mean": 494.0,
  "lightweight_tokens_mean": 294.0,
  "tokens_pct_drop_mean": 40.5,
  "hybrid_prompt_tokens_mean": 898.0,
  "lightweight_prompt_tokens_mean": 797.0,
  "prompt_tokens_pct_drop_mean": 11.2,
  "hybrid_retrieve_ms_mean": 1738.0,
  "lightweight_retrieve_ms_mean": 50.0,
  "retrieve_ms_delta_mean": -1688.0
}
```

### `lookup_files`
```json
{
  "n": 2,
  "routing_ok": 2,
  "hybrid_tokens_mean": 1069.0,
  "lightweight_tokens_mean": 43.0,
  "tokens_pct_drop_mean": 96.0,
  "hybrid_prompt_tokens_mean": 1617.0,
  "lightweight_prompt_tokens_mean": 442.5,
  "prompt_tokens_pct_drop_mean": 72.6,
  "hybrid_retrieve_ms_mean": 1679.5,
  "lightweight_retrieve_ms_mean": 1130.0,
  "retrieve_ms_delta_mean": -549.5
}
```

### `explanation_control`
```json
{
  "n": 1,
  "routing_ok": 1,
  "hybrid_tokens_mean": 684.0,
  "lightweight_tokens_mean": 684.0,
  "tokens_pct_drop_mean": 0.0,
  "hybrid_prompt_tokens_mean": 1169.0,
  "lightweight_prompt_tokens_mean": 1169.0,
  "prompt_tokens_pct_drop_mean": 0.0,
  "hybrid_retrieve_ms_mean": 1857.0,
  "lightweight_retrieve_ms_mean": 1816.0,
  "retrieve_ms_delta_mean": -41.0
}
```
