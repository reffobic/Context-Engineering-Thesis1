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
import re
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

# Process-wide singleton so short-term history survives across callers
# (agent_endpoint, generate_response shortcut, tests) within one server process.
_SHARED_AGENT: Optional["OllamaAgent"] = None


def get_shared_agent(
    model: str = "qwen3.5:9b",
    **kwargs: Any,
) -> "OllamaAgent":
    """Return the process-wide agent, creating it once."""
    global _SHARED_AGENT
    if _SHARED_AGENT is None:
        _SHARED_AGENT = OllamaAgent(model=model, **kwargs)
    return _SHARED_AGENT


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

        # Short-term history as Ollama chat messages (role/content dicts).
        # Loaded from disk so uvicorn --reload does not wipe the thread.
        self._history_path = (
            Path(os.environ.get("MEM0_DIR", str(_DEFAULT_MEM0_DIR)))
            / f"chat_history_{self.memory_user_id}.json"
        )
        self._context_path = (
            Path(os.environ.get("MEM0_DIR", str(_DEFAULT_MEM0_DIR)))
            / f"last_code_context_{self.memory_user_id}.json"
        )
        self.messages: List[dict[str, str]] = self._load_messages()
        # Keep the most recent retrieved code context separately from chat
        # history.  It lets follow-up questions refer to the previous function
        # even though we deliberately keep large RAG dumps out of the thread.
        self._last_code_context: str = self._load_last_code_context()

        # Back-compat alias used by older call sites / debugging.
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

    def _load_messages(self) -> List[dict[str, str]]:
        """Load persisted short-term chat messages from disk."""
        try:
            if not self._history_path.is_file():
                return []
            raw = json.loads(self._history_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            messages: List[dict[str, str]] = []
            for item in raw:
                if (
                    isinstance(item, dict)
                    and isinstance(item.get("role"), str)
                    and isinstance(item.get("content"), str)
                ):
                    messages.append(
                        {"role": item["role"], "content": item["content"]}
                    )
            return messages
        except Exception as e:
            print(f"[Memory] Failed to load chat history: {e}")
            return []

    def _save_messages(self) -> None:
        """Persist short-term chat messages so reloads keep the thread."""
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(
                json.dumps(self.messages, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Memory] Failed to save chat history: {e}")

    def _load_last_code_context(self) -> str:
        """Load the compact code context belonging to the latest code turn."""
        try:
            if not self._context_path.is_file():
                return ""
            raw = json.loads(self._context_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("context"), str):
                return raw["context"]
        except Exception as e:
            print(f"[Memory] Failed to load last code context: {e}")
        return ""

    def _save_last_code_context(self, context: str) -> None:
        """Persist the latest code context for same-thread follow-up questions."""
        try:
            self._context_path.parent.mkdir(parents=True, exist_ok=True)
            self._context_path.write_text(
                json.dumps({"context": context}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[Memory] Failed to save last code context: {e}")

    def _trim_messages(self) -> None:
        """Keep system (if any) + the most recent user/assistant turns."""
        if self.max_history_messages is None:
            return

        system_msgs = [m for m in self.messages if m.get("role") == "system"]
        other = [m for m in self.messages if m.get("role") != "system"]
        # Each "turn" is roughly user+assistant; keep last N message dicts.
        other = other[-self.max_history_messages :]
        self.messages = system_msgs[:1] + other

    def clear_history(self) -> None:
        """Reset short-term conversation (does not wipe Mem0)."""
        self.messages = []
        self.chat_history = []
        self._last_code_context = ""
        try:
            if self._history_path.is_file():
                self._history_path.unlink()
            if self._context_path.is_file():
                self._context_path.unlink()
        except Exception as e:
            print(f"[Memory] Failed to clear chat history/context files: {e}")

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

    def _augment_user_prompt(self, prompt: str) -> str:
        """Attach retrieved long-term memories to the current user turn."""
        long_term = self._format_long_term_memory(
            self._search_long_term_memory(prompt)
        )
        if not long_term:
            return prompt
        return long_term + "\n\n" + prompt

    def _extract_task(self, prompt: str) -> str:
        """Pull the bare user question out of an injected RAG prompt."""
        match = re.search(
            r"<TASK>\s*(.*?)\s*</TASK>",
            prompt,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if match:
            task = match.group(1).strip()
            if task:
                return task
        return prompt.strip()

    def _compact_user_content(self, prompt: str) -> str:
        """
        Shrink a RAG user prompt for storage in history.

        Prior turns should keep the question/task, not the full retrieved
        code context (which would explode the token budget).
        """
        task = self._extract_task(prompt)
        if task and task != prompt.strip():
            return task

        stripped = prompt.strip()
        if len(stripped) > 1500:
            return stripped[:1500] + "\n...[truncated for chat history]"
        return stripped

    def _is_meta_memory_question(self, text: str) -> bool:
        """True when the user is asking about prior turns, not about code."""
        q = text.strip().lower()
        patterns = (
            r"\bwhat (was|were|did) (the )?(last|previous|prior)\b",
            r"\bwhat did i (just )?(ask|say|request|want)\b",
            r"\b(my|the) (last|previous|prior) (thing|question|query|prompt|message|request)\b",
            r"\bremind me what i (asked|said)\b",
            r"\bwhat was i (asking|talking) about\b",
            r"\blast thin[gk] i asked\b",
            r"\blast thing i asked\b",
        )
        return any(re.search(p, q) for p in patterns)

    def _is_referential_followup(self, text: str) -> bool:
        """
        True for underspecified follow-ups that depend on prior turns
        (pronouns, 'again', 'more', etc.).
        """
        q = text.strip().lower()
        if not q:
            return False
        if self._is_meta_memory_question(q):
            return False
        # Do NOT match bare "this/that" — "this codebase" is a normal coding query.
        patterns = (
            r"\bexplain it\b",
            r"\b(about|of|on|for) it\b",
            r"\bit again\b",
            r"\b(that|this|those|these) (one|part|bit|section|step|point|thing)\b",
            r"\bagain\b",
            r"\b(the )?(same|previous|last) (one|thing|topic|question|part|answer)\b",
            r"\b(more detail|in more detail|go deeper|elaborate|clarify)\b",
            r"\btell me more\b",
            r"\bwhat about (it|that|this)\b",
            r"\bcan you (explain|show|repeat|rephrase)\b",
            r"\bre-?explain\b",
            r"\bsay (that|it) (again|differently)\b",
        )
        if any(re.search(p, q) for p in patterns):
            return True
        return bool(
            re.fullmatch(
                r"(please\s+)?(explain|describe|show|repeat|clarify|continue|"
                r"yes|yep|ok|sure|more|again|go on|same)(\s+\w+){0,3}\??",
                q,
            )
        )

    def _is_assistant_referential_followup(self, text: str) -> bool:
        """
        True when the current question depends on the previous assistant reply.

        This check MUST happen before code/RAG retrieval.  A coding follow-up such
        as "what are its external dependencies?" is not a new code-search query:
        "its" refers to something explained in the previous answer.

        We intentionally require an existing assistant reply before treating
        pronouns as conversational references.  This avoids classifying a fresh
        standalone question such as "What is its purpose?" as a follow-up when
        there is no conversation context.
        """
        q = text.strip().lower()
        if not q or self._is_meta_memory_question(q):
            return False
        if not self._last_assistant_message():
            return False

        # Explicit references to the previous answer.
        explicit_patterns = (
            r"\b(the|those|these|your)\s+(\d+\s+)?(key\s+)?"
            r"(tasks?|steps?|points?|parts?|items?|reasons?|ways?|things?|"
            r"findings?|takeaways?|dependencies?|examples?|issues?|files?)\b",
            r"\bwhat (are|were|was|is)\s+(the|those|these|its|their|his|her)\b",
            r"\bwhat (does|did)\s+(it|that|this)\b",
            r"\bhow (does|did|is|was)\s+(it|that|this)\b",
            r"\bwhy (does|did|is|was)\s+(it|that|this)\b",
            r"\bwhere (does|did|is|was)\s+(it|that|this)\b",
            r"\bwhich (of (them|those|these)|one)\b",
            r"\b(it|its|they|their|them)\b.*\b("
            r"dependencies?|inputs?|outputs?|parameters?|arguments?|"
            r"callers?|callees?|uses?|usage|purpose|role|function|"
            r"implementation|behavior|logic|parts?|steps?|details?)\b",
            r"\byou (mentioned|listed|said|wrote|outlined|explained)\b",
            r"\bin your (last |previous )?(answer|response|reply|explanation)\b",
            r"\bthe (first|second|third|last|previous) "
            r"(one|step|task|point|part|item)\b",
            r"\bexpand on (the|that|those|this|it|point|step|task)\b",
            r"\bcan you (explain|show|repeat|rephrase|clarify) (it|that|this|those|these)\b",
        )
        if any(re.search(p, q) for p in explicit_patterns):
            return True

        # Short, underspecified questions containing a conversational pronoun.
        # These are almost always dependent on the preceding turn in a coding
        # conversation (e.g. "what about its inputs?", "and its callers?").
        if len(q.split()) <= 12 and re.search(
            r"\b(it|its|they|their|them|that|those|these|this)\b", q
        ):
            return True

        return False

    def _clean_topic(self, text: str) -> str:
        """Strip injected resolution / memory wrappers from a stored user turn."""
        content = (text or "").strip()
        if content.startswith("Relevant information remembered"):
            parts = content.split("\n\n", 1)
            content = parts[-1].strip() if len(parts) > 1 else content
        content = re.split(
            r"\n\s*Context from this conversation\b",
            content,
            maxsplit=1,
        )[0].strip()
        content = re.split(
            r"\n\s*Previous assistant reply\b",
            content,
            maxsplit=1,
        )[0].strip()
        content = re.split(
            r"\n\s*Follow-up question:\b",
            content,
            maxsplit=1,
        )[0].strip()
        return self._compact_user_content(content)

    def _last_assistant_message(self) -> str:
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant":
                return (msg.get("content") or "").strip()
        return ""

    def _last_real_user_topic(self) -> str:
        """Most recent substantive user topic (skips pure follow-up noise)."""
        prior = self._prior_user_tasks(skip_meta=True)
        for topic in reversed(prior):
            clean = self._clean_topic(topic)
            if not clean:
                continue
            if self._is_referential_followup(clean) and len(clean.split()) <= 6:
                continue
            if self._is_assistant_referential_followup(clean):
                continue
            return clean
        return self._clean_topic(prior[-1]) if prior else ""

    def resolve_referential_prompt(self, prompt: str) -> str:
        """
        Expand follow-ups before retrieval using the last real user topic.

        Assistant-referential questions ("what are the 3 key tasks") are left
        unchanged here and answered from chat history in ``generate_response``.
        """
        task = prompt.strip()
        if not task or self._is_meta_memory_question(task):
            return prompt
        # Don't rewrite — generate_response will answer from the last reply.
        if self._is_assistant_referential_followup(task):
            print(
                "[Memory] Assistant-referential follow-up detected; "
                "will answer from previous reply (skip topic rewrite)."
            )
            return prompt

        last_topic = self._last_real_user_topic()
        if not last_topic or not self._is_referential_followup(task):
            return prompt

        resolved = (
            f"{task}\n\n"
            f"Context from this conversation — the user is referring to their "
            f"previous question: {last_topic}"
        )
        print(f"[Memory] Follow-up resolved using prior topic: {last_topic[:120]!r}")
        return resolved

    def _prior_user_tasks(self, *, skip_meta: bool = False) -> List[str]:
        """Compact prior user turns already stored in ``self.messages``."""
        tasks: List[str] = []
        for msg in self.messages:
            if msg.get("role") != "user":
                continue
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            task = self._clean_topic(content)
            if not task:
                continue
            if skip_meta and self._is_meta_memory_question(task):
                continue
            tasks.append(task)
        return tasks

    def _build_assistant_followup_prompt(self, task: str) -> str:
        """
        Build a deterministic prompt for questions referring to the previous turn.

        The previous assistant reply is the primary source.  The last retrieved
        code context is included only as supporting evidence, so a follow-up
        such as "what are its external dependencies?" cannot trigger a fresh
        unrelated retrieval result.
        """
        last_assistant = self._last_assistant_message()
        last_topic = self._last_real_user_topic()

        if not last_assistant:
            return (
                f"The user asked: {task}\n\n"
                "There is no previous assistant reply stored. Say so briefly."
            )

        parts = [
            "Answer the follow-up question using the conversation immediately "
            "before it.",
            "First use the previous assistant reply as the primary context.",
            "If the answer requires a code-level detail that is not stated in "
            "that reply, use the previous retrieved code context below.",
            "Do NOT invent a new unrelated topic and do NOT perform a fresh "
            "interpretation of the user's question.",
            "",
            f"Original user topic:\n{last_topic or '(unknown)'}",
            "",
            f"Previous assistant reply:\n{last_assistant}",
        ]

        if self._last_code_context:
            parts.extend([
                "",
                "Code context retrieved for that previous topic:",
                self._last_code_context,
            ])

        parts.extend([
            "",
            f"Current follow-up question:\n{task}",
            "",
            "Answer directly and specifically. Resolve pronouns such as "
            "'it', 'its', 'they', and 'their' using the original topic and "
            "previous assistant reply.",
        ])
        return "\n".join(parts)

    def _build_meta_memory_prompt(self, task: str) -> str:
        """
        Build a RAG-free prompt for conversation-memory questions.

        Retrieved code CONTEXT often hijacks these answers (the model invents
        a 'previous question' from whatever files were retrieved). Quote prior
        turns explicitly so the model cannot miss them.
        """
        prior = self._prior_user_tasks(skip_meta=True)
        if not prior:
            return (
                "The user is asking about earlier messages in this conversation, "
                "but there are no previous user questions stored yet.\n\n"
                f"Current question:\n{task}\n\n"
                "Reply briefly that you do not have a previous question yet."
            )

        lines = [
            "Answer using ONLY the conversation history below.",
            "Do NOT use any retrieved code context. Do NOT invent a prior question.",
            "",
            "Previous user questions (oldest → newest):",
        ]
        for i, prev in enumerate(prior, start=1):
            lines.append(f'{i}. "{prev}"')
        lines.extend(
            [
                "",
                f'The most recent previous user question was: "{prior[-1]}"',
                "",
                f"Current question:\n{task}",
                "",
                "Answer the current question directly from the history above.",
            ]
        )
        return "\n".join(lines)

    def _remember_chat_turn(
        self,
        prompt: str,
        response: str,
    ) -> None:
        """
        Store the current exchange in Mem0 long-term memory.

        Short-term history is already stored in ``self.messages``.
        """

        if not prompt and not response:
            return

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
                entry = (
                    f"User: {prompt.strip()}\n"
                    f"Assistant: {response.strip()}"
                )
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
            2. Append the current user turn to the persistent message list.
            3. Send the full message history (not a fresh 2-message list).

        After Ollama:
            4. Append the assistant reply to the message list and save to disk.
            5. Store the exchange in Mem0.
        """

        task = self._extract_task(prompt)
        # If inject wrapped a resolved follow-up, prefer the bare question when present.
        followup_match = re.search(
            r"Follow-up question:\s*(.+?)(?:\n\n|\Z)",
            task,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if followup_match:
            task = followup_match.group(1).strip()
        task = self._clean_topic(task)

        meta = self._is_meta_memory_question(task)
        assistant_ref = self._is_assistant_referential_followup(task)

        if meta or assistant_ref:
            if assistant_ref:
                user_content = self._build_assistant_followup_prompt(task)
                meta_system = (
                    "You answer follow-up questions about your previous reply. "
                    "Use only that previous reply. Do not invent a different list "
                    "from retrieved code."
                )
                print(
                    "[Memory] Assistant-referential follow-up → LLM with prior reply."
                )
            else:
                user_content = self._build_meta_memory_prompt(task)
                meta_system = (
                    "You answer questions about this conversation's history. "
                    "Use only the prior user questions listed in the prompt. "
                    "Never invent a previous question from code snippets."
                )
                print(
                    f"[Memory] Meta question → LLM with quoted history "
                    f"(prior_turns={len(self._prior_user_tasks(skip_meta=True))})."
                )
            try:
                response = self._chat(
                    messages=[
                        {"role": "system", "content": meta_system},
                        {"role": "user", "content": user_content},
                    ]
                )
            except Exception:
                raise
            self.messages.append({"role": "user", "content": task})
            self.messages.append({"role": "assistant", "content": response})
            self._trim_messages()
            self._save_messages()
            print(
                f"[Memory] Short-term turns={sum(1 for m in self.messages if m.get('role')=='user')} "
                f"(messages={len(self.messages)}, saved={self._history_path.name})"
            )
            self._remember_chat_turn(task, response)
            return response

        user_content = self._augment_user_prompt(prompt)

        if system is not None:
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0] = {"role": "system", "content": system}
            else:
                self.messages.insert(0, {"role": "system", "content": system})

        # Compact older user turns so repeated RAG contexts don't pile up.
        for msg in self.messages:
            if msg.get("role") == "user":
                raw = msg.get("content") or ""
                if "<CONTEXT>" in raw or "Context from this conversation" in raw:
                    msg["content"] = self._clean_topic(raw)

        self.messages.append({"role": "user", "content": user_content})
        self._trim_messages()

        try:
            response = self._chat()
        except Exception:
            # Do not leave a dangling user turn if Ollama failed.
            if self.messages and self.messages[-1].get("role") == "user":
                self.messages.pop()
            raise

        # Store a compact clean topic (never the RAG dump / resolution wrapper).
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                self.messages[i]["content"] = self._clean_topic(prompt)
                break

        self.messages.append({"role": "assistant", "content": response})
        self._trim_messages()
        self._save_messages()

        print(
            f"[Memory] Short-term turns={sum(1 for m in self.messages if m.get('role')=='user')} "
            f"(messages={len(self.messages)}, saved={self._history_path.name})"
        )

        self._remember_chat_turn(prompt, response)

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

    def _chat(self, messages: Optional[List[dict[str, str]]] = None) -> str:
        """
        Call Ollama /api/chat with ``messages`` (or ``self.messages`` by default).

        Previously this rebuilt a fresh [system, user] list every call, which
        wiped conversational memory even when the agent singleton survived.
        """
        url = f"{self.base_url}/api/chat"

        body = {
            "model": self.model,
            "messages": messages if messages is not None else self.messages,
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

        # Keep the previous retrieval result available for conversational
        # follow-ups.  Do not put it into the normal chat history because that
        # would repeatedly inflate the context window.
        context_parts: List[str] = []
        for chunk in chunks:
            if isinstance(chunk, dict):
                path = str(chunk.get("path", ""))
                symbol = str(chunk.get("symbol", ""))
                chunk_text = str(chunk.get("text", ""))
                if chunk_text:
                    header = f"[{path}::{symbol}]" if symbol else f"[{path}]"
                    context_parts.append(f"{header}\n{chunk_text}")
            elif chunk:
                context_parts.append(str(chunk))

        if extra_dependencies:
            context_parts.append(f"[Extra dependencies]\n{extra_dependencies}")

        # Do not overwrite the previous turn's code context when this request
        # is itself a conversational follow-up.  The upstream retriever may
        # have produced unrelated chunks for an underspecified query such as
        # "what are its dependencies?", but those chunks must not replace the
        # context belonging to "its".
        if context_parts and not self._is_assistant_referential_followup(user_task):
            self._last_code_context = "\n\n".join(context_parts)
            self._save_last_code_context(self._last_code_context)

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
    # Reuse the process-wide agent so chat history is not reset every call.
    return get_shared_agent(model=model).generate_response(
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
