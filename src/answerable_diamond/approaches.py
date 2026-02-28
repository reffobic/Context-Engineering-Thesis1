"""
Approaches for the 'Answerable?' diamond in the Context Engineering Optimization Engine.

The diamond is the core routing decision: given a user query and a set of retrieved
RAG chunks, is the query answerable from those chunks alone — or do we need to
escalate to a more expensive retrieval strategy (LongRAG + FastGen)?

Literature basis:
  - SELF-ROUTE (Li et al., 2024): ask the LLM itself to declare "unanswerable"
    when the retrieved chunks are insufficient, then escalate to Long Context.
  - SELF-RAG  (Asai et al., 2023): special reflection tokens decide whether
    retrieval was needed and whether the output is supported.
  - Uncertainty-based detection (Alansari & Luqman, 2025): flag low-confidence
    outputs via token-level probability / semantic entropy.
  - Keyword / overlap heuristic: fast, zero-LLM-call baseline.

We implement four concrete approaches so Hana & Refai can compare them:

  Approach 1 — SELF-ROUTE (LLM-as-judge, no fine-tuning)
  Approach 2 — Keyword Overlap Heuristic (fast baseline)
  Approach 3 — Semantic Similarity Threshold (embedding-based)
  Approach 4 — Confidence / Uncertainty Estimation (token probability)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Decision(str, Enum):
    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"


@dataclass
class AnswerabilityResult:
    """Returned by every approach."""
    decision: Decision
    confidence: float          # 0.0 – 1.0  (1.0 = very certain)
    approach_name: str
    reasoning: str             # human-readable explanation
    metadata: dict = field(default_factory=dict)

    @property
    def is_answerable(self) -> bool:
        return self.decision == Decision.ANSWERABLE


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class AnswerabilityApproach:
    """Abstract base — subclass and implement `check()`."""

    name: str = "base"

    def check(
        self,
        query: str,
        retrieved_chunks: List[str],
        **kwargs,
    ) -> AnswerabilityResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Approach 1 — SELF-ROUTE  (LLM-as-judge)
# ---------------------------------------------------------------------------

SELF_ROUTE_PROMPT = """\
You are a context-quality judge.

TASK: Given the user query and the retrieved context chunks below, decide whether
the query can be answered correctly and completely from those chunks ALONE.

Rules:
- Reply with exactly one word on the first line: ANSWERABLE or UNANSWERABLE
- On the second line, give a one-sentence reason.
- Do NOT attempt to answer the query itself.

USER QUERY:
{query}

RETRIEVED CONTEXT CHUNKS:
{chunks}

