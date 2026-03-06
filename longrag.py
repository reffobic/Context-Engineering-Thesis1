"""
LongRAG: Enhancing Retrieval-Augmented Generation with Long-context LLMs
Prototype implementation based on: Jiang et al., 2024 (arXiv:2406.15319)

Architecture:
  1. Long Retrieval Unit construction (grouping short docs into 4K-token units)
  2. Long Retriever (TF-IDF based similarity with MaxP approximation)
  3. Long Reader (extracts answer from concatenated long context)
"""

from __future__ import annotations

import re
import math
import heapq
import json
import collections
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Document:
    """A single raw document in the corpus."""
    doc_id: str
    title: str
    text: str
    links: list[str] = field(default_factory=list)   # outgoing hyperlinks (doc_ids)

    @property
    def token_count(self) -> int:
        return len(self.text.split())


@dataclass
class RetrievalUnit:
    """A (possibly grouped) long retrieval unit."""
    unit_id: str
    docs: list[Document]

    @property
    def text(self) -> str:
        parts = []
        for d in self.docs:
            parts.append(f"Title: {d.title}\n{d.text}")
        return "\n\n".join(parts)

    @property
    def token_count(self) -> int:
        return len(self.text.split())


# ---------------------------------------------------------------------------
# Step 1 – Group short documents into long retrieval units
# ---------------------------------------------------------------------------

def group_documents(
    docs: list[Document],
    max_tokens: int = 4000,
    short_doc_threshold: int = 1000,
) -> list[RetrievalUnit]:
    """
    Implements Algorithm 1 from the paper.

    - Documents already longer than `max_tokens` are kept as single units.
    - Short documents (< short_doc_threshold tokens) are grouped via hyperlink
      adjacency up to `max_tokens` total.

    Returns a list of RetrievalUnit objects.
    """
    long_docs = [d for d in docs if d.token_count >= short_doc_threshold]
    short_docs = [d for d in docs if d.token_count < short_doc_threshold]

    units: list[RetrievalUnit] = []

    # Long docs → individual units
    for d in long_docs:
        units.append(RetrievalUnit(unit_id=d.doc_id, docs=[d]))

    # Build adjacency map for short docs
    id_to_doc = {d.doc_id: d for d in short_docs}
    # Sort by degree (ascending) as in Algorithm 1
    short_docs_sorted = sorted(short_docs, key=lambda d: len(d.links))

    # Track which unit each doc has been assigned to
    doc_to_unit: dict[str, RetrievalUnit] = {}

    for doc in short_docs_sorted:
        if doc.doc_id in doc_to_unit:
            continue  # already grouped

        # Find related groups via hyperlinks
        related_units: list[RetrievalUnit] = []
        seen_unit_ids = set()
        for link_id in doc.links:
            if link_id in doc_to_unit:
                u = doc_to_unit[link_id]
                if u.unit_id not in seen_unit_ids:
                    related_units.append(u)
                    seen_unit_ids.add(u.unit_id)

        # Start a new group with this doc
        new_unit = RetrievalUnit(
            unit_id=f"group_{doc.doc_id}",
            docs=[doc]
        )

        # Merge related units if they fit within token budget
        related_units.sort(key=lambda u: u.token_count)
        for rel_unit in related_units:
            if new_unit.token_count + rel_unit.token_count <= max_tokens:
                new_unit.docs.extend(rel_unit.docs)
                # Re-point all docs in the merged unit
                for d in rel_unit.docs:
                    doc_to_unit[d.doc_id] = new_unit
                # Remove old unit from tracked list
                units = [u for u in units if u.unit_id != rel_unit.unit_id]

        doc_to_unit[doc.doc_id] = new_unit
        units.append(new_unit)

    return units


# ---------------------------------------------------------------------------
# Step 2 – Long Retriever (TF-IDF with MaxP approximation)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.lower())


