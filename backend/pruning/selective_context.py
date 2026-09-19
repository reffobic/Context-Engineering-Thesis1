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

transformers + torch are already available as transitive dependencies
of sentence-transformers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

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

        input_ids = self._tokenizer.encode(text, return_tensors="pt")

        # Handle texts longer than the model's context window
        # Process in sliding windows with overlap
        if input_ids.shape[1] > self.max_seq_len:
            return self._compute_windowed(input_ids)

        return self._compute_single(input_ids)

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

    def prune_lines(
        self,
        text: str,
        target_ratio: float = 0.5,
    ) -> PruneResult:
        """
        Line-level pruning: compute mean self-information per line,
        drop the least informative lines.

        Better for code than raw token-level pruning because it preserves
        syntactic structure within each kept line.
        """
        lines = text.split("\n")
        if len(lines) <= 1:
            return PruneResult(
                original_text=text,
                pruned_text=text,
                original_token_count=len(text) // 4,
                kept_token_count=len(text) // 4,
                compression_ratio=1.0,
                mean_self_info=0.0,
                threshold_used=0.0,
            )

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

        # Map tokens to lines by reconstructing text position
        line_self_infos: list[list[float]] = [[] for _ in lines]
        reconstructed = ""
        current_line = 0

        for ti in token_infos:
            if current_line < len(lines) and math.isfinite(ti.self_info):
                line_self_infos[current_line].append(ti.self_info)
            reconstructed += ti.token
            # Count newlines in token to advance line counter
            newline_count = ti.token.count("\n")
            current_line += newline_count

        # Compute mean self-info per line
        line_scores: list[tuple[int, float]] = []
        for i, infos in enumerate(line_self_infos):
            avg = sum(infos) / len(infos) if infos else 0.0
            line_scores.append((i, avg))

        # Find threshold
        score_vals = sorted(s for _, s in line_scores)
        cutoff_idx = int(len(score_vals) * (1 - target_ratio))
        cutoff_idx = max(0, min(cutoff_idx, len(score_vals) - 1))
        line_threshold = score_vals[cutoff_idx]

        kept_indices = {i for i, s in line_scores if s >= line_threshold}
        kept_lines = [line for i, line in enumerate(lines) if i in kept_indices]
        pruned_text = "\n".join(kept_lines)

        all_finite = [ti.self_info for ti in token_infos if math.isfinite(ti.self_info)]
        mean_si = sum(all_finite) / len(all_finite) if all_finite else 0.0

        return PruneResult(
            original_text=text,
            pruned_text=pruned_text,
            original_token_count=len(token_infos),
            kept_token_count=sum(1 for i, line in enumerate(lines) if i in kept_indices for _ in line),
            compression_ratio=len(kept_lines) / len(lines) if lines else 1.0,
            mean_self_info=mean_si,
            threshold_used=line_threshold,
        )

    def chunk_informativeness(self, text: str) -> float:
        """
        Compute the mean self-information of a chunk.

        Useful as a ranking signal: chunks with higher mean self-info
        carry more information and should be prioritized.
        """
        token_infos = self.compute_self_information(text)
        finite = [ti.self_info for ti in token_infos if math.isfinite(ti.self_info)]
        return sum(finite) / len(finite) if finite else 0.0
