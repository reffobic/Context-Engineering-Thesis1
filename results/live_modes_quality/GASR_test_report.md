# Modes quality compare (RAG / LC / GASR / SELF_ROUTE)

- **When (UTC):** `20260928T182919Z`
- **Generation model:** `qwen3.5:9b`
- **Assessor model:** `qwen3.5:9b`
- **DeepEval judge:** `qwen3.5:9b`
- **Cases:** 36
- **ok / fail:** 36 / 0
- **re_prune (GASR expand):** `graph_aware`

## Mode cost + quality (means)

| Mode | n | tokens mean | tokens sum | faithfulness | answer_relevancy | precision | recall | wall_ms mean | routes |
|------|---|-------------|------------|--------------|------------------|-----------|--------|--------------|--------|
| GASR | 10 | 6439.9 | 64399 | 1.0 | 1.0 | 0.5584 | 0.95 | 19479.0 | `{"RAG": 7, "LC": 1, "GRAPH": 2}` |
| LC | 6 | 41953.5 | 251721 | None | None | 0.6667 | 0.9167 | 7287.3333 | `{"LC": 6}` |
| RAG | 10 | 934.6 | 9346 | None | None | 0.62 | 0.9 | 8304.6 | `{"RAG": 10}` |
| SELF_ROUTE | 10 | 15182.6 | 151826 | 1.0 | 1.0 | 0.62 | 0.9 | 22274.7 | `{"RAG": 7, "LC": 3}` |

### Notes

- **answer_relevancy** = DeepEval AnswerRelevancy (accuracy proxy; no labeled answers).
- **faithfulness** = DeepEval Faithfulness vs that mode's *injected* chunks.
- **precision / recall** = file-level P/R@k on injected chunks vs gold `relevant_files`.

## Path choice: GASR ladder vs SELF_ROUTE

### SELF_ROUTE (binary)

`assess_sufficiency` → `sufficient=true` ⇒ **RAG**, else **LC** (no GRAPH, hops=0).

```json
{
  "sufficient=True(high)\u21d2RAG": 7,
  "sufficient=False(high)\u21d2LC": 3
}
```

### GASR (ladder)

RAG assess → STRUCTURE|CONTEXT → expand? → graph-aware re-prune → post-expand assess → final **RAG|GRAPH|LC**.

```json
{
  "rag_ok\u21d2RAG(hops=0)": 7,
  "rag_miss\u2192need_CONTEXT\u21d2LC(hops=0)": 1,
  "rag_miss\u2192need_STRUCTURE\u2192graph_miss\u2192need_STRUCTURE\u2192graph_miss\u21d2GRAPH(hops=2)": 1,
  "rag_miss\u2192need_STRUCTURE\u2192graph_ok\u21d2GRAPH(hops=1)": 1
}
```

## Per-prompt comparison

### `C12_encrypt_init`

