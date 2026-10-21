# Modes quality compare (RAG / LC / GASR / SELF_ROUTE)

- **When (UTC):** `20260928T210615Z`
- **Generation model:** `qwen3.5:9b`
- **Assessor model:** `qwen3.5:9b`
- **DeepEval judge:** `qwen3.5:9b`
- **Cases:** 66
- **ok / fail:** 66 / 0
- **re_prune (GASR expand):** `graph_aware`

## Mode cost + quality (means)

| Mode | n | tokens mean | tokens sum | faithfulness | answer_relevancy | precision | recall | wall_ms mean | routes |
|------|---|-------------|------------|--------------|------------------|-----------|--------|--------------|--------|
| GASR | 25 | 7576.36 | 189409 | 1.0 | 0.92 | 0.6014 | 0.9 | 21888.76 | `{"LC": 7, "RAG": 13, "GRAPH": 5}` |
| LC | 6 | 41953.5 | 251721 | None | None | 0.6667 | 0.9167 | 7287.3333 | `{"LC": 6}` |
| RAG | 10 | 934.6 | 9346 | None | None | 0.62 | 0.9 | 8304.6 | `{"RAG": 10}` |
| SELF_ROUTE | 25 | 16775.56 | 419389 | 0.9877 | 0.9714 | 0.622 | 0.88 | 19111.28 | `{"LC": 12, "RAG": 13}` |

### Notes

- **answer_relevancy** = DeepEval AnswerRelevancy (accuracy proxy; no labeled answers).
- **faithfulness** = DeepEval Faithfulness vs that mode's *injected* chunks.
- **precision / recall** = file-level P/R@k on injected chunks vs gold `relevant_files`.

## Path choice: GASR ladder vs SELF_ROUTE

### SELF_ROUTE (binary)

`assess_sufficiency` → `sufficient=true` ⇒ **RAG**, else **LC** (no GRAPH, hops=0).

```json
{
  "sufficient=False(high)\u21d2LC": 12,
  "sufficient=True(high)\u21d2RAG": 13
}
```

### GASR (ladder)

RAG assess → STRUCTURE|CONTEXT → expand? → graph-aware re-prune → post-expand assess → final **RAG|GRAPH|LC**.

```json
{
  "rag_miss\u2192need_CONTEXT\u21d2LC(hops=0)": 7,
  "rag_ok\u21d2RAG(hops=0)": 13,
  "rag_miss\u2192need_STRUCTURE\u2192graph_miss\u2192need_STRUCTURE\u21d2GRAPH(hops=2)": 1,
  "rag_miss\u2192need_STRUCTURE\u2192graph_miss\u2192need_STRUCTURE\u2192graph_miss\u21d2GRAPH(hops=2)": 2,
  "rag_miss\u2192need_STRUCTURE\u2192graph_ok\u21d2GRAPH(hops=1)": 2
}
```

## Per-prompt comparison

### `C10_local_extensions_watcher`

**Prompt:** In create_app (superset/app.py), under what conditions is the local extensions watcher started? Name the function that is called to start the thread, which module defines it, and what role that watcher plays for local extensions.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `LC` | 0 | 25388 | 1.0 | 1.0 | 0.4 | 1.0 | 20931 | `rag_miss→need_CONTEXT⇒LC(hops=0)` |
| SELF_ROUTE | True | `LC` | 0 | 25388 | 1.0 | 1.0 | 0.4 | 1.0 | 16437 | `sufficient=False(high)⇒LC` |

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
      "reason": "The retrieved chunks do not contain information about the local extensions watcher startup conditions, the function that starts the thread, its defining module, or its role - chunk 1 cuts off mid-function and chunks 2-5 are unrelated to this topic.",
      "route": "LC",
      "duration_ms": 6455
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "CONTEXT",
      "confidence": "high",
      "reason": "The query asks about specific implementation details within create_app (extensions watcher conditions, thread-starting function, module definition), but the provided chunks only show partial code of that function and unrelated files; we need more of superset/app.py to see where the extensions watcher logic is defined.",
      "duration_ms": 4519,
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
  "reason": "The retrieved chunks do not contain information about the local extensions watcher startup conditions, the function that starts the thread, its defining module, or its role - chunk 1 cuts off mid-function and chunks 2-5 are unrelated to this topic.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C11_db_up_to_date`

