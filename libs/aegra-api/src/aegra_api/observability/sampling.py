"""Trace-id seeding policy vs OTEL ratio samplers.

``TraceIdRatioBased`` reads the low 64 bits of ``trace_id``. UUID v4 values
(``UUID(run_id).int``) pin those bits in ``[0.5, 0.75)``, so rate ``0.1`` /
``0.5`` keep 0% of runs and ``0.75+`` keep 100%. When a ratio sampler is
configured we disable run_id seeding and use random trace ids instead.
"""

from __future__ import annotations

import logging
import os

from opentelemetry.sdk.trace.sampling import OTEL_TRACES_SAMPLER

logger = logging.getLogger(__name__)

_RATIO_SAMPLERS = frozenset({"traceidratio", "parentbased_traceidratio"})

# None = not configured yet; resolved on first use or via configure_*.
_seeding_enabled: bool | None = None


def is_ratio_sampler_configured() -> bool:
    """True when ``OTEL_TRACES_SAMPLER`` is a trace-id ratio sampler."""
    name = os.getenv(OTEL_TRACES_SAMPLER, "parentbased_always_on").lower()
    return name in _RATIO_SAMPLERS


def configure_trace_id_from_run_id(*, enabled_by_settings: bool) -> bool:
    """Decide whether to seed ``trace_id`` from ``run_id``; warn once if ratio wins."""
    global _seeding_enabled
    if not enabled_by_settings:
        _seeding_enabled = False
        return False
    if is_ratio_sampler_configured():
        _seeding_enabled = False
        logger.warning(
            "OTEL_TRACE_ID_FROM_RUN_ID disabled: ratio sampler "
            "(traceidratio / parentbased_traceidratio) is incompatible with "
            "UUID(run_id)-seeded trace ids (biased low-64 bits). Using random "
            "trace ids so sampling rates apply correctly."
        )
        return False
    _seeding_enabled = True
    return True


def is_trace_id_from_run_id_enabled() -> bool:
    """Whether ``generate_trace_id`` should consume a run_id seed."""
    global _seeding_enabled
    if _seeding_enabled is None:
        from aegra_api.settings import settings

        configure_trace_id_from_run_id(enabled_by_settings=settings.observability.OTEL_TRACE_ID_FROM_RUN_ID)
    return bool(_seeding_enabled)


def reset_trace_id_seeding_for_tests() -> None:
    """Clear cached seeding policy (tests only)."""
    global _seeding_enabled
    _seeding_enabled = None