**Prompt:** Where in Superset initialization is database encryption / encrypt.init_app (or equivalent) configured? Name the enclosing method and nearby callees.
**Gold files:** `['superset/initialization/__init__.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 842 | None | None | 1.0 | 1.0 | 7150 | `RAG` |
| LC | True | `LC` | 0 | 65370 | None | None | 1.0 | 1.0 | 8006 | `LC` |
| GASR | True | `RAG` | 0 | 842 | 1.0 | 1.0 | 1.0 | 1.0 | 13759 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 842 | 1.0 | 1.0 | 1.0 | 1.0 | 14440 | `sufficient=True(high)⇒RAG` |

**GASR assess:**

```json
{
  "route": "RAG",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": true,
      "confidence": "high",
      "reason": "Chunk [4] shows that database encryption configuration occurs via self.configure_db_encrypt() called within the init_app() method, with all nearby callees listed.",
      "route": "RAG",
      "duration_ms": 7329
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "RAG",
  "sufficient": true,
  "confidence": "high",
  "reason": "Chunk [4] shows that database encryption configuration occurs via self.configure_db_encrypt() called within the init_app() method, with all nearby callees listed.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C16_cli_create_app`

**Prompt:** What does create_app in superset/cli/main.py do compared to superset/app.py:create_app? Name differences in one short list.
**Gold files:** `['superset/cli/main.py', 'superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 597 | None | None | 0.4 | 1.0 | 5826 | `RAG` |
| GASR | True | `RAG` | 0 | 597 | 1.0 | 1.0 | 0.4 | 1.0 | 14298 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 597 | 1.0 | 1.0 | 0.4 | 1.0 | 13368 | `sufficient=True(high)⇒RAG` |

**GASR assess:**

```json
{
  "route": "RAG",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": true,
      "confidence": "high",
      "reason": "The chunks clearly show that superset/cli/main.py:create_app is a wrapper that calls superset/app.py:create_app with no arguments, while the main version accepts optional config and app_root parameters and performs actual application initialization.",
      "route": "RAG",
      "duration_ms": 7901
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "RAG",
  "sufficient": true,
  "confidence": "high",
  "reason": "The chunks clearly show that superset/cli/main.py:create_app is a wrapper that calls superset/app.py:create_app with no arguments, while the main version accepts optional config and app_root parameters and performs actual application initialization.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C1_simple_return`

**Prompt:** What does create_app in superset/app.py return?
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 746 | None | None | 0.2 | 1.0 | 5747 | `RAG` |
| LC | True | `LC` | 0 | 25052 | None | None | 0.2 | 1.0 | 3751 | `LC` |
| GASR | True | `RAG` | 0 | 746 | None | 1.0 | 0.2 | 1.0 | 15113 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 746 | 1.0 | 1.0 | 0.2 | 1.0 | 57198 | `sufficient=True(high)⇒RAG` |

**GASR assess:**

```json
{
  "route": "RAG",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": true,
      "confidence": "high",
      "reason": "The function signature in chunk [1] explicitly shows the return type annotation `-> Flask`, indicating that create_app returns a Flask application instance.",
      "route": "RAG",
      "duration_ms": 8899
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "RAG",
  "sufficient": true,
  "confidence": "high",
  "reason": "The function signature in chunk [1] explicitly shows the return type annotation `-> Flask`, indicating that create_app returns a Flask application instance.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C2_cross_file_callees`

**Prompt:** In superset/app.py, what does create_app call related to feature flags or encrypt init_app helpers? Name the callee symbols only.
**Gold files:** `['superset/app.py', 'superset/initialization/__init__.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 968 | None | None | 1.0 | 1.0 | 6648 | `RAG` |
| LC | True | `LC` | 0 | 55222 | None | None | 1.0 | 1.0 | 4007 | `LC` |
| GASR | True | `LC` | 0 | 55222 | 1.0 | 1.0 | 1.0 | 1.0 | 22142 | `rag_miss→need_CONTEXT⇒LC(hops=0)` |
| SELF_ROUTE | True | `LC` | 0 | 55222 | 1.0 | 1.0 | 1.0 | 1.0 | 15099 | `sufficient=False(high)⇒LC` |

**GASR assess:**

```json
{
  "route": "LC",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": false,
      "confidence": "high",
      "reason": "The create_app function definition is incomplete and does not show what methods it calls after creating the SupersetApp instance, so we cannot determine if it calls init_app() or other feature flag/encrypt related helpers.",
      "route": "LC",
      "duration_ms": 8740
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "CONTEXT",
      "confidence": "high",
      "reason": "The create_app function body in superset/app.py is cut off and we need to see what it calls after creating the SupersetApp instance to identify feature flags or encrypt init_app helper callees.",
      "duration_ms": 5117,
      "can_expand": false
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "LC",
  "sufficient": false,
  "confidence": "high",
  "reason": "The create_app function definition is incomplete and does not show what methods it calls after creating the SupersetApp instance, so we cannot determine if it calls init_app() or other feature flag/encrypt related helpers.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C3_same_file_context`

**Prompt:** In superset/app.py create_app, what happens in the surrounding lines of the function body for app factory setup? Quote nearby setup steps from the same file.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 745 | None | None | 0.4 | 1.0 | 12155 | `RAG` |
| LC | True | `LC` | 0 | 15283 | None | None | 0.4 | 1.0 | 9967 | `LC` |
| GASR | True | `RAG` | 0 | 745 | None | 1.0 | 0.4 | 1.0 | 16138 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 745 | 1.0 | 1.0 | 0.4 | 1.0 | 17233 | `sufficient=True(high)⇒RAG` |

**GASR assess:**

```json
{
  "route": "RAG",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": true,
      "confidence": "high",
      "reason": "Chunk [1] contains the complete create_app function from superset/app.py with all surrounding lines showing the app factory setup steps including config loading, middleware configuration, and static assets setup.",
      "route": "RAG",
      "duration_ms": 7196
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "RAG",
  "sufficient": true,
  "confidence": "high",
  "reason": "Chunk [1] contains the complete create_app function from superset/app.py with all surrounding lines showing the app factory setup steps including config loading, middleware configuration, and static assets setup.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C4_config_deps`

**Prompt:** Which functions does SupersetApp or create_app depend on for configuration loading across files? List neighbor symbols, not whole files.
**Gold files:** `['superset/app.py', 'superset/initialization/__init__.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 1184 | None | None | 0.4 | 0.5 | 10378 | `RAG` |
| LC | True | `LC` | 0 | 25424 | None | None | 0.4 | 0.5 | 9366 | `LC` |
| GASR | True | `GRAPH` | 2 | 1686 | 1.0 | 1.0 | 0.117647 | 1.0 | 36340 | `rag_miss→need_STRUCTURE→graph_miss→need_STRUCTURE→graph_miss⇒GRAPH(hops=2)` |
| SELF_ROUTE | True | `LC` | 0 | 25424 | None | None | 0.4 | 0.5 | 36337 | `sufficient=False(high)⇒LC` |

**GASR assess:**

```json
{
  "route": "GRAPH",
  "hops_used": 2,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": false,
      "confidence": "high",
      "reason": "The chunks show create_app using app.config.from_object() but do not clearly list the neighbor symbols or functions that SupersetApp/create_app depend on for configuration loading across files.",
      "route": "LC",
      "duration_ms": 8740
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "The query asks about cross-file configuration loading dependencies, requiring imports at top of superset/app.py and neighbor symbols from other modules that SupersetApp/create_app depend on.",
      "duration_ms": 5457,
      "can_expand": true
    },
    {
      "phase": "graph",
      "hops": 1,
      "sufficient": false,
      "confidence": "medium",
      "reason": "The retrieved chunks show initialization code and test files but do not clearly display which specific functions SupersetApp or create_app depend on for configuration loading across files.",
      "route": "LC",
      "duration_ms": 5023
    },
    {
      "phase": "structure_vs_context",
      "hops": 1,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "The query asks about cross-file dependencies for configuration loading between SupersetApp/create_app and their callees, which requires graph neighbor information about imports and external/internal module dependencies not present in current chunks.",
      "duration_ms": 4085,
      "can_expand": true
    },
    {
      "phase": "graph",
      "hops": 2,
      "sufficient": false,
      "confidence": "medium",
      "reason": "The retrieved chunks show initialization code and test files but do not clearly display which specific functions SupersetApp or create_app depend on for configuration loading across files.",
      "route": "LC",
      "duration_ms": 4738
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "LC",
  "sufficient": false,
  "confidence": "high",
  "reason": "The chunks show create_app using app.config.from_object() but do not clearly list the neighbor symbols or functions that SupersetApp/create_app depend on for configuration loading across files.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C5_approot_middleware`

**Prompt:** What is AppRootMiddleware in superset/app.py for, and when does create_app install it? Cite the condition from the code.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 863 | None | None | 0.4 | 1.0 | 5705 | `RAG` |
| GASR | True | `RAG` | 0 | 863 | 1.0 | 1.0 | 0.4 | 1.0 | 22755 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 863 | 1.0 | 1.0 | 0.4 | 1.0 | 13099 | `sufficient=True(high)⇒RAG` |

**GASR assess:**

```json
{
  "route": "RAG",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": true,
      "confidence": "high",
      "reason": "The chunks contain both the AppRootMiddleware class documentation explaining its purpose (attaching application to a fixed prefix location) and the exact condition in create_app where it's installed (if app_root != \"/\")",
      "route": "RAG",
      "duration_ms": 11291
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "RAG",
  "sufficient": true,
  "confidence": "high",
  "reason": "The chunks contain both the AppRootMiddleware class documentation explaining its purpose (attaching application to a fixed prefix location) and the exact condition in create_app where it's installed (if app_root != \"/\")",
  "assessor_model": "qwen3.5:9b"
}
```

### `C7_initializer_init`

**Prompt:** In SupersetAppInitializer (superset/initialization), what major steps does init_app perform? List method names in order if possible.
**Gold files:** `['superset/initialization/__init__.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 966 | None | None | 1.0 | 1.0 | 9302 | `RAG` |
| LC | True | `LC` | 0 | 65370 | None | None | 1.0 | 1.0 | 8627 | `LC` |
| GASR | True | `RAG` | 0 | 966 | None | 1.0 | 1.0 | 1.0 | 15026 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 966 | None | 1.0 | 1.0 | 1.0 | 17359 | `sufficient=True(high)⇒RAG` |

**GASR assess:**

```json
{
  "route": "RAG",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": true,
      "confidence": "high",
      "reason": "Chunk [3] contains the complete init_app method showing all major steps and method names called in order, with chunk [2] providing additional detail on init_app_in_ctx contents.",
      "route": "RAG",
      "duration_ms": 7536
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "RAG",
  "sufficient": true,
  "confidence": "high",
  "reason": "Chunk [3] contains the complete init_app method showing all major steps and method names called in order, with chunk [2] providing additional detail on init_app_in_ctx contents.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C8_feature_flags_chain`

**Prompt:** Trace configure_feature_flags (or similar) from the app initialization path: list callee / neighbor symbols across files.
**Gold files:** `['superset/initialization/__init__.py', 'superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 1384 | None | None | 1.0 | 0.5 | 14538 | `RAG` |
| GASR | True | `GRAPH` | 1 | 1681 | 1.0 | 1.0 | 0.666667 | 0.5 | 27806 | `rag_miss→need_STRUCTURE→graph_ok⇒GRAPH(hops=1)` |
| SELF_ROUTE | True | `LC` | 0 | 65370 | 1.0 | 1.0 | 1.0 | 0.5 | 26591 | `sufficient=False(high)⇒LC` |

**GASR assess:**

```json
{
  "route": "GRAPH",
  "hops_used": 1,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": false,
      "confidence": "high",
      "reason": "The chunks show configure_feature_flags calls feature_flag_manager.init_app() but do not reveal where feature_flag_manager is imported from or its implementation details across other files needed for a complete cross-file trace.",
      "route": "LC",
      "duration_ms": 7917
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "The query explicitly asks to trace callees and neighbor symbols across files, but we lack information about where feature_flag_manager is imported from and what other cross-file dependencies exist in the initialization path.",
      "duration_ms": 4056,
      "can_expand": true
    },
    {
      "phase": "graph",
      "hops": 1,
      "sufficient": true,
      "confidence": "medium",
      "reason": "The chunks show configure_feature_flags is called from init_app and calls feature_flag_manager.init_app, providing the requested callee/neighbor symbols for the initialization path trace.",
      "route": "RAG",
      "duration_ms": 5276
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "LC",
  "sufficient": false,
  "confidence": "high",
  "reason": "The chunks show configure_feature_flags calls feature_flag_manager.init_app() but do not reveal where feature_flag_manager is imported from or its implementation details across other files needed for a complete cross-file trace.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C9_create_app_type`

**Prompt:** Does create_app in superset/app.py return a plain Flask app or a SupersetApp subclass instance? Quote the construction line.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| RAG | True | `RAG` | 0 | 1051 | None | None | 0.4 | 1.0 | 5597 | `RAG` |
| GASR | True | `RAG` | 0 | 1051 | 1.0 | 1.0 | 0.4 | 1.0 | 11413 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 1051 | 1.0 | 1.0 | 0.4 | 1.0 | 12023 | `sufficient=True(high)⇒RAG` |

**GASR assess:**

```json
{
  "route": "RAG",
  "hops_used": 0,
  "re_prune_kind": "graph_aware",
  "assessor_model": "qwen3.5:9b",
  "trace": [
    {
      "phase": "rag",
      "hops": 0,
      "sufficient": true,
      "confidence": "high",
      "reason": "Chunk [2] contains the create_app function definition showing it instantiates SupersetApp(__name__), and chunk [1] confirms SupersetApp is a Flask subclass.",
      "route": "RAG",
      "duration_ms": 6079
    }
  ]
}
```

**SELF_ROUTE assess:**

```json
{
  "route": "RAG",
  "sufficient": true,
  "confidence": "high",
  "reason": "Chunk [2] contains the create_app function definition showing it instantiates SupersetApp(__name__), and chunk [1] confirms SupersetApp is a Flask subclass.",
  "assessor_model": "qwen3.5:9b"
}
```