**Prompt:** Explain SupersetApp._is_database_up_to_date in superset/app.py: what does it verify, which migration/alembic or DB helpers does it use, and how does sync_config_to_db use its boolean result? Quote the decisive check.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `RAG` | 0 | 1606 | 1.0 | 1.0 | 0.75 | 1.0 | 14826 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 1606 | 1.0 | 1.0 | 0.75 | 1.0 | 15859 | `sufficient=True(high)⇒RAG` |

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
      "reason": "All four chunks contain the complete implementation of _is_database_up_to_date showing what it verifies (migration status via revision comparison), which helpers it uses (MigrationContext, ScriptDirectory, Config from alembic), how sync_config_to_db uses its result (returns early if False, continues if True), and the decisive check is quoted (is_current = current_rev == head_rev).",
      "route": "RAG",
      "duration_ms": 7781
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
  "reason": "All four chunks contain the complete implementation of _is_database_up_to_date showing what it verifies (migration status via revision comparison), which helpers it uses (MigrationContext, ScriptDirectory, Config from alembic), how sync_config_to_db uses its result (returns early if False, continues if True), and the decisive check is quoted (is_current = current_rev == head_rev).",
  "assessor_model": "qwen3.5:9b"
}
```

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

### `C13_app_imports`

**Prompt:** From the import section of superset/app.py, which imported symbols are directly used by create_app or SupersetApp for app construction, middleware, config loading, or the extensions watcher? List symbol names and one-line why each matters (no unrelated stdlib noise).
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `LC` | 0 | 21826 | 1.0 | 1.0 | 0.6 | 1.0 | 27247 | `rag_miss→need_CONTEXT⇒LC(hops=0)` |
| SELF_ROUTE | True | `LC` | 0 | 21826 | 1.0 | 1.0 | 0.6 | 1.0 | 17583 | `sufficient=False(high)⇒LC` |

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
      "reason": "None of the retrieved chunks show the actual import section at the top of superset/app.py, which is required to identify imported symbols used for app construction, middleware, config loading, or extensions watcher.",
      "route": "LC",
      "duration_ms": 8508
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "CONTEXT",
      "confidence": "high",
      "reason": "The query asks about imported symbols from the import section of superset/app.py, but none of the current chunks show the top of the file where imports are declared; we need to see the import statements and their usage points within the same file.",
      "duration_ms": 6938,
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
  "reason": "None of the retrieved chunks show the actual import section at the top of superset/app.py, which is required to identify imported symbols used for app construction, middleware, config loading, or extensions watcher.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C14_superset_config_env`