def _build_idf(units: list[RetrievalUnit]) -> dict[str, float]:
    """Compute IDF over all retrieval units (treating each unit as a document)."""
    N = len(units)
    df: dict[str, int] = collections.defaultdict(int)
    for unit in units:
        tokens = set(_tokenize(unit.text))
        for t in tokens:
            df[t] += 1
    idf = {t: math.log((N + 1) / (cnt + 1)) + 1 for t, cnt in df.items()}
    return idf


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """Compute TF-IDF vector (sparse, as a dict)."""
    tf: dict[str, int] = collections.Counter(tokens)
    vec: dict[str, float] = {}
    for t, freq in tf.items():
        if t in idf:
            vec[t] = (1 + math.log(freq)) * idf[t]
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {t: v / norm for t, v in vec.items()}


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    """Dot product between two sparse vectors."""
    # Iterate over the shorter one
    if len(a) > len(b):
        a, b = b, a
    return sum(a[t] * b.get(t, 0.0) for t in a)


class LongRetriever:
    """
    TF-IDF retriever with MaxP approximation (Section 2.1 of the paper).

    For each long retrieval unit g, similarity is approximated as:
        sim(q, g) ≈ max_{g' ⊆ g} sim(q, g')
    where g' are fixed-size chunks of g.
    """

    def __init__(
        self,
        units: list[RetrievalUnit],
        chunk_size: int = 100,   # tokens per sub-chunk (proxy for 512 BPE tokens)
    ):
        self.units = units
        self.chunk_size = chunk_size
        self.idf = _build_idf(units)
        # Pre-compute chunk vectors for every unit
        self._chunk_vecs: dict[str, list[dict[str, float]]] = {}
        for unit in units:
            self._chunk_vecs[unit.unit_id] = self._encode_unit(unit)

    def _encode_unit(self, unit: RetrievalUnit) -> list[dict[str, float]]:
        """Chunk a unit into sub-chunks and compute TF-IDF for each."""
        tokens = _tokenize(unit.text)
        vecs = []
        for i in range(0, max(1, len(tokens)), self.chunk_size):
            chunk_tokens = tokens[i: i + self.chunk_size]
            vecs.append(_tfidf_vector(chunk_tokens, self.idf))
        return vecs

    def retrieve(self, query: str, top_k: int = 4) -> list[tuple[RetrievalUnit, float]]:
        """
        Retrieve top-k units using MaxP approximation.
        Returns list of (unit, score) sorted by descending score.
        """
        q_tokens = _tokenize(query)
        q_vec = _tfidf_vector(q_tokens, self.idf)

        scored: list[tuple[float, str]] = []
        for unit in self.units:
            chunk_vecs = self._chunk_vecs[unit.unit_id]
            # MaxP: take the best chunk score
            max_sim = max(_dot(q_vec, cv) for cv in chunk_vecs)
            scored.append((max_sim, unit.unit_id))

        top = heapq.nlargest(top_k, scored, key=lambda x: x[0])
        unit_map = {u.unit_id: u for u in self.units}
        return [(unit_map[uid], score) for score, uid in top]


# ---------------------------------------------------------------------------
# Step 3a – Heuristic Long Reader (no API, no cost)
# ---------------------------------------------------------------------------

