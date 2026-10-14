"""Samplers that keep ratio sampling honest for run_id-seeded trace IDs.

``TraceIdRatioBased`` decides from the low 64 bits of ``trace_id``. UUID v4
values (``UUID(run_id).int``) fix the variant bits so those lows always fall
in ``[0.5, 0.75)`` of the range — rate ``0.1`` drops every run; rate ``0.75``
samples every run.

We keep the deterministic ``trace_id`` for Langfuse linking and hash it to
uniform bits before applying the ratio bound.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Sequence

from opentelemetry.context import Context
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    OTEL_TRACES_SAMPLER,
    OTEL_TRACES_SAMPLER_ARG,
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind, TraceState, get_current_span
from opentelemetry.util.types import Attributes

logger = logging.getLogger(__name__)

_TRACE_ID_LIMIT = (1 << 64) - 1


def uniform_ratio_sampling_bits(trace_id: int) -> int:
    """Map a 128-bit trace_id to low-64 bits that are uniform for ratio sampling."""
    digest = hashlib.sha256(trace_id.to_bytes(16, "big")).digest()
    return int.from_bytes(digest[:8], "big") & _TRACE_ID_LIMIT


def _parent_trace_state(parent_context: Context | None) -> TraceState | None:
    parent_span_context = get_current_span(parent_context).get_span_context()
    if parent_span_context is None or not parent_span_context.is_valid:
        return None
    return parent_span_context.trace_state


class UniformTraceIdRatioBased(Sampler):
    """``TraceIdRatioBased`` decision using hashed bits, not raw UUID lows."""

    def __init__(self, rate: float) -> None:
        if rate < 0.0 or rate > 1.0:
            raise ValueError("Probability must be in range [0.0, 1.0].")
        self._rate = rate
        self._bound = TraceIdRatioBased.get_bound_for_rate(rate)

    @property
    def rate(self) -> float:
        return self._rate

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        decision = Decision.DROP
        if uniform_ratio_sampling_bits(trace_id) < self._bound:
            decision = Decision.RECORD_AND_SAMPLE
        if decision is Decision.DROP:
            attributes = None
        return SamplingResult(
            decision,
            attributes,
            _parent_trace_state(parent_context),
        )

    def get_description(self) -> str:
        return f"UniformTraceIdRatioBased{{{self._rate}}}"


class ParentBasedUniformTraceIdRatio(ParentBased):
    """ParentBased root that uses :class:`UniformTraceIdRatioBased`."""

    def __init__(self, rate: float) -> None:
        super().__init__(root=UniformTraceIdRatioBased(rate=rate))


def get_run_aware_sampler() -> Sampler:
    """Resolve ``OTEL_TRACES_SAMPLER`` with uniform-bit ratio sampling for seeded IDs."""
    trace_sampler = os.getenv(OTEL_TRACES_SAMPLER, "parentbased_always_on").lower()

    if trace_sampler == "always_on":
        return ALWAYS_ON
    if trace_sampler == "always_off":
        return ALWAYS_OFF
    if trace_sampler == "parentbased_always_on":
        return ParentBased(ALWAYS_ON)
    if trace_sampler == "parentbased_always_off":
        return ParentBased(ALWAYS_OFF)

    if trace_sampler in ("traceidratio", "parentbased_traceidratio"):
        try:
            rate = float(os.getenv(OTEL_TRACES_SAMPLER_ARG, ""))
        except (ValueError, TypeError):
            logger.warning("Could not convert TRACES_SAMPLER_ARG to float.")
            rate = 1.0
        if trace_sampler == "traceidratio":
            return UniformTraceIdRatioBased(rate)
        return ParentBasedUniformTraceIdRatio(rate)

    logger.warning("Couldn't recognize sampler %s.", trace_sampler)
    return ParentBased(ALWAYS_ON)
