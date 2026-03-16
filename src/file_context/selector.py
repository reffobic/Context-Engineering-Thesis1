"""
File-level context selector: given a file map and a user change request,
returns the minimal set of files an LLM needs to implement that change.

Uses an LLM (Ollama locally, or mock for tests) to answer:
"What are the need context (min list of files) to answer the following user question/request?"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from .file_map import FileMap


@dataclass
class SelectionResult:
    """Result of file context selection."""

    selected_paths: List[str]
    reasoning: str
    raw_response: str = ""

    @property
    def must_have(self) -> List[str]:
        return self.selected_paths  # selector returns one list; gold has must/should split


# ---------------------------------------------------------------------------
# LLM client interface
# ---------------------------------------------------------------------------

class LLMClient:
    """Minimal interface: complete(prompt) -> str."""

    def complete(self, prompt: str) -> str:
        raise NotImplementedError


class OllamaClient(LLMClient):
    """
    Call local Ollama API (e.g. ollama run llama2).
    Expects Ollama running at http://localhost:11434.
    """

    def __init__(self, model: str = "llama2", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, prompt: str) -> str:
        url = f"{self.base_url}/api/generate"
        body = {"model": self.model, "prompt": prompt, "stream": False}
        data = json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=120) as resp:
                out = json.loads(resp.read().decode())
                return out.get("response", "")
        except URLError as e:
            return f"[Ollama error: {e}. Is Ollama running? Try: ollama run llama2]"


class MockFileSelectorClient(LLMClient):
    """
    Deterministic mock: for known requests returns a fixed list of files
    so tests run without Ollama.
    """

    def complete(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "image" in prompt_lower and ("upload" in prompt_lower or "custom chat" in prompt_lower):
            return (
                "FILES:\n"
                "services/chatbot_context_builder.py\n"
                "utils/llm_client.py\n"
                "schemas/chat.py\n"
                "api/chatbot_sessions.py\n"
                "services/chatbot_chat_service.py\n"
                "models/chat_message.py\n"
                "utils/validators.py\n"
                "services/chatbot_file_service.py\n"
                "utils/storage.py\n"
                "models/chatbot_file.py\n"
                "config.py\n"
                "EXPLANATION: Must-have files implement multimodal payloads, API, schema, and storage; "
                "should-have files show existing file handling and config for vision model."
            )
        if "vector database" in prompt_lower or "rag" in prompt_lower and "vector" in prompt_lower:
            return (
                "Key files for changing vector DB:\n"
                "rag/vector_store.py - abstraction for Chroma/Pinecone/Weaviate\n"
                "config.py - store vector DB connection settings\n"
                "rag/pipeline.py - uses vector store for indexing\n"
                "services/rag_chat_service.py - uses retriever backed by vector store"
            )
        # Apache Subversion codebase: checksum / build / backend
        if "subversion" in prompt_lower or "checksum" in prompt_lower or "xdigest" in prompt_lower:
            return (
                "FILES:\n"
                "build.conf\n"
                "subversion/libsvn_subr\n"
                "CMakeLists.txt\n"
                "EXPLANATION: build.conf defines checksum backend linkage; libsvn_subr has "
                "checksum and crypto utilities; CMakeLists.txt wires the build."
            )
        return "Relevant files: config.py, main.py (review full file map for your request)."


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

SELECT_PROMPT = """You are a context window optimization agent. Given a project described by a list of files and their purpose (no code content), output the MINIMAL set of files a developer (or an LLM) needs to read to implement the following user request. Do not include tests or auth unless the request explicitly needs them.

PROJECT FILES (path + purpose):
{file_map}

USER REQUEST:
{user_request}

Output format:
1. List each file path one per line under "FILES:" (use the exact path as shown above).
2. Then write "EXPLANATION:" and in 1-3 sentences say why these files are the minimal necessary set.

Your response:"""


class FileContextSelector:
    """
    Selects minimal file set for a user change request using an LLM.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or MockFileSelectorClient()

    def select(self, file_map: FileMap, user_request: str) -> SelectionResult:
        prompt = SELECT_PROMPT.format(
            file_map=file_map.to_prompt_text(),
            user_request=user_request.strip(),
        )
        raw = self.llm.complete(prompt)
        paths = self._parse_paths_from_response(raw, file_map.paths())
        reasoning = self._extract_explanation(raw)
        return SelectionResult(
            selected_paths=paths,
            reasoning=reasoning.strip() or raw[:500],
            raw_response=raw,
        )

    @staticmethod
    def _parse_paths_from_response(response: str, valid_paths: List[str]) -> List[str]:
        valid_set = set(valid_paths)
        found = []
        # Look for "FILES:" block or any line that looks like a path (contains / and .py or no extension)
        in_files = False
        for line in response.splitlines():
            line = line.strip()
            if line.upper().startswith("FILES:"):
                in_files = True
                line = line[6:].strip()
            elif line.upper().startswith("EXPLANATION:"):
                in_files = False
            if in_files and line:
                # Strip leading bullets/dashes
                path = re.sub(r"^[\-\*\d\.]+\s*", "", line).strip()
                if path in valid_set:
                    found.append(path)
                else:
                    # Try to match partial (e.g. "api/chatbot_sessions.py" in valid_set)
                    for v in valid_set:
                        if v == path or v.endswith(path) or path in v:
                            if v not in found:
                                found.append(v)
                            break
        # If no FILES: block, scan whole response for valid paths
        if not found:
            for v in valid_set:
                if v in response and v not in found:
                    found.append(v)
        return list(dict.fromkeys(found))  # preserve order, no dupes

    @staticmethod
    def _extract_explanation(response: str) -> str:
        for marker in ["EXPLANATION:", "Explain:", "Why:"]:
            if marker in response:
                idx = response.index(marker) + len(marker)
                return response[idx:].strip().split("\n")[0]
        return ""