Your decision (ANSWERABLE / UNANSWERABLE):"""


class SelfRouteApproach(AnswerabilityApproach):
    """
    SELF-ROUTE: prompt the LLM with the retrieved chunks and ask it to declare
    whether the query is answerable.  If it says UNANSWERABLE, we escalate to
    the full LongRAG + FastGen pipeline.

    Reference: Li et al. (2024) — "Retrieval Augmented Generation or Long
    Context LLMs? A Comprehensive Study and Hybrid Approach"
    """

    name = "SELF-ROUTE (LLM-as-judge)"

    def __init__(self, llm_client=None):
        """
        llm_client: any object with a `.complete(prompt: str) -> str` method.
        If None, a mock client is used (useful for unit tests without API keys).
        """
        self.llm = llm_client or _MockLLMClient()

    def check(
        self,
        query: str,
        retrieved_chunks: List[str],
        **kwargs,
    ) -> AnswerabilityResult:
        chunks_text = "\n\n---\n\n".join(
            f"[Chunk {i+1}]\n{c}" for i, c in enumerate(retrieved_chunks)
        )
        prompt = SELF_ROUTE_PROMPT.format(query=query, chunks=chunks_text)
        raw_response = self.llm.complete(prompt)

        first_line = raw_response.strip().split("\n")[0].strip().upper()
        reason_lines = raw_response.strip().split("\n")[1:]
        reason = " ".join(reason_lines).strip() if reason_lines else ""

        if "UNANSWERABLE" in first_line:
            decision = Decision.UNANSWERABLE
            confidence = 0.85
        elif "ANSWERABLE" in first_line:
            decision = Decision.ANSWERABLE
            confidence = 0.85
        else:
            # Fallback: treat ambiguous response as unanswerable (safe default)
            decision = Decision.UNANSWERABLE
            confidence = 0.40
            reason = f"Ambiguous LLM response: '{raw_response[:80]}'"

        return AnswerabilityResult(
            decision=decision,
            confidence=confidence,
            approach_name=self.name,
            reasoning=reason,
            metadata={"raw_llm_response": raw_response},
        )


# ---------------------------------------------------------------------------
# Approach 2 — Keyword Overlap Heuristic  (fast, zero API calls)
# ---------------------------------------------------------------------------

class KeywordOverlapApproach(AnswerabilityApproach):
    """
    Fast baseline: tokenise the query into content words, then check what
    fraction of them appear in the retrieved chunks.

    Threshold (default 0.5): if ≥50 % of query keywords are found in the
    chunks, we declare the query ANSWERABLE.

    Pros : no LLM call, deterministic, very fast.
    Cons : misses semantic paraphrases; ignores multi-hop reasoning needs.
    """

    name = "Keyword Overlap Heuristic"

    STOP_WORDS = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "on", "at", "by", "for", "with", "about",
        "against", "between", "into", "through", "during", "before", "after",
        "above", "below", "from", "up", "down", "out", "off", "over", "under",
        "again", "further", "then", "once", "and", "but", "or", "nor", "so",
        "yet", "both", "either", "neither", "not", "only", "own", "same",
        "than", "too", "very", "just", "what", "which", "who", "whom", "how",
        "when", "where", "why", "all", "each", "every", "few", "more", "most",
        "other", "some", "such", "no", "any", "i", "me", "my", "we", "our",
        "you", "your", "he", "him", "his", "she", "her", "it", "its", "they",
        "them", "their", "this", "that", "these", "those",
    }

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def _tokenize(self, text: str) -> set:
        tokens = re.findall(r"\b[a-z]+\b", text.lower())
        return {t for t in tokens if t not in self.STOP_WORDS and len(t) > 2}

    def check(
        self,
        query: str,
        retrieved_chunks: List[str],
        **kwargs,
    ) -> AnswerabilityResult:
        query_keywords = self._tokenize(query)
        if not query_keywords:
            return AnswerabilityResult(
                decision=Decision.UNANSWERABLE,
                confidence=0.5,
                approach_name=self.name,
                reasoning="No meaningful keywords found in query.",
            )

        combined_chunks = " ".join(retrieved_chunks)
        chunk_tokens = self._tokenize(combined_chunks)

        matched = query_keywords & chunk_tokens
        overlap_ratio = len(matched) / len(query_keywords)

        decision = Decision.ANSWERABLE if overlap_ratio >= self.threshold else Decision.UNANSWERABLE
        confidence = overlap_ratio if decision == Decision.ANSWERABLE else (1.0 - overlap_ratio)

        return AnswerabilityResult(
            decision=decision,
            confidence=round(confidence, 3),
            approach_name=self.name,
            reasoning=(
                f"{len(matched)}/{len(query_keywords)} query keywords found in chunks "
                f"(overlap={overlap_ratio:.0%}, threshold={self.threshold:.0%}). "
                f"Matched: {sorted(matched)}"
            ),
            metadata={
                "query_keywords": sorted(query_keywords),
                "matched_keywords": sorted(matched),
                "overlap_ratio": overlap_ratio,
            },
        )


# ---------------------------------------------------------------------------
# Approach 3 — Semantic Similarity Threshold  (embedding-based)
# ---------------------------------------------------------------------------

class SemanticSimilarityApproach(AnswerabilityApproach):
    """
    Compute cosine similarity between the query embedding and the mean of
    the retrieved chunk embeddings.  If similarity ≥ threshold, declare
    ANSWERABLE.

    Reference: Alansari & Luqman (2025) — embedding-based hallucination
    detection; also aligns with LongRAG's insight that retrieval unit size
    affects answer recall.

    Requires: sentence-transformers (or any embedding provider).
    Falls back to a mock if the library is unavailable.
    """

    name = "Semantic Similarity (Embedding)"

    def __init__(self, threshold: float = 0.60, embedding_model=None):
        self.threshold = threshold
        self._embed = embedding_model or _MockEmbedder()

    def _cosine(self, a: List[float], b: List[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x ** 2 for x in a) ** 0.5
        norm_b = sum(x ** 2 for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _mean_vec(self, vecs: List[List[float]]) -> List[float]:
        n = len(vecs)
        max_len = max(len(v) for v in vecs)
        padded = [v + [0.0] * (max_len - len(v)) for v in vecs]
        return [sum(p[i] for p in padded) / n for i in range(max_len)]

    def check(
        self,
        query: str,
        retrieved_chunks: List[str],
        **kwargs,
    ) -> AnswerabilityResult:
        if not retrieved_chunks:
            return AnswerabilityResult(
                decision=Decision.UNANSWERABLE,
                confidence=1.0,
                approach_name=self.name,
                reasoning="No chunks retrieved.",
            )

        # Encode chunks first so the shared vocabulary covers all words,
        # then re-encode query against the full vocabulary.
        chunk_vecs = [self._embed.encode(c) for c in retrieved_chunks]
        query_vec = self._embed.encode(query)
        # Pad all vectors to the same length (query may add new vocab words)
        max_len = max(len(query_vec), max(len(v) for v in chunk_vecs))
        query_vec = query_vec + [0.0] * (max_len - len(query_vec))
        chunk_vecs = [v + [0.0] * (max_len - len(v)) for v in chunk_vecs]
        mean_chunk_vec = self._mean_vec(chunk_vecs)
        similarity = self._cosine(query_vec, mean_chunk_vec)

        decision = Decision.ANSWERABLE if similarity >= self.threshold else Decision.UNANSWERABLE
        confidence = similarity if decision == Decision.ANSWERABLE else (1.0 - similarity)

        return AnswerabilityResult(
            decision=decision,
            confidence=round(confidence, 3),
            approach_name=self.name,
            reasoning=(
                f"Cosine similarity between query and mean chunk embedding = {similarity:.3f} "
                f"(threshold={self.threshold}). Decision: {decision.value}."
            ),
            metadata={"cosine_similarity": similarity},
        )


# ---------------------------------------------------------------------------
# Approach 4 — Confidence / Uncertainty Estimation
# ---------------------------------------------------------------------------

class ConfidenceEstimationApproach(AnswerabilityApproach):
    """
    Ask the LLM to generate an answer AND a self-assessed confidence score
    (0–10).  If confidence < threshold, declare UNANSWERABLE and escalate.

    This mirrors the uncertainty-based detection category in Alansari &
    Luqman (2025): "flag low-confidence outputs through token-level
    probability, semantic entropy, or supervised uncertainty heads."

    For black-box LLMs we use the self-reported confidence as a proxy.
    """

    name = "Confidence / Uncertainty Estimation"

    CONFIDENCE_PROMPT = """\
