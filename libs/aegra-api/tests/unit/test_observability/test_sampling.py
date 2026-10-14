"""Regression tests for ratio sampling with run_id-seeded UUID trace IDs."""

from __future__ import annotations

import uuid

import pytest
from opentelemetry.sdk.trace.sampling import Decision, TraceIdRatioBased

from aegra_api.observability.sampling import (
    ParentBasedUniformTraceIdRatio,
    UniformTraceIdRatioBased,
    get_run_aware_sampler,
    uniform_ratio_sampling_bits,
)
from aegra_api.observability.span_enrichment import RunIdAwareIdGenerator, _run_trace_id, seed_otel_trace_id


class TestUuidTraceIdRatioBias:
    """Stock TraceIdRatioBased is unusable with UUID(run_id).int trace ids."""

    def test_stock_ratio_01_drops_every_uuid4_trace_id(self) -> None:
        sampler = TraceIdRatioBased(0.1)
        for _ in range(200):
            result = sampler.should_sample(None, uuid.uuid4().int, "span")
            assert result.decision is Decision.DROP

    def test_stock_ratio_075_samples_every_uuid4_trace_id(self) -> None:
        sampler = TraceIdRatioBased(0.75)
        for _ in range(200):
            result = sampler.should_sample(None, uuid.uuid4().int, "span")
            assert result.decision is Decision.RECORD_AND_SAMPLE


class TestUniformRatioSamplingBits:
    def test_bits_are_within_64_bit_range(self) -> None:
        bits = uniform_ratio_sampling_bits(uuid.uuid4().int)
        assert 0 <= bits <= (1 << 64) - 1

    def test_deterministic_for_same_trace_id(self) -> None:
        tid = uuid.uuid4().int
        assert uniform_ratio_sampling_bits(tid) == uniform_ratio_sampling_bits(tid)

    def test_uuid_low_bits_escape_variant_band(self) -> None:
        """Hashed lows must not stay trapped in the UUID variant [0.5, 0.75) band."""
        below_half = 0
        above_three_quarters = 0
        for _ in range(500):
            fraction = uniform_ratio_sampling_bits(uuid.uuid4().int) / (1 << 64)
            if fraction < 0.5:
                below_half += 1
            if fraction >= 0.75:
                above_three_quarters += 1
        assert below_half > 0
        assert above_three_quarters > 0


class TestUniformTraceIdRatioBased:
    def test_rate_01_does_not_drop_every_uuid_trace(self) -> None:
        sampler = UniformTraceIdRatioBased(0.1)
        sampled = 0
        n = 2000
        for _ in range(n):
            if sampler.should_sample(None, uuid.uuid4().int, "span").decision is Decision.RECORD_AND_SAMPLE:
                sampled += 1
        # Expect ~10%; allow wide band so the test is stable.
        assert 50 < sampled < 400
        assert sampled / n == pytest.approx(0.1, abs=0.05)

    def test_rate_075_does_not_sample_every_uuid_trace(self) -> None:
        sampler = UniformTraceIdRatioBased(0.75)
        sampled = 0
        n = 2000
        for _ in range(n):
            if sampler.should_sample(None, uuid.uuid4().int, "span").decision is Decision.RECORD_AND_SAMPLE:
                sampled += 1
        assert 1200 < sampled < 1800
        assert sampled / n == pytest.approx(0.75, abs=0.05)

    def test_keeps_seeded_run_id_trace_identity(self) -> None:
        """Sampling must not change the IdGenerator contract used for Langfuse linking."""
        run_id = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
        seed_otel_trace_id(run_id)
        try:
            trace_id = RunIdAwareIdGenerator().generate_trace_id()
        finally:
            _run_trace_id.set(None)

        assert trace_id == uuid.UUID(run_id).int
        # Decision is based on hashed bits, but the id itself stays the UUID int.
        sampler = UniformTraceIdRatioBased(0.1)
        sampler.should_sample(None, trace_id, "span")
        assert trace_id == uuid.UUID(run_id).int

    def test_decision_is_deterministic_for_trace_id(self) -> None:
        sampler = UniformTraceIdRatioBased(0.3)
        tid = uuid.uuid4().int
        first = sampler.should_sample(None, tid, "span").decision
        second = sampler.should_sample(None, tid, "span").decision
        assert first is second

    def test_rejects_invalid_rate(self) -> None:
        with pytest.raises(ValueError, match="Probability"):
            UniformTraceIdRatioBased(1.5)


class TestGetRunAwareSampler:
    def test_parentbased_traceidratio_uses_uniform_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_TRACES_SAMPLER", "parentbased_traceidratio")
        monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.1")
        sampler = get_run_aware_sampler()
        assert isinstance(sampler, ParentBasedUniformTraceIdRatio)

    def test_traceidratio_uses_uniform_sampler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_TRACES_SAMPLER", "traceidratio")
        monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.25")
        sampler = get_run_aware_sampler()
        assert isinstance(sampler, UniformTraceIdRatioBased)
        assert sampler.rate == 0.25

    def test_default_is_parentbased_always_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
        monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)
        sampler = get_run_aware_sampler()
        assert sampler.get_description().startswith("ParentBased")