**Prompt:** How does create_app in superset/app.py choose which config module to load (environment variable vs default)? Quote the relevant lines, name the env var / default module string, and describe what happens if import fails.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `LC` | 0 | 15283 | 1.0 | 1.0 | 0.4 | 1.0 | 22670 | `rag_miss→need_CONTEXT⇒LC(hops=0)` |
| SELF_ROUTE | True | `LC` | 0 | 15283 | 0.9090909090909091 | 1.0 | 0.4 | 1.0 | 18538 | `sufficient=False(high)⇒LC` |

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
      "reason": "The chunks show the env var (SUPERSET_CONFIG) and default module (superset.config), but do not contain information about what happens if the config import fails (no error handling code is shown for app.config.from_object)",
      "route": "LC",
      "duration_ms": 9520
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "CONTEXT",
      "confidence": "high",
      "reason": "The query asks about exception handling when config import fails, which requires seeing the full try/except block and error handling logic in superset/app.py that is not visible in the current chunks.",
      "duration_ms": 5056,
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
  "reason": "The chunks show the env var (SUPERSET_CONFIG) and default module (superset.config), but do not contain information about what happens if the config import fails (no error handling code is shown for app.config.from_object)",
  "assessor_model": "qwen3.5:9b"
}
```

### `C15_test_create_app`

**Prompt:** In tests/integration_tests/base_tests.py, how does the test harness create_app (or equivalent factory on the test case) obtain a Flask/Superset app for integration tests? Contrast briefly with production superset/app.py:create_app (return type / wiring differences).
**Gold files:** `['tests/integration_tests/base_tests.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `GRAPH` | 2 | 664 | 1.0 | 1.0 | 1.0 | 1.0 | 31044 | `rag_miss→need_STRUCTURE→graph_miss→need_STRUCTURE⇒GRAPH(hops=2)` |
| SELF_ROUTE | True | `LC` | 0 | 32620 | 1.0 | 1.0 | 1.0 | 1.0 | 19141 | `sufficient=False(high)⇒LC` |

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
      "reason": "The chunks only show a minimal create_app stub that returns an existing 'app' variable without explaining how it's created or configured for integration tests, and there is no production superset/app.py:create_app code to contrast against.",
      "route": "LC",
      "duration_ms": 7180
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "The query asks about cross-file comparison between test harness create_app and production superset/app.py:create_app, requiring caller/implementation neighbors from both files.",
      "duration_ms": 5517,
      "can_expand": true
    },
    {
      "phase": "graph",
      "hops": 1,
      "sufficient": false,
      "confidence": "medium",
      "reason": "The chunks show a create_app method returning an app object but lack details on how the test harness obtains the Flask/Superset app and any contrast with production superset/app.py:create_app return types or wiring differences.",
      "route": "LC",
      "duration_ms": 4486
    },
    {
      "phase": "structure_vs_context",
      "hops": 1,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "The query explicitly asks for contrast with production superset/app.py:create_app (cross-file dependency) and needs to understand how app is obtained in base_tests.py (imports/class definition neighbors)",
      "duration_ms": 6377,
      "can_expand": true
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
  "reason": "The chunks only show a minimal create_app stub that returns an existing 'app' variable without explaining how it's created or configured for integration tests, and there is no production superset/app.py:create_app code to contrast against.",
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

### `C17_approot_middleware_class`

**Prompt:** Describe the AppRootMiddleware class in superset/app.py: what WSGI responsibility does it wrap, which constructor arguments does it take, and how does create_app decide to install it (app_root / config condition)? Quote the install site and the class docstring or key method behavior.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `RAG` | 0 | 1587 | None | 1.0 | 1.0 | 1.0 | 19613 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 1587 | None | 1.0 | 1.0 | 1.0 | 15957 | `sufficient=True(high)⇒RAG` |

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
      "reason": "The retrieved chunks contain the AppRootMiddleware class definition with its docstring, constructor arguments (wsgi_app and app_root), WSGI wrapping behavior (SCRIPT_NAME/PATH_INFO manipulation), and the create_app installation condition (app_root != '/') with quoted install code.",
      "route": "RAG",
      "duration_ms": 7585
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
  "reason": "The retrieved chunks contain the AppRootMiddleware class definition with its docstring, constructor arguments (wsgi_app and app_root), WSGI wrapping behavior (SCRIPT_NAME/PATH_INFO manipulation), and the create_app installation condition (app_root != '/') with quoted install code.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C18_sync_and_db_gate`

**Prompt:** Trace the relationship between sync_config_to_db and _is_database_up_to_date on SupersetApp: who calls whom, what is skipped when the DB is not up to date, and which initialization path eventually triggers sync_config_to_db (name the initializer method if present).
**Gold files:** `['superset/app.py', 'superset/initialization/__init__.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `RAG` | 0 | 1768 | None | 1.0 | 0.8 | 1.0 | 17277 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 1768 | 1.0 | 1.0 | 0.8 | 1.0 | 16589 | `sufficient=True(high)⇒RAG` |

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
      "reason": "The chunks show sync_config_to_db calls _is_database_up_to_date (chunk 2), what gets skipped when DB is not up to date (early return after logging pending migrations in chunk 2), and init_app_in_ctx as the initializer that triggers sync_config_to_db (chunk 5).",
      "route": "RAG",
      "duration_ms": 8835
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
  "reason": "The chunks show sync_config_to_db calls _is_database_up_to_date (chunk 2), what gets skipped when DB is not up to date (early return after logging pending migrations in chunk 2), and init_app_in_ctx as the initializer that triggers sync_config_to_db (chunk 5).",
  "assessor_model": "qwen3.5:9b"
}
```

### `C19_extensions_watcher_def`

**Prompt:** Locate start_local_extensions_watcher_thread: which file defines it, what does it do with the Flask app argument, and from which create_app branch in superset/app.py is it invoked? List preconditions (debug/config flags) at the call site.
**Gold files:** `['superset/app.py', 'superset/extensions/local_extensions_watcher.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `GRAPH` | 2 | 1663 | 1.0 | 1.0 | 0.3 | 0.5 | 34728 | `rag_miss→need_STRUCTURE→graph_miss→need_STRUCTURE→graph_miss⇒GRAPH(hops=2)` |
| SELF_ROUTE | True | `LC` | 0 | 48672 | 1.0 | 0.6666666666666666 | 0.2 | 0.5 | 18500 | `sufficient=False(high)⇒LC` |

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
      "reason": "None of the retrieved chunks contain any reference to start_local_extensions_watcher_thread, its definition, Flask app argument handling, invocation location in create_app, or related preconditions.",
      "route": "LC",
      "duration_ms": 6307
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "The query requires locating where start_local_extensions_watcher_thread is defined (cross-file), which create_app branch invokes it, and preconditions at the call site\u2014all requiring graph neighbors and cross-file dependency information not present in current chunks.",
      "duration_ms": 5875,
      "can_expand": true
    },
    {
      "phase": "graph",
      "hops": 1,
      "sufficient": false,
      "confidence": "high",
      "reason": "The queried symbol start_local_extensions_watcher_thread does not appear in any of the 10 retrieved chunks, making it impossible to answer which file defines it, what it does with the Flask app argument, or from which create_app branch it is invoked.",
      "route": "LC",
      "duration_ms": 4983
    },
    {
      "phase": "structure_vs_context",
      "hops": 1,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "Query requires locating the function definition file, tracing its invocation from create_app, and identifying config flags at the call site - all cross-file dependency information not present in current chunks.",
      "duration_ms": 5146,
      "can_expand": true
    },
    {
      "phase": "graph",
      "hops": 2,
      "sufficient": false,
      "confidence": "high",
      "reason": "The queried symbol start_local_extensions_watcher_thread does not appear in any of the 10 retrieved chunks, making it impossible to answer which file defines it, what it does with the Flask app argument, or from which create_app branch it is invoked.",
      "route": "LC",
      "duration_ms": 4702
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
  "reason": "None of the retrieved chunks contain any reference to start_local_extensions_watcher_thread, its definition, Flask app argument handling, invocation location in create_app, or related preconditions.",
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

### `C20_configure_feature_flags_body`

**Prompt:** In SupersetAppInitializer.configure_feature_flags (superset/initialization), what does the method configure, which extension/feature-flag objects does it touch, and where is it ordered inside init_app relative to neighboring configure_* calls?
**Gold files:** `['superset/initialization/__init__.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `RAG` | 0 | 607 | None | 1.0 | 1.0 | 1.0 | 14795 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 607 | None | 0.8666666666666667 | 1.0 | 1.0 | 15661 | `sufficient=True(high)⇒RAG` |

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
      "reason": "The chunks show that configure_feature_flags calls feature_flag_manager.init_app(self.superset_app), and chunk [2] shows its exact position in init_app (after configure_logging() and before configure_db_encrypt()).",
      "route": "RAG",
      "duration_ms": 7335
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
  "reason": "The chunks show that configure_feature_flags calls feature_flag_manager.init_app(self.superset_app), and chunk [2] shows its exact position in init_app (after configure_logging() and before configure_db_encrypt()).",
  "assessor_model": "qwen3.5:9b"
}
```

### `C21_initializer_calls_sync`

**Prompt:** From SupersetAppInitializer.init_app (or closely related setup), find where the app's sync_config_to_db is invoked. Quote the call, name the enclosing initializer method, and list 2–3 nearby configure_* / init steps in the same phase.
**Gold files:** `['superset/initialization/__init__.py', 'superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `RAG` | 0 | 706 | 1.0 | 1.0 | 0.8 | 0.5 | 13584 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 706 | 1.0 | 1.0 | 0.8 | 0.5 | 13688 | `sufficient=True(high)⇒RAG` |

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
      "reason": "Chunk [2] contains the exact quoted call self.superset_app.sync_config_to_db() within init_app_in_ctx method, along with multiple nearby configure_* steps like configure_fab, configure_url_map_converters, and configure_data_sources in the same phase.",
      "route": "RAG",
      "duration_ms": 7293
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
  "reason": "Chunk [2] contains the exact quoted call self.superset_app.sync_config_to_db() within init_app_in_ctx method, along with multiple nearby configure_* steps like configure_fab, configure_url_map_converters, and configure_data_sources in the same phase.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C22_config_path_env`

**Prompt:** In superset/config.py (or related config loading), how is SUPERSET_CONFIG_PATH (or equivalent CONFIG_PATH_ENV_VAR) defined and intended to be used? Quote the constant and explain how create_app's config import path relates to it.
**Gold files:** `['superset/config.py', 'superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `LC` | 0 | 10010 | 1.0 | 1.0 | 0.0 | 0.0 | 32677 | `rag_miss→need_CONTEXT⇒LC(hops=0)` |
| SELF_ROUTE | True | `LC` | 0 | 10010 | 0.8333333333333334 | 0.8333333333333334 | 0.0 | 0.0 | 14024 | `sufficient=False(high)⇒LC` |

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
      "reason": "None of the retrieved chunks contain any information about SUPERSET_CONFIG_PATH, CONFIG_PATH_ENV_VAR, config.py, or create_app's config import path - all chunks are from superset/sql/parse.py and discuss SQL parsing functions instead.",
      "route": "LC",
      "duration_ms": 5945
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "CONTEXT",
      "confidence": "high",
      "reason": "The query asks for definitions in superset/config.py, but the provided chunks are from superset/sql/parse.py, requiring context from the correct file.",
      "duration_ms": 19748,
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
  "reason": "None of the retrieved chunks contain any information about SUPERSET_CONFIG_PATH, CONFIG_PATH_ENV_VAR, config.py, or create_app's config import path - all chunks are from superset/sql/parse.py and discuss SQL parsing functions instead.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C23_cli_create_app_wiring`

**Prompt:** Inspect superset/cli/main.py create_app: what does it return, does it delegate to the main app factory, and how is it wired for the CLI (FlaskGroup / app factory pattern)? Contrast in 3 bullets with superset/app.py:create_app's responsibilities (middleware, watcher, config).
**Gold files:** `['superset/cli/main.py', 'superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `LC` | 0 | 15283 | 1.0 | 0.0 | 0.6 | 1.0 | 26627 | `rag_miss→need_CONTEXT⇒LC(hops=0)` |
| SELF_ROUTE | True | `LC` | 0 | 15283 | 1.0 | 1.0 | 0.6 | 1.0 | 19863 | `sufficient=False(high)⇒LC` |

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
      "reason": "The chunks show that cli/main.py create_app delegates to superset/app.py create_app, but chunk [1] is truncated and doesn't show the watcher configuration or complete CLI wiring (FlaskGroup integration) needed for the requested contrast.",
      "route": "LC",
      "duration_ms": 9703
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "CONTEXT",
      "confidence": "high",
      "reason": "Need full superset/app.py:create_app body (cut off mid-function) to see watcher/config responsibilities, plus CLI file imports/structure to understand FlaskGroup wiring",
      "duration_ms": 6369,
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
  "reason": "The chunks show that cli/main.py create_app delegates to superset/app.py create_app, but chunk [1] is truncated and doesn't show the watcher configuration or complete CLI wiring (FlaskGroup integration) needed for the requested contrast.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C24_create_app_error_paths`

**Prompt:** In superset/app.py create_app, what error handling surrounds config loading and app initialization? Mention try/except or logging behavior, what happens on config import failure, and whether the function can exit without returning a fully configured app.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `LC` | 0 | 25052 | 1.0 | 0.0 | 0.2 | 1.0 | 19377 | `rag_miss→need_CONTEXT⇒LC(hops=0)` |
| SELF_ROUTE | True | `LC` | 0 | 25052 | 1.0 | 0.9473684210526315 | 0.2 | 1.0 | 19902 | `sufficient=False(high)⇒LC` |

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
      "reason": "The retrieved chunks only show the beginning of the try block in create_app but do not reveal any except clauses, logging behavior, or what happens when config loading fails.",
      "route": "LC",
      "duration_ms": 7763
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "CONTEXT",
      "confidence": "high",
      "reason": "The query asks about error handling behavior in create_app which requires seeing more of the same file's code including try/except blocks, logging statements, and what happens after config loading to determine if errors are caught or re-raised.",
      "duration_ms": 3816,
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
  "reason": "The retrieved chunks only show the beginning of the try block in create_app but do not reveal any except clauses, logging behavior, or what happens when config loading fails.",
  "assessor_model": "qwen3.5:9b"
}
```

### `C25_init_app_pre_hooks`

**Prompt:** At the start of SupersetAppInitializer.init_app, what pre-initialization hooks or setup steps run before the main configure_* sequence? Name the methods/calls in order and state their purpose briefly from the code.
**Gold files:** `['superset/initialization/__init__.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `GRAPH` | 1 | 1782 | 1.0 | 1.0 | 1.0 | 1.0 | 40639 | `rag_miss→need_STRUCTURE→graph_ok⇒GRAPH(hops=1)` |
| SELF_ROUTE | True | `LC` | 0 | 65370 | 1.0 | 1.0 | 1.0 | 1.0 | 15880 | `sufficient=False(high)⇒LC` |

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
      "reason": "The retrieved chunks show individual configure_* methods but do not include the init_app method or any documentation about pre-initialization hooks that run BEFORE the main configure_* sequence, which is what the query specifically asks for.",
      "route": "LC",
      "duration_ms": 8029
    },
    {
      "phase": "structure_vs_context",
      "hops": 0,
      "need": "STRUCTURE",
      "confidence": "high",
      "reason": "The query asks for pre-initialization hooks that run before configure_* methods in init_app, requiring visibility into the init_app method body and its call order/dependencies to understand what happens first.",
      "duration_ms": 6282,
      "can_expand": true
    },
    {
      "phase": "graph",
      "hops": 1,
      "sufficient": true,
      "confidence": "high",
      "reason": "Chunk [1] shows the complete init_app method sequence with all method calls in order, and chunks [3-6] provide code bodies for configure_middlewares, configure_celery, configure_wtf, and configure_cache that explain their purposes.",
      "route": "RAG",
      "duration_ms": 19247
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
  "reason": "The retrieved chunks show individual configure_* methods but do not include the init_app method or any documentation about pre-initialization hooks that run BEFORE the main configure_* sequence, which is what the query specifically asks for.",
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

### `C6_sync_config`

**Prompt:** In superset/app.py, when and why is SupersetApp.sync_config_to_db called? Summarize its high-level steps, name the key helper methods it invokes (including any database up-to-date check), and quote the early-return condition if the DB is not ready.
**Gold files:** `['superset/app.py']`

| Mode | ok | route | hops | tokens | faith | ans_rel | P | R | wall_ms | path |
|------|----|-------|------|--------|-------|---------|---|---|---------|------|
| GASR | True | `RAG` | 0 | 1785 | None | 1.0 | 0.6 | 1.0 | 16394 | `rag_ok⇒RAG(hops=0)` |
| SELF_ROUTE | True | `RAG` | 0 | 1785 | 1.0 | 1.0 | 0.6 | 1.0 | 17413 | `sufficient=True(high)⇒RAG` |

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
      "reason": "The retrieved chunks contain the full sync_config_to_db method with its docstring explaining when/why it's called, the _is_database_up_to_date helper method implementation, and the early-return condition for pending migrations.",
      "route": "RAG",
      "duration_ms": 7907
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
  "reason": "The retrieved chunks contain the full sync_config_to_db method with its docstring explaining when/why it's called, the _is_database_up_to_date helper method implementation, and the early-return condition for pending migrations.",
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

