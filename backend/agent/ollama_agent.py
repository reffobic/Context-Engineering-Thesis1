"""
Ollama-backed agent for code generation with persistent memory.

The agent uses:
- Short-term memory: recent conversation history in the current thread
- Long-term memory: optional Mem0 persistent memory
- Code context: supplied separately through generate_with_context()

Requirements:
    pip install mem0ai

Ollama:
    ollama pull qwen3.5:9b

Mem0 runs fully locally:
- LLM via Ollama (same model/base_url as this agent)
- Embeddings via HuggingFace sentence-transformers (all-MiniLM-L6-v2)
- Vectors stored under <project>/.mem0/ (override with MEM0_DIR)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Mem0 creates $MEM0_DIR (default ~/.mem0) at import time.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MEM0_DIR = _PROJECT_ROOT / ".mem0"
if not os.environ.get("MEM0_DIR"):
    os.environ["MEM0_DIR"] = str(_DEFAULT_MEM0_DIR)
Path(os.environ["MEM0_DIR"]).mkdir(parents=True, exist_ok=True)

_MEM0_IMPORT_ERROR: Optional[BaseException] = None
try:
    from mem0 import Memory as Mem0Memory  # type: ignore
except Exception as e:  # pragma: no cover - optional dependency
    Mem0Memory = None  # type: ignore
    _MEM0_IMPORT_ERROR = e

from injection.prompt_builder import (
    DEFAULT_SYSTEM_PROMPT,
    ContextChunk,
    PromptBuilder,
    build_coding_prompt,
)

# Local embedder dims for all-MiniLM-L6-v2
_MEM0_EMBEDDING_DIMS = 384
_MEM0_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class OllamaAgent:
    """
    Ollama coding agent with short-term chat history and optional Mem0
    long-term memory.

    Short-term memory:
        Recent messages from the current conversation/thread.

    Long-term memory:
        Relevant persistent information retrieved from Mem0.

    Code context:
        Retrieved code chunks supplied through generate_with_context().
    """

    def __init__(
        self,
        model: str = "qwen3.5:9b",
        base_url: str = "http://localhost:11434",
        timeout: int = 600,
        *,
        think: Optional[bool] = None,
        memory_store: Optional[Any] = None,
        memory_user_id: Optional[str] = None,
        max_history_messages: int = 20,
        max_memories: int = 5,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        # ---------------------------------------------------------
        # Memory configuration
        # ---------------------------------------------------------

        self.memory_user_id = (
            memory_user_id
            or os.environ.get("MEM0_USER_ID")
            or "default-user"
        )

        # Keep only the most recent messages in the active thread.
        # Set to None if you intentionally want unlimited history.
        self.max_history_messages = (
            max_history_messages
            if max_history_messages is not None
            and max_history_messages > 0
            else None
        )

        # Number of relevant long-term memories to retrieve.
        self.max_memories = max_memories

        # Current conversation/thread history.
        self.chat_history: List[str] = []

        # Mem0 is optional.
        self.memory_store = (
            memory_store
            if memory_store is not None
            else self._build_default_memory_store()
        )

        # ---------------------------------------------------------
        # Ollama configuration
        # ---------------------------------------------------------

        if think is not None:
            self.think = think
        else:
            env = os.environ.get("OLLAMA_THINK", "0").strip().lower()
            self.think = env in ("1", "true", "yes", "on")

        raw = os.environ.get("OLLAMA_NUM_PREDICT", "8192")

        try:
            self.num_predict = int(raw)
        except ValueError:
            self.num_predict = 8192

    # =============================================================
    # MEMORY
    # =============================================================

    def _mem0_config(self) -> dict[str, Any]:
        """Fully local Mem0 config: Ollama LLM + HF embeddings + Chroma on disk."""
        mem0_dir = Path(os.environ.get("MEM0_DIR", str(_DEFAULT_MEM0_DIR)))
        mem0_dir.mkdir(parents=True, exist_ok=True)
        chroma_path = mem0_dir / "chroma"
        chroma_path.mkdir(parents=True, exist_ok=True)

        return {
            "llm": {
                "provider": "ollama",
                "config": {
                    "model": self.model,
                    "temperature": 0.1,
                    "max_tokens": 2000,
                    "ollama_base_url": self.base_url,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": os.environ.get(
                        "MEM0_EMBEDDING_MODEL",
                        _MEM0_EMBEDDING_MODEL,
                    ),
                    "embedding_dims": _MEM0_EMBEDDING_DIMS,
                },
            },
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": "mem0",
                    "path": str(chroma_path),
                },
            },
        }

    def _build_default_memory_store(self) -> Optional[Any]:
        """
        Create a local Mem0 Memory instance if the package is installed.

        Uses Ollama for the memory LLM and a local HuggingFace embedder so
        no OpenAI API key is required. Data lives under MEM0_DIR.

        If Mem0 is unavailable, the agent continues with short-term history only.
        """

        if Mem0Memory is None:
            detail = f" ({_MEM0_IMPORT_ERROR})" if _MEM0_IMPORT_ERROR else ""
            print(
                "[Memory] Mem0 is not available"
                f"{detail}. Long-term memory disabled."
            )
            return None

        try:
            if hasattr(Mem0Memory, "from_config"):
                memory = Mem0Memory.from_config(self._mem0_config())
            else:
                memory = Mem0Memory(config=self._mem0_config())
            print(
                "[Memory] Mem0 enabled "
                f"(dir={os.environ.get('MEM0_DIR')}, llm=ollama/{self.model})."
            )
            return memory
        except Exception as e:
            print(f"[Memory] Failed to initialize Mem0: {e}")
            return None

    def _search_long_term_memory(self, query: str) -> List[str]:
        """
        Retrieve memories from Mem0 that are relevant to the current query.
        """

        if self.memory_store is None:
            return []

        if not query.strip():
            return []

        if not hasattr(self.memory_store, "search"):
            return []

        try:
            # Newer Mem0: filters={"user_id": ...}, top_k=...
            # Older Mem0: user_id=..., limit=...
            try:
                results = self.memory_store.search(
                    query,
                    filters={"user_id": self.memory_user_id},
                    top_k=self.max_memories,
                )
            except TypeError:
                results = self.memory_store.search(
                    query,
                    user_id=self.memory_user_id,
                    limit=self.max_memories,
                )

            if not results:
                return []

            # Mem0 normally returns:
            #
            # {
            #     "results": [
            #         {
            #             "memory": "...",
            #             ...
            #         }
            #     ]
            # }

            if isinstance(results, dict):
                results = results.get("results", [])

            memories: List[str] = []

            for item in results:
                if isinstance(item, dict):
                    memory = item.get("memory")

                    if memory:
                        memories.append(str(memory))

                elif isinstance(item, str):
                    memories.append(item)

            return memories[: self.max_memories]

        except Exception as e:
            # Do not crash the agent if Mem0 retrieval fails.
            print(f"[Memory] Search failed: {e}")
            return []

    def _format_short_term_memory(self) -> str:
        """
        Format recent conversation history.
        """

        if not self.chat_history:
            return ""

        entries = self.chat_history

        if self.max_history_messages is not None:
            entries = entries[-self.max_history_messages :]

        return "\n\n".join(entries)

    def _format_long_term_memory(
        self,
        memories: List[str],
    ) -> str:
        """
        Convert retrieved Mem0 memories into a prompt section.
        """

        if not memories:
            return ""

        lines = [
            "Relevant information remembered from previous interactions:"
        ]

        for memory in memories:
            lines.append(f"- {memory}")

        return "\n".join(lines)

    def _build_memory_context(self, prompt: str) -> str:
        """
        Build the conversational memory context.

        The model receives:
        1. Recent conversation from the current thread
        2. Relevant long-term memories from Mem0
        3. The current user message
        """

        long_term_memories = self._search_long_term_memory(prompt)
        short_term = self._format_short_term_memory()
        long_term = self._format_long_term_memory(long_term_memories)

        sections = []

        if short_term:
            sections.append("Recent conversation:\n" + short_term)

        if long_term:
            sections.append(long_term)

        sections.append("Current user message:\n" + prompt)

        return "\n\n".join(sections)

    def _remember_chat_turn(
        self,
        prompt: str,
        response: str,
    ) -> None:
        """
        Store the current exchange.

        The exchange is stored in:
        - short-term thread history
        - Mem0 long-term memory
        """

        if not prompt and not response:
            return

        # ---------------------------------------------------------
        # Short-term memory
        # ---------------------------------------------------------

        entry = (
            f"User: {prompt.strip()}\n"
            f"Assistant: {response.strip()}"
        )

        self.chat_history.append(entry)

        if self.max_history_messages is not None:
            self.chat_history = self.chat_history[-self.max_history_messages :]

        # ---------------------------------------------------------
        # Long-term memory
        # ---------------------------------------------------------

        if self.memory_store is None:
            return

        try:
            if hasattr(self.memory_store, "add"):
                messages = [
                    {"role": "user", "content": prompt.strip()},
                    {"role": "assistant", "content": response.strip()},
                ]
                self.memory_store.add(
                    messages,
                    user_id=self.memory_user_id,
                )
                return

            if hasattr(self.memory_store, "create"):
                self.memory_store.create(
                    entry,
                    user_id=self.memory_user_id,
                )

        except Exception as e:
            # Memory failure should not prevent the coding agent
            # from returning the generated answer.
            print(f"[Memory] Failed to store memory: {e}")

    # =============================================================
    # OLLAMA
    # =============================================================

    def _ollama_options(self) -> dict[str, Any]:
        """
        Ollama generation options.
        """

        return {
            "num_predict": self.num_predict,
        }

    def generate_response(
        self,
        prompt: str,
        system: Optional[str] = None,
    ) -> str:
        """
        Generate a response using Ollama.

        Before Ollama:
            1. Search Mem0 for relevant long-term memories.
            2. Add recent thread history.
            3. Add the current user message.

        After Ollama:
            4. Store the exchange in short-term memory.
            5. Store the exchange in Mem0.
        """

        prompt_with_memory = self._build_memory_context(prompt)

        if system is not None:
            response = self._chat(
                system,
                prompt_with_memory,
            )
        else:
            response = self._generate(prompt_with_memory)

        self._remember_chat_turn(
            prompt,
            response,
        )

        return response

    def _generate(self, prompt: str) -> str:
        url = f"{self.base_url}/api/generate"

        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "think": self.think,
            "options": self._ollama_options(),
        }

        return self._post_json(
            url,
            body,
            response_key="response",
        )

    def _chat(
        self,
        system: str,
        user: str,
    ) -> str:
        url = f"{self.base_url}/api/chat"

        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system,
                },
                {
                    "role": "user",
                    "content": user,
                },
            ],
            "stream": False,
            "think": self.think,
            "options": self._ollama_options(),
        }

        raw = self._post_json_raw(
            url,
            body,
        )

        msg = raw.get("message") or {}

        content = (msg.get("content") or "").strip()

        if content:
            return content

        thinking = (msg.get("thinking") or "").strip()

        if thinking:
            return thinking

        raise RuntimeError(f"Ollama returned no response content: {raw}")

    # =============================================================
    # HTTP
    # =============================================================

    def _post_json(
        self,
        url: str,
        body: dict,
        response_key: str,
    ) -> str:

        raw = self._post_json_raw(
            url,
            body,
        )

        response = (raw.get(response_key) or "").strip()

        if not response:
            raise RuntimeError(
                f"Ollama returned no '{response_key}' field: {raw}"
            )

        return response

    def _post_json_raw(
        self,
        url: str,
        body: dict,
    ) -> dict:

        data = json.dumps(body).encode("utf-8")

        req = Request(
            url,
            data=data,
            method="POST",
        )

        req.add_header(
            "Content-Type",
            "application/json",
        )

        try:
            with urlopen(
                req,
                timeout=self.timeout,
            ) as resp:

                raw_text = resp.read().decode()

                try:
                    return json.loads(raw_text)
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"Ollama returned invalid JSON: {raw_text}"
                    ) from e

        except HTTPError as e:
            error_body = ""

            try:
                error_body = e.read().decode()
            except Exception:
                pass

            raise RuntimeError(
                f"Ollama HTTP error {e.code}: {error_body or e.reason}"
            ) from e

        except URLError as e:
            raise RuntimeError(
                "Could not connect to Ollama at "
                f"{self.base_url}. "
                "Make sure Ollama is installed and running."
            ) from e

    # =============================================================
    # CODE GENERATION WITH RETRIEVED CONTEXT
    # =============================================================

    def generate_with_context(
        self,
        user_task: str,
        chunks: List[ContextChunk],
        *,
        system_prompt: Optional[str] = None,
        extra_dependencies: Optional[str] = None,
        max_context_tokens: Optional[int] = None,
        prompt_builder: Optional[PromptBuilder] = None,
    ) -> str:
        """
        Full coding-agent path:

            code retrieval
                ↓
            PromptBuilder
                ↓
            memory retrieval
                ↓
            Ollama
        """

        builder = prompt_builder or PromptBuilder(
            system_prompt=(system_prompt or DEFAULT_SYSTEM_PROMPT)
        )

        system, user = builder.build(
            user_task,
            chunks,
            extra_dependencies=extra_dependencies,
            max_context_tokens=max_context_tokens,
        )

        return self.generate_response(
            user,
            system=system,
        )


# =============================================================
# FUNCTIONAL SHORTCUT
# =============================================================


def generate_response(
    prompt: str,
    *,
    model: str = "qwen3.5:9b",
    system: Optional[str] = None,
) -> str:

    return OllamaAgent(model=model).generate_response(
        prompt,
        system=system,
    )


# =============================================================
# SMOKE TEST
# =============================================================

if __name__ == "__main__":

    import sys

    demo_chunks: List[ContextChunk] = [
        {
            "path": "example.py",
            "symbol": "hello",
            "language": "python",
            "text": (
                "def hello():\n"
                "    return 'world'\n"
            ),
        }
    ]

    agent = OllamaAgent(
        model=(sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:9b")
    )

    print("--- Memory configuration ---")
    print(
        "Mem0:",
        "enabled" if agent.memory_store is not None else "disabled",
    )

    print(
        "User ID:",
        agent.memory_user_id,
    )

    print(
        "History limit:",
        agent.max_history_messages,
    )

    print("\n--- Built user message (preview) ---")

    sys_p, user = build_coding_prompt(
        "Add type hints to hello().",
        demo_chunks,
    )

    print(
        "SYSTEM (first 200 chars):",
        sys_p[:200],
        "...",
    )

    print(
        "USER (first 500 chars):",
        user[:500],
        "...",
    )

    print("\n--- Calling Ollama ---")

    if os.environ.get("OLLAMA_SKIP") == "1":

        print("Skipped (OLLAMA_SKIP=1)")

    else:

        try:
            out = agent.generate_with_context(
                "Add type hints to hello().",
                demo_chunks,
            )

            print(out[:2000] if out else "(empty response)")

        except Exception as e:
            print(f"\nERROR: {e}")
