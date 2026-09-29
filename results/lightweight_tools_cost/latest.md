# Lightweight tools cost ablation

- **timestamp:** `20260929T130415Z`
- **with_generate:** `False`
- **token_budget:** `1800` (RAG prune)

## Summary (lookup prompts only)

| Arm | n | tokens_injected mean | prompt tokens mean | retrieve_ms mean |
|-----|---|----------------------|--------------------|------------------|
| Hybrid (tools OFF) | 6 | 687.17 | 1156.17 | 2027.0 |
| Lightweight (tools ON) | 6 | 213.83 | 579.83 | 1026.83 |

**Token reduction (injected):** `68.9%`  
**Token reduction (final prompt):** `49.8%`  
**Routing correct:** `6/6`

## Per-case pairs

| ID | expect | path ON | hybrid tokens | light tokens | Δ tokens | % drop | hybrid retrieve_ms | light retrieve_ms | routing_ok |
|----|--------|---------|---------------|--------------|----------|--------|--------------------|-------------------|------------|
| S1_find_symbol_create_app | find_symbol | find_symbol | 317 | 117 | -200.0 | 63.1% | 3060 | 1511 | yes |
| S2_find_symbol_SupersetApp | find_symbol | find_symbol | 1138 | 22 | -1116.0 | 98.1% | 1879 | 1483 | yes |
| R1_find_references_create_app | find_references | find_references | 632 | 336 | -296.0 | 46.8% | 1777 | 46 | yes |
| R2_find_references_init_app | find_references | find_references | 356 | 252 | -104.0 | 29.2% | 1893 | 54 | yes |
| L1_list_files_cli | list_files | list_files | 576 | 81 | -495.0 | 85.9% | 1711 | 1502 | yes |
| L2_list_files_mcp | list_files | list_files | 1104 | 475 | -629.0 | 57.0% | 1842 | 1565 | yes |
| X1_explain_create_app | hybrid | hybrid | 684 | 684 | 0.0 | 0.0% | 1852 | 1794 | yes |

## Aggregates by bucket

### `lookup_all`
```json
{
  "n": 6,
  "routing_ok": 6,
  "hybrid_tokens_mean": 687.17,
  "lightweight_tokens_mean": 213.83,
  "tokens_pct_drop_mean": 68.9,
  "hybrid_prompt_tokens_mean": 1156.17,
  "lightweight_prompt_tokens_mean": 579.83,
  "prompt_tokens_pct_drop_mean": 49.8,
  "hybrid_retrieve_ms_mean": 2027.0,
  "lightweight_retrieve_ms_mean": 1026.83,
  "retrieve_ms_delta_mean": -1000.17
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
  "hybrid_retrieve_ms_mean": 2469.5,
  "lightweight_retrieve_ms_mean": 1497.0,
  "retrieve_ms_delta_mean": -972.5
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
  "hybrid_retrieve_ms_mean": 1835.0,
  "lightweight_retrieve_ms_mean": 50.0,
  "retrieve_ms_delta_mean": -1785.0
}
```

### `lookup_files`
```json
{
  "n": 2,
  "routing_ok": 2,
  "hybrid_tokens_mean": 840.0,
  "lightweight_tokens_mean": 278.0,
  "tokens_pct_drop_mean": 66.9,
  "hybrid_prompt_tokens_mean": 1391.0,
  "lightweight_prompt_tokens_mean": 575.0,
  "prompt_tokens_pct_drop_mean": 58.7,
  "hybrid_retrieve_ms_mean": 1776.5,
  "lightweight_retrieve_ms_mean": 1533.5,
  "retrieve_ms_delta_mean": -243.0
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
  "hybrid_retrieve_ms_mean": 1852.0,
  "lightweight_retrieve_ms_mean": 1794.0,
  "retrieve_ms_delta_mean": -58.0
}
```
