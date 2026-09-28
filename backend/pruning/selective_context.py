"""
Selective Context Pruning — Li et al. (2023)

Computes token-level self-information using a causal language model
to identify and remove redundant (predictable) tokens from code context.

    Self-information:  I(x_i) = -log P(x_i | x_1, ..., x_{i-1})

    High I  →  surprising / informative  →  KEEP
    Low  I  →  predictable / redundant   →  PRUNE

Uses a local causal LM (default: Salesforce/codegen-350M-multi) via
HuggingFace transformers.  CodeGen is a code-aware model trained on
multi-language code, giving better self-information estimates for source
code than a general-purpose LM like GPT-2.

Two pruning modes:
  prune_text  — the paper's token-level pruning on raw BPE tokens (baseline).
  prune_code  — code-aware: rule-based comment filter, then pruning of whole
                lexical units (Li et al.'s lexical-unit aggregation) whose
                self-information is weighted by kind (code logic > syntax).
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional

from .code_units import (
    DEFAULT_CODE_WEIGHTS,
    PROTECTED_KINDS,
    CodeUnitWeights,
    filter_comments,
    lex_units,
)

# Lazy imports — torch / transformers are heavy; only load when actually used.
# This lets the heuristic fallback work even if torch is not installed.
_torch = None
_AutoModelForCausalLM = None
_AutoTokenizer = None


def _ensure_imports() -> None:
    global _torch, _AutoModelForCausalLM, _AutoTokenizer
    if _torch is not None:
        return
    try:
        import torch as _t
        from transformers import AutoModelForCausalLM as _M, AutoTokenizer as _T

        _torch = _t
        _AutoModelForCausalLM = _M
        _AutoTokenizer = _T
    except ImportError as e:
        raise ImportError(
            "Selective Context requires `torch` and `transformers`. "
            "Install with:  pip install torch transformers\n"
            "(Already available if sentence-transformers is installed.)"
        ) from e


# ── Data types ─────────────────────────────────────────────────


@dataclass
class TokenSelfInfo:
    """One token with its self-information value."""

    token: str
    token_id: int
    self_info: float  # nats; higher = more informative
    start: int = -1   # char offsets of the token in the input text
    end: int = -1


@dataclass
class PruneResult:
    """Output of a selective-context prune pass."""

    original_text: str
    pruned_text: str
    original_token_count: int
    kept_token_count: int
    compression_ratio: float  # kept / original
    mean_self_info: float
    threshold_used: float


# ── Core class ─────────────────────────────────────────────────


_INSTANCE_CACHE: dict[str, "SelectiveContextPruner"] = {}


@dataclass
class SelectiveContextPruner:
    """
    Compute token-level self-information with a causal LM and prune
    low-information tokens.

    Usage::

        pruner = SelectiveContextPruner.get_instance()   # singleton
        result = pruner.prune_text(code, target_ratio=0.5)
        print(result.pruned_text)
    """

    model_name: str = "Salesforce/codegen-350M-multi"
    max_seq_len: int = 2048  # CodeGen context window
    _model: object = field(default=None, repr=False)
    _tokenizer: object = field(default=None, repr=False)
    _device: str = field(default="", repr=False)
    # Self-information per text, so ranking and pruning share one forward pass.
    _cache: "OrderedDict[str, list[TokenSelfInfo]]" = field(default_factory=OrderedDict, repr=False)
    cache_size: int = 256

    def __post_init__(self) -> None:
        _ensure_imports()
        self._device = "cuda" if _torch.cuda.is_available() else "cpu"
        self._tokenizer = _AutoTokenizer.from_pretrained(self.model_name)
        self._model = _AutoModelForCausalLM.from_pretrained(self.model_name)
        self._model.to(self._device)
        self._model.eval()

    @classmethod
    def get_instance(cls, model_name: str = "Salesforce/codegen-350M-multi") -> "SelectiveContextPruner":
        """Return a cached singleton per model name (avoids reloading)."""
        if model_name not in _INSTANCE_CACHE:
            _INSTANCE_CACHE[model_name] = cls(model_name=model_name)
        return _INSTANCE_CACHE[model_name]

    # ── Self-information computation ───────────────────────────

    def compute_self_information(self, text: str) -> list[TokenSelfInfo]:
        """
        Compute self-information for every token in *text*.

        Returns a list of TokenSelfInfo ordered by position.
        The first token gets self_info = inf (no left context → always kept).
        """
        if not text.strip():
            return []
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        offsets = None
        if getattr(self._tokenizer, "is_fast", False):
            enc = self._tokenizer(text, return_offsets_mapping=True, return_tensors="pt")
            input_ids = enc["input_ids"]
            offsets = enc["offset_mapping"][0].tolist()
        else:
            input_ids = self._tokenizer.encode(text, return_tensors="pt")

        # Handle texts longer than the model's context window
        # Process in sliding windows with overlap
        if input_ids.shape[1] > self.max_seq_len:
            result = self._compute_windowed(input_ids)
        else:
            result = self._compute_single(input_ids)

        if offsets is None:
            # Slow tokenizer: approximate offsets from cumulative decoded length.
            pos = 0
            offsets = []
            for ti in result:
                offsets.append((pos, pos + len(ti.token)))
                pos += len(ti.token)
        for ti, (start, end) in zip(result, offsets):
            ti.start, ti.end = start, end

        self._cache[text] = result
        if len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return result

    def _compute_single(self, input_ids) -> list[TokenSelfInfo]:
        """Self-information for a sequence that fits in one context window."""
        input_ids = input_ids.to(self._device)

        with _torch.no_grad():
            outputs = self._model(input_ids)
            logits = outputs.logits  # (1, seq_len, vocab_size)

        # logits[:, i, :] predicts token at position i+1
        log_probs = _torch.log_softmax(logits[:, :-1, :], dim=-1)
        target_ids = input_ids[:, 1:]

        # Gather log-prob of the actual next token
        token_log_probs = (
            log_probs.gather(2, target_ids.unsqueeze(-1))
            .squeeze(-1)
            .squeeze(0)
        )

        # Self-information = -log P(x_i | x_{<i})
        self_info_values = (-token_log_probs).cpu().tolist()

        all_ids = input_ids.squeeze(0).cpu().tolist()

        result: list[TokenSelfInfo] = []

        # First token: no preceding context — always keep
        result.append(
            TokenSelfInfo(
                token=self._tokenizer.decode([all_ids[0]]),
                token_id=all_ids[0],
                self_info=float("inf"),
            )
        )

        for tid, si in zip(all_ids[1:], self_info_values):
            result.append(
                TokenSelfInfo(
                    token=self._tokenizer.decode([tid]),
                    token_id=tid,
                    self_info=si,
                )
            )

        return result

    def _compute_windowed(self, input_ids) -> list[TokenSelfInfo]:
        """
        Handle long sequences with a sliding window.

        Uses stride = max_seq_len // 2 so each token (except edges) appears
        in at least one window where it has enough left context.
        """
        all_ids = input_ids.squeeze(0).cpu().tolist()
        seq_len = len(all_ids)
        stride = self.max_seq_len // 2
        self_infos = [0.0] * seq_len
        counts = [0] * seq_len

        for start in range(0, seq_len, stride):
            end = min(start + self.max_seq_len, seq_len)
            window = input_ids[:, start:end].to(self._device)

            with _torch.no_grad():
                outputs = self._model(window)
                logits = outputs.logits

            log_probs = _torch.log_softmax(logits[:, :-1, :], dim=-1)
            target = window[:, 1:]
            tlp = (
                log_probs.gather(2, target.unsqueeze(-1))
                .squeeze(-1)
                .squeeze(0)
                .cpu()
                .tolist()
            )

            for i, si in enumerate(tlp):
                global_idx = start + i + 1  # +1 because logits[i] predicts token[i+1]
                self_infos[global_idx] += -si
                counts[global_idx] += 1

            if end >= seq_len:
                break

        result: list[TokenSelfInfo] = []
        result.append(
            TokenSelfInfo(
                token=self._tokenizer.decode([all_ids[0]]),
                token_id=all_ids[0],
                self_info=float("inf"),
            )
        )

        for i in range(1, seq_len):
            avg_si = self_infos[i] / counts[i] if counts[i] > 0 else 0.0
            result.append(
                TokenSelfInfo(
                    token=self._tokenizer.decode([all_ids[i]]),
                    token_id=all_ids[i],
                    self_info=avg_si,
                )
            )

        return result

    # ── Pruning ────────────────────────────────────────────────

    def prune_text(
        self,
        text: str,
        target_ratio: float = 0.5,
        threshold: Optional[float] = None,
    ) -> PruneResult:
        """
        Token-level Selective Context pruning.

        Args:
            text: source code / context string.
            target_ratio: fraction of tokens to KEEP (0.5 = keep 50%).
                          Ignored when *threshold* is set explicitly.
            threshold: explicit self-information cutoff.  Tokens with
                       I(x) < threshold are pruned.

        Returns:
            PruneResult with pruned text and compression stats.
        """
        token_infos = self.compute_self_information(text)

        if not token_infos:
            return PruneResult(
                original_text=text,
                pruned_text=text,
                original_token_count=0,
                kept_token_count=0,
                compression_ratio=1.0,
                mean_self_info=0.0,
                threshold_used=0.0,
            )

        # Determine threshold
        finite_vals = sorted(
            ti.self_info for ti in token_infos if math.isfinite(ti.self_info)
        )

        if not finite_vals:
            return PruneResult(
                original_text=text,
                pruned_text=text,
                original_token_count=len(token_infos),
                kept_token_count=len(token_infos),
                compression_ratio=1.0,
                mean_self_info=0.0,
                threshold_used=0.0,
            )

        if threshold is None:
            # Percentile-based: keep top target_ratio of tokens
            cutoff_idx = int(len(finite_vals) * (1 - target_ratio))
            cutoff_idx = max(0, min(cutoff_idx, len(finite_vals) - 1))
            threshold = finite_vals[cutoff_idx]

        kept_tokens = [ti.token for ti in token_infos if ti.self_info >= threshold]
        pruned_text = "".join(kept_tokens)

        mean_si = sum(finite_vals) / len(finite_vals)

        return PruneResult(
            original_text=text,
            pruned_text=pruned_text,
            original_token_count=len(token_infos),
            kept_token_count=len(kept_tokens),
            compression_ratio=len(kept_tokens) / len(token_infos) if token_infos else 1.0,
            mean_self_info=mean_si,
            threshold_used=threshold,
        )

    def prune_code(
        self,
        text: str,
        target_ratio: float = 0.7,
        language: str = "python",
        query: str = "",
        weights: Optional[CodeUnitWeights] = None,
    ) -> PruneResult:
        """
        Code-aware Selective Context pruning.

        1. filter_comments() drops comments/docstring lines with no semantic
           value (rules + query match; no model involved). Surviving comments
           are kept whole.
        2. The rest is split into lexical units. A unit's score is the summed
           self-information of its BPE tokens times a per-kind weight, so the
           model still decides which identifiers/calls/syntax are redundant
           while syntax is pruned before code logic.
        3. The highest-scoring units are kept until they use *target_ratio* of
           the prunable tokens (control flow, logic operators and definition
           names are always kept); identifiers are
           never split, and indentation/newlines are preserved.
        """
        w = weights or DEFAULT_CODE_WEIGHTS
        original_token_count = len(self._tokenizer.encode(text)) if text else 0
        filtered = self.prepare_code(text, language, query)
        if not filtered.strip():
            return PruneResult(text, "", original_token_count, 0, 0.0, 0.0, 0.0)

        token_infos = self.compute_self_information(filtered)
        units, keep, threshold = self.select_units(filtered, target_ratio, language, w)

        pruned_text = _rebuild(units, keep)
        all_finite = [ti.self_info for ti in token_infos if math.isfinite(ti.self_info)]
        kept_token_count = len(self._tokenizer.encode(pruned_text)) if pruned_text else 0
        return PruneResult(
            original_text=text,
            pruned_text=pruned_text,
            original_token_count=original_token_count,
            kept_token_count=kept_token_count,
            compression_ratio=kept_token_count / original_token_count if original_token_count else 1.0,
            mean_self_info=sum(all_finite) / len(all_finite) if all_finite else 0.0,
            threshold_used=threshold,
        )

    def select_units(
        self,
        filtered: str,
        target_ratio: float = 0.7,
        language: str = "python",
        weights: Optional[CodeUnitWeights] = None,
    ) -> tuple[list, set[int], float]:
        """Score lexical units of an already-filtered text and choose which to keep.

        Returns (units, indices of kept units, score threshold). Exposed separately
        so experiments (e.g. rules-only vs model ablations) can compare decisions.
        """
        w = weights or DEFAULT_CODE_WEIGHTS
        token_infos = self.compute_self_information(filtered)
        units = lex_units(filtered, language)

        # Distribute each token's self-information over the non-layout units it overlaps.
        unit_at = [-1] * len(filtered)
        for idx, u in enumerate(units):
            if u.kind not in ("whitespace", "newline"):
                for c in range(u.start, u.end):
                    unit_at[c] = idx
        unit_si = [0.0] * len(units)
        unit_cost = [0.0] * len(units)  # BPE tokens attributed to each unit
        for ti in token_infos:
            if ti.start < 0:
                continue
            chars = [unit_at[c] for c in range(ti.start, min(ti.end, len(filtered))) if unit_at[c] >= 0]
            if not chars:
                continue
            for idx in chars:
                unit_si[idx] += ti.self_info / len(chars)
                unit_cost[idx] += 1.0 / len(chars)

        always = set(w.keep_kinds)
        prunable = [
            i for i, u in enumerate(units) if u.kind not in PROTECTED_KINDS and u.kind not in always
        ]
        finite_unit_si = [unit_si[i] for i in prunable if math.isfinite(unit_si[i])]
        mean_unit_si = sum(finite_unit_si) / len(finite_unit_si) if finite_unit_si else 0.0
        scores = {
            i: (unit_si[i] + w.prior * mean_unit_si) * w.for_kind(units[i].kind)
            for i in prunable
        }

        # Keep the highest-scoring units until they use target_ratio of the
        # prunable tokens, so target_ratio means "fraction of tokens kept" as in
        # the paper (dropped units are often 1-char punctuation; a unit count
        # would barely shrink the text).
        budget = target_ratio * sum(unit_cost[i] for i in prunable)
        keep: set[int] = {i for i, u in enumerate(units) if u.kind in always}
        used = 0.0
        threshold = 0.0
        for i in sorted(prunable, key=lambda i: scores[i], reverse=True):
            if used + unit_cost[i] > budget and used > 0:
                continue  # skip; a cheaper lower-scoring unit may still fit
            keep.add(i)
            used += unit_cost[i]
            threshold = scores[i]
        return units, keep, threshold

    @staticmethod
    def prepare_code(text: str, language: str = "python", query: str = "") -> str:
        """The text prune_code() actually scores (after the comment filter).

        Use it for chunk_informativeness() too, so both share the cached forward pass.
        """
        return filter_comments(text, language, query)

    def chunk_informativeness(self, text: str) -> float:
        """
        Compute the mean self-information of a chunk.

        Useful as a ranking signal: chunks with higher mean self-info
        carry more information and should be prioritized.
        """
        token_infos = self.compute_self_information(text)
        finite = [ti.self_info for ti in token_infos if math.isfinite(ti.self_info)]
        return sum(finite) / len(finite) if finite else 0.0


def _rebuild(units: list, keep: set[int]) -> str:
    """Join kept units line by line: keep indentation, collapse gaps left by
    dropped units, and drop lines that pruning emptied."""
    lines: list[list[tuple[int, object]]] = [[]]
    for i, u in enumerate(units):
        if u.kind == "newline":
            lines.append([])
        else:
            lines[-1].append((i, u))

    out: list[str] = []
    for line in lines:
        if not line:
            out.append("")
            continue
        indent = ""
        rest = line
        if line[0][1].kind == "whitespace":
            indent, rest = line[0][1].text, line[1:]
        pieces: list[str] = []
        prev_space = True  # suppress whitespace right after the indent
        had_content = any(u.kind != "whitespace" for _, u in rest)
        for i, u in rest:
            if u.kind == "whitespace":
                if not prev_space:
                    pieces.append(" ")
                    prev_space = True
                continue
            if u.kind in PROTECTED_KINDS or i in keep:
                # Dropping e.g. "(" between two names must not fuse them into a new identifier.
                if pieces and not prev_space and _is_word_char(pieces[-1][-1]) and _is_word_char(u.text[0]):
                    pieces.append(" ")
                pieces.append(u.text)
                prev_space = False
        body = "".join(pieces).rstrip()
        if had_content and not body:
            continue  # line emptied by pruning
        out.append(indent + body)
    return "\n".join(out)


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"