class HeuristicReader:
    """
    Lightweight extractive reader — no LLM, no API key needed.
    Uses sentence scoring + regex to extract answers.
    """

    def read(self, query: str, context: str, verbose: bool = False) -> dict:
        cleaned = re.sub(r"Title:\s*[^\n]+\n?", "", context)
        sentences = re.split(r"(?<=[.!?])\s+", cleaned)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

        if not sentences:
            return {"long_answer": "", "short_answer": "", "reader": "heuristic"}

        q_tokens = set(_tokenize(query))
        query_lower = query.lower()

        measurement_query = any(w in query_lower for w in (
            "height", "weight", "tall", "length", "distance", "speed",
            "temperature", "size", "age", "how many", "how much", "how long",
            "how far", "how old", "how wide",
        ))
        who_query = any(w in query_lower for w in (
            "who", "founder", "creator", "author", "created", "wrote",
            "invented", "designed", "winner", "won",
        ))

        def sentence_score(s: str) -> float:
            s_tokens = _tokenize(s)
            overlap = len(q_tokens & set(s_tokens))
            length_norm = math.sqrt(len(s_tokens)) + 1
            base = overlap / length_norm

            has_named_entity = bool(re.search(r"[A-Z][a-z]+ [A-Z][a-z]+", s))
            has_decimal = bool(re.search(r"\d+\.\d+", s))
            has_measurement = bool(re.search(r"\d+\s*(?:m|kg|km|lb|ft)\b", s))
            has_creator_pattern = bool(re.search(
                r"[A-Z][a-z]+ (?:van |de )?[A-Z][a-z]+.{0,60}"
                r"(?:creator|founder|author|designer|inventor|created|founded|developed|invented|made|built)",
                s, re.IGNORECASE
            ))
            has_measurement_pattern = bool(re.search(
                r"(?:stands?|is|height|tall|measures?|weighs?).{0,30}\d+[\.\d]*\s*(?:m|cm|kg|lb|ft)\b",
                s, re.IGNORECASE
            ))

            if measurement_query and (has_measurement_pattern or has_decimal):
                return base * 10.0
            if who_query:
                if has_creator_pattern:
                    base *= 3.0
                elif has_named_entity:
                    base *= 1.5
            if measurement_query and has_measurement:
                base *= 2.0
            return base

        best_sent = max(sentences, key=sentence_score)
        if verbose:
            print(f"[Heuristic Reader] Best sentence: {best_sent[:120]}...")

        short_answer = self._extract_short_answer(query, best_sent)
        return {"long_answer": best_sent, "short_answer": short_answer, "reader": "heuristic"}

    def _extract_short_answer(self, query: str, sentence: str) -> str:
        query_lower = query.lower()
        query_words = set(_tokenize(query))

        measurement_query = any(w in query_lower for w in (
            "height", "weight", "tall", "length", "distance", "speed",
            "temperature", "size", "age", "how many", "how much", "how long",
            "how far", "how old", "how wide",
        ))
        who_query = any(w in query_lower for w in (
            "who", "founder", "creator", "author", "created", "wrote",
            "invented", "designed", "winner", "won",
        ))

        if measurement_query:
            measurements = re.findall(
                r"\b\d+\.\d+(?:\s*(?:m|km|kg|lb|ft|cm|mph|kph))?\b", sentence)
            if measurements:
                return measurements[0].strip()
            unit_numbers = re.findall(
                r"\b\d+\s*(?:m|km|kg|lb|ft|cm|mph|kph|years?|months?|days?)\b", sentence)
            if unit_numbers:
                return unit_numbers[0].strip()

        if who_query:
            phrases = re.findall(r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", sentence)
            for phrase in phrases:
                if not any(w in query_words for w in _tokenize(phrase)):
                    return phrase

        phrases = re.findall(r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", sentence)
        for phrase in phrases:
            if not any(w in query_words for w in _tokenize(phrase)):
                return phrase

        numbers = re.findall(
            r"\b\d[\d,./]*(?:\s+(?:m|km|kg|years?|months?|days?|million|billion))?\b", sentence)
        if numbers:
            return numbers[0].strip()

        return " ".join(sentence.split()[:5])


# ---------------------------------------------------------------------------
# Step 3b – Claude API Long Reader (requires ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

class ClaudeReader:
    """
    Real LLM reader using the Claude API (as tested in Figure 4 of the paper).

    Requires:  pip install anthropic
               export ANTHROPIC_API_KEY=sk-ant-...

    Implements the two-turn prompt from Table 7 of the paper:
      Turn 1 – Claude reads the long context and gives a detailed answer.
      Turn 2 – Claude extracts a concise short answer from that long answer.
    """

    # Prompts taken directly from Table 7 of the paper
    TURN1_SYSTEM = (
        "You are a helpful assistant. Go through the provided context documents "
        "and answer the question as accurately as possible. "
        "Your response should be concise but complete."
    )

    TURN2_SYSTEM = (
        "You are a helpful assistant. Your task is to extract a very short, "
        "concise answer from a longer answer. Output only the short answer, "
        "nothing else — no explanation, no punctuation beyond what is needed."
    )

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        """
        model: which Claude model to use as the reader.
               Defaults to Haiku (cheapest) to preserve free credits.
               Use 'claude-sonnet-4-6' for higher quality.
        """
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "The 'anthropic' package is not installed.\n"
                "Run:  pip install anthropic"
            )

        import os

        # Try config.py first (easiest), then environment variable
        api_key = None
        try:
            from config import ANTHROPIC_API_KEY as _key
            if _key and "PASTE-YOUR-KEY-HERE" not in _key:
                api_key = _key
        except ImportError:
            pass

        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY")

        if not api_key:
            raise EnvironmentError(
                "No API key found.\n"
                "Open config.py and paste your key where it says PASTE-YOUR-KEY-HERE"
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def read(self, query: str, context: str, verbose: bool = False) -> dict:
        """
        Two-turn Claude reading (mirrors Table 7 of the paper).
        Turn 1: read long context → detailed answer.
        Turn 2: compress detailed answer → short answer.
        """
        # ── Turn 1: read context ──────────────────────────────────────────
        turn1_user = (
            f"Here are the retrieved documents (ordered by relevance):\n\n"
            f"{context}\n\n"
            f"Question: {query}\n\n"
            f"Find the relevant information in the documents above and answer "
            f"the question. Be concise but thorough."
        )

        if verbose:
            print(f"[Claude Reader] Sending Turn 1 ({len(turn1_user.split())} words to API)...")

        turn1_response = self.client.messages.create(
            model=self.model,
            max_tokens=300,
            system=self.TURN1_SYSTEM,
            messages=[{"role": "user", "content": turn1_user}],
        )
        long_answer = turn1_response.content[0].text.strip()

        if verbose:
            print(f"[Claude Reader] Long answer: {long_answer}")

        # ── Turn 2: compress to short answer ─────────────────────────────
        turn2_user = (
            f"Question: {query}\n"
            f"Long answer: {long_answer}\n\n"
            f"Extract the shortest possible answer from the long answer above. "
            f"Output only the answer itself — a word, name, number, or short phrase."
        )

        turn2_response = self.client.messages.create(
            model=self.model,
            max_tokens=50,
            system=self.TURN2_SYSTEM,
            messages=[{"role": "user", "content": turn2_user}],
        )
        short_answer = turn2_response.content[0].text.strip()

        if verbose:
            print(f"[Claude Reader] Short answer: {short_answer}")

        return {
            "long_answer": long_answer,
            "short_answer": short_answer,
            "reader": f"claude ({self.model})",
        }


# ---------------------------------------------------------------------------
# LongRAG pipeline
# ---------------------------------------------------------------------------

class LongRAG:
    """
    End-to-end LongRAG pipeline.

    Two modes:
      use_llm=False  →  free, no API key, heuristic reader (default)
      use_llm=True   →  uses Claude API, requires ANTHROPIC_API_KEY

    Usage:
        # Free mode (no API key needed)
        system = LongRAG(corpus)
        result = system.answer("Who created Python?")

        # LLM mode (requires API key)
        system = LongRAG(corpus, use_llm=True)
        result = system.answer("Who created Python?")
    """

    def __init__(
        self,
        docs: list[Document],
        max_tokens_per_unit: int = 4000,
        short_doc_threshold: int = 500,
        chunk_size: int = 100,
        use_llm: bool = False,
        claude_model: str = "claude-haiku-4-5-20251001",
    ):
        print(f"[LongRAG] Building retrieval units from {len(docs)} documents...")
        self.units = group_documents(docs, max_tokens_per_unit, short_doc_threshold)
        print(f"[LongRAG] Corpus compressed: {len(docs)} docs → {len(self.units)} units")

        self.retriever = LongRetriever(self.units, chunk_size=chunk_size)

        if use_llm:
            self.reader = ClaudeReader(model=claude_model)
            print(f"[LongRAG] Reader: Claude API ({claude_model})")
        else:
            self.reader = HeuristicReader()
            print(f"[LongRAG] Reader: Heuristic (free, no API key)")

    def answer(
        self,
        query: str,
        top_k: int = 4,
        verbose: bool = False,
    ) -> dict:
        # --- Retrieval ---
        retrieved = self.retriever.retrieve(query, top_k=top_k)

        # --- Aggregate context ---
        context = "\n\n".join(unit.text for unit, _ in retrieved)

        if verbose:
            print(f"\n[LongRAG] Query: {query}")
            print(f"[LongRAG] Retrieved {len(retrieved)} units "
                  f"({sum(u.token_count for u, _ in retrieved)} tokens total)")
            for unit, score in retrieved:
                print(f"  • {unit.unit_id:30s} score={score:.4f}  tokens={unit.token_count}")

        # --- Reading ---
        result = self.reader.read(query, context, verbose=verbose)
        result["retrieved_units"] = [(u.unit_id, round(s, 4)) for u, s in retrieved]
        result["context_tokens"] = sum(u.token_count for u, _ in retrieved)
        result["query"] = query
        return result