Answer the following question using ONLY the provided context.
After your answer, on a new line write:
CONFIDENCE: <integer 0-10>
where 0 = completely unsure / no relevant info, 10 = fully certain.

QUESTION: {query}

CONTEXT:
{chunks}

Your answer and confidence:"""

    def __init__(self, llm_client=None, confidence_threshold: int = 5):
        self.llm = llm_client or _MockLLMClient()
        self.confidence_threshold = confidence_threshold

    def check(
        self,
        query: str,
        retrieved_chunks: List[str],
        **kwargs,
    ) -> AnswerabilityResult:
        chunks_text = "\n\n---\n\n".join(retrieved_chunks)
        prompt = self.CONFIDENCE_PROMPT.format(query=query, chunks=chunks_text)
        raw = self.llm.complete(prompt)

        score = self._extract_confidence(raw)
        decision = Decision.ANSWERABLE if score >= self.confidence_threshold else Decision.UNANSWERABLE
        confidence = score / 10.0

        return AnswerabilityResult(
            decision=decision,
            confidence=round(confidence, 2),
            approach_name=self.name,
            reasoning=(
                f"LLM self-reported confidence = {score}/10 "
                f"(threshold={self.confidence_threshold}/10). "
                f"Decision: {decision.value}."
            ),
            metadata={"llm_confidence_score": score, "raw_response": raw},
        )

    @staticmethod
    def _extract_confidence(text: str) -> int:
        match = re.search(r"CONFIDENCE\s*:\s*(\d+)", text, re.IGNORECASE)
        if match:
            return min(10, max(0, int(match.group(1))))
        return 0  # conservative default if parsing fails


# ---------------------------------------------------------------------------
# Mock helpers (used when no real LLM / embedder is available)
# ---------------------------------------------------------------------------

class _MockLLMClient:
    """
    Deterministic mock for unit tests.
    Looks for simple keyword signals in the prompt to simulate LLM behaviour.
    """

    def complete(self, prompt: str) -> str:
        prompt_lower = prompt.lower()

        # SELF-ROUTE style
        if "answerable / unanswerable" in prompt_lower or "your decision" in prompt_lower:
            if any(kw in prompt_lower for kw in ["no information", "not mentioned", "unknown"]):
                return "UNANSWERABLE\nThe retrieved chunks do not contain relevant information."
            return "ANSWERABLE\nThe retrieved chunks contain sufficient information to answer the query."

        # Confidence style
        if "confidence:" in prompt_lower or "your answer and confidence" in prompt_lower:
            if any(kw in prompt_lower for kw in ["no information", "not mentioned"]):
                return "I cannot find the answer in the provided context.\nCONFIDENCE: 1"
            return "Based on the context, the answer is present.\nCONFIDENCE: 8"

        return "I don't know.\nCONFIDENCE: 0"


class _MockEmbedder:
    """
    Bag-of-words mock embedder with a shared global vocabulary.
    All vectors have the same length so cosine similarity is valid across
    different texts.  The vocabulary is built lazily from all encoded texts.
    """

    def __init__(self):
        self._vocab: List[str] = []
        self._vocab_set: set = set()

    def _update_vocab(self, words: List[str]) -> None:
        for w in words:
            if w not in self._vocab_set:
                self._vocab.append(w)
                self._vocab_set.add(w)

    def encode(self, text: str) -> List[float]:
        words = [w for w in re.findall(r"\b[a-z]+\b", text.lower()) if len(w) > 2]
        self._update_vocab(words)
        if not self._vocab:
            return [0.0]
        word_counts = {w: words.count(w) for w in words}
        return [float(word_counts.get(w, 0)) for w in self._vocab]
