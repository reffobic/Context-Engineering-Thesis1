# Lightweight tools cost ablation

- **timestamp:** `20260929T130300Z`
- **with_generate:** `False`
- **token_budget:** `1800` (RAG prune)

## Summary (lookup prompts only)

| Arm | n | tokens_injected mean | prompt tokens mean | retrieve_ms mean |
|-----|---|----------------------|--------------------|------------------|
| Hybrid (tools OFF) | 6 | 763.5 | 1231.5 | 1921.83 |
| Lightweight (tools ON) | 6 | 135.5 | 535.67 | 921.67 |

**Token reduction (injected):** `82.3%`  
**Token reduction (final prompt):** `56.5%`  
**Routing correct:** `6/6`

## Per-case pairs

| ID | expect | path ON | hybrid tokens | light tokens | Δ tokens | % drop | hybrid retrieve_ms | light retrieve_ms | routing_ok |
|----|--------|---------|---------------|--------------|----------|--------|--------------------|-------------------|------------|
| S1_find_symbol_create_app | find_symbol | find_symbol | 317 | 117 | -200.0 | 63.1% | 3008 | 1104 | yes |
| S2_find_symbol_SupersetApp | find_symbol | find_symbol | 1138 | 22 | -1116.0 | 98.1% | 1294 | 1140 | yes |
| R1_find_references_create_app | find_references | find_references | 632 | 336 | -296.0 | 46.8% | 1759 | 46 | yes |
| R2_find_references_init_app | find_references | find_references | 356 | 252 | -104.0 | 29.2% | 1908 | 54 | yes |
| L1_list_files_cli | list_files | list_files | 576 | 75 | -501.0 | 87.0% | 1756 | 1513 | yes |
| L2_list_files_mcp | list_files | list_files | 1562 | 11 | -1551.0 | 99.3% | 1806 | 1673 | yes |
| X1_explain_create_app | hybrid | hybrid | 684 | 684 | 0.0 | 0.0% | 1846 | 1828 | yes |

## Aggregates by bucket

### `lookup_all`
```json
{
  "n": 6,
  "routing_ok": 6,
  "hybrid_tokens_mean": 763.5,
  "lightweight_tokens_mean": 135.5,
  "tokens_pct_drop_mean": 82.3,
  "hybrid_prompt_tokens_mean": 1231.5,
  "lightweight_prompt_tokens_mean": 535.67,
  "prompt_tokens_pct_drop_mean": 56.5,
  "hybrid_retrieve_ms_mean": 1921.83,
  "lightweight_retrieve_ms_mean": 921.67,
  "retrieve_ms_delta_mean": -1000.16
}
```

### `lookup_symbol`
```json
{
  "n": 2,
  "routing_ok": 2,
  "hybrid_tokens_mean": 727.5,
  "lightweight_tokens_mean": 69.5,
  "tokens_pct_drop_mean": 90.4,
  "hybrid_prompt_tokens_mean": 1179.5,
  "lightweight_prompt_tokens_mean": 367.5,
  "prompt_tokens_pct_drop_mean": 68.8,
  "hybrid_retrieve_ms_mean": 2151.0,
  "lightweight_retrieve_ms_mean": 1122.0,
  "retrieve_ms_delta_mean": -1029.0
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
  "hybrid_retrieve_ms_mean": 1833.5,
  "lightweight_retrieve_ms_mean": 50.0,
  "retrieve_ms_delta_mean": -1783.5
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
  "hybrid_retrieve_ms_mean": 1781.0,
  "lightweight_retrieve_ms_mean": 1593.0,
  "retrieve_ms_delta_mean": -188.0
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
  "hybrid_retrieve_ms_mean": 1846.0,
  "lightweight_retrieve_ms_mean": 1828.0,
  "retrieve_ms_delta_mean": -18.0
}
```
