"""Trend detection.

A trend is a **comparison**, not a topic. The engine takes a signal's observation window and
compares it against the signal's own prior baseline window along three axes:

* level      — is the signal running above its baseline?
* frequency  — are events arriving more often?
* breadth    — are more *independent* sources reporting it?

A large, stable, widely-covered phenomenon produces a high level and an acceleration near
zero. That is the difference between "many articles mention AI memory" and "AI-related
memory demand evidence is running above its own baseline", and it is arithmetic rather than
an opinion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, Direction
from marketradar.domain.models import Signal, SignalObservation, Trend
from marketradar.logging import get_logger
from marketradar.signals.definitions import SignalDefinition
from marketradar.signals.engine import SignalEngine

log = get_logger(__name__)

COMPUTATION_VERSION = "trend_v1"

#: Floor for the baseline denominator. Without it, a baseline of ~0 makes any observation
#: look infinitely accelerated — the classic way trend detectors manufacture excitement out
#: of a quiet history.
BASELINE_FLOOR = 8.0

#: Scales the ratio before tanh. 1.0 means "double the baseline" maps to ~76 acceleration.
ACCELERATION_SCALE = 1.0


@dataclass
class TrendResult:
    """A computed trend with every input retained for explanation."""

    signal_key: str
    observation_start: datetime
    observation_end: datetime
    baseline_start: datetime
    baseline_end: datetime
    observation_strength: float
    baseline_strength: float
    acceleration: float
    frequency_change: float
    independence_change: float
    novelty: float
    direction: Direction
    data_mode: DataMode
    observation_buckets: int = 0
    baseline_buckets: int = 0
    inputs: dict = field(default_factory=dict)


def bounded_change(observed: float, baseline: float, floor: float = BASELINE_FLOOR) -> float:
    """Relative change mapped to -100..100 through tanh.

    tanh keeps a small baseline from producing an unbounded score while staying monotonic:
    more change always reads as more acceleration, but never as infinite acceleration.
    """
    denominator = max(baseline, floor)
    ratio = (observed - baseline) / denominator
    return round(100.0 * math.tanh(ACCELERATION_SCALE * ratio), 4)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class TrendEngine:
    """Computes and persists trends from stored signal observations."""

    def __init__(
        self,
        session: Session,
        signal_engine: SignalEngine,
        observation_window_days: int = 30,
        baseline_window_days: int = 90,
    ) -> None:
        self.session = session
        self.signal_engine = signal_engine
        self.observation_window_days = observation_window_days
        self.baseline_window_days = baseline_window_days

    def compute(
        self, signal: Signal, definition: SignalDefinition, as_of: datetime
    ) -> TrendResult:
        """Compute a trend for one signal.

        Two different quantities are needed and they are computed differently on purpose:

        * **Level** (``observation_strength`` / ``baseline_strength``) aggregates the *whole*
          window at once, so it answers "how strong is the evidence that this is happening".
          Averaging weekly buckets would understate a window with quiet weeks, which is a
          property of reporting cadence rather than of the phenomenon.
        * **Acceleration** compares the *mean weekly bucket* of the two windows, because the
          windows have different lengths and only a per-bucket rate is comparable between
          them.

        Both sets of numbers are stored, so the comparison can be audited.
        """
        observation_start = as_of - timedelta(days=self.observation_window_days)
        baseline_end = observation_start
        baseline_start = baseline_end - timedelta(days=self.baseline_window_days)

        observations = self.session.scalars(
            select(SignalObservation)
            .where(
                SignalObservation.signal_id == signal.id,
                SignalObservation.computation_version.isnot(None),
                SignalObservation.bucket_start >= baseline_start,
            )
            .order_by(SignalObservation.bucket_start)
        ).all()

        window = [o for o in observations if o.bucket_start >= observation_start]
        baseline = [
            o for o in observations if baseline_start <= o.bucket_start < baseline_end
        ]

        # Window-level aggregation: every independent cluster in the window counted once.
        observation_window = self.signal_engine.compute_bucket(
            definition, observation_start, as_of
        )
        baseline_window = self.signal_engine.compute_bucket(
            definition, baseline_start, baseline_end
        )
        observation_strength = observation_window.strength
        baseline_strength = baseline_window.strength

        # Bucket means drive the acceleration comparison (see the docstring).
        observation_bucket_mean = _mean([o.strength for o in window])
        baseline_bucket_mean = _mean([o.strength for o in baseline])

        # Rates per day, so windows of different lengths are comparable.
        observation_rate = sum(o.event_count for o in window) / max(
            self.observation_window_days, 1
        )
        baseline_rate = sum(o.event_count for o in baseline) / max(
            self.baseline_window_days, 1
        )
        independent_observation = sum(o.independent_source_count for o in window) / max(
            self.observation_window_days, 1
        )
        independent_baseline = sum(o.independent_source_count for o in baseline) / max(
            self.baseline_window_days, 1
        )

        acceleration = bounded_change(observation_bucket_mean, baseline_bucket_mean)
        frequency_change = bounded_change(observation_rate, baseline_rate, floor=0.05)
        independence_change = bounded_change(
            independent_observation, independent_baseline, floor=0.05
        )

        positive = sum(1 for o in window if o.net_direction == Direction.POSITIVE)
        negative = sum(1 for o in window if o.net_direction == Direction.NEGATIVE)
        direction = (
            Direction.POSITIVE
            if positive > negative
            else Direction.NEGATIVE
            if negative > positive
            else Direction.NEUTRAL
        )

        modes: list[DataMode] = [
            o.data_mode for o in window if o.data_mode != DataMode.UNAVAILABLE
        ]
        result = TrendResult(
            signal_key=signal.key,
            observation_start=observation_start,
            observation_end=as_of,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            observation_strength=round(observation_strength, 4),
            baseline_strength=round(baseline_strength, 4),
            acceleration=acceleration,
            frequency_change=frequency_change,
            independence_change=independence_change,
            novelty=0.0,  # filled in by the theme layer, which sees all of a theme's evidence
            direction=direction,
            data_mode=DataMode.weakest(modes) if modes else DataMode.UNAVAILABLE,
            observation_buckets=len(window),
            baseline_buckets=len(baseline),
            inputs={
                "observation_bucket_mean": round(observation_bucket_mean, 4),
                "baseline_bucket_mean": round(baseline_bucket_mean, 4),
                "observation_net_contribution": observation_window.net_contribution,
                "baseline_net_contribution": baseline_window.net_contribution,
                "observation_independent_sources": (
                    observation_window.independence.independent_source_count
                ),
                "observation_rate_per_day": round(observation_rate, 4),
                "baseline_rate_per_day": round(baseline_rate, 4),
                "independent_per_day_observation": round(independent_observation, 4),
                "independent_per_day_baseline": round(independent_baseline, 4),
                "baseline_floor": BASELINE_FLOOR,
                "observation_window_days": self.observation_window_days,
                "baseline_window_days": self.baseline_window_days,
            },
        )
        return result

    def persist(self, signal: Signal, result: TrendResult, theme_id: str | None = None) -> Trend:
        existing = self.session.scalar(
            select(Trend).where(
                Trend.signal_id == signal.id,
                Trend.observation_end == result.observation_end,
                Trend.computation_version == COMPUTATION_VERSION,
            )
        )
        row = existing or Trend(
            signal_id=signal.id,
            observation_end=result.observation_end,
            computation_version=COMPUTATION_VERSION,
            observation_start=result.observation_start,
            baseline_start=result.baseline_start,
            baseline_end=result.baseline_end,
            observation_strength=0.0,
            baseline_strength=0.0,
            acceleration=0.0,
            direction=Direction.NEUTRAL,
            data_mode=DataMode.UNAVAILABLE,
            computed_at=result.observation_end,
        )
        row.theme_id = theme_id or row.theme_id
        row.observation_start = result.observation_start
        row.baseline_start = result.baseline_start
        row.baseline_end = result.baseline_end
        row.observation_strength = result.observation_strength
        row.baseline_strength = result.baseline_strength
        row.acceleration = result.acceleration
        row.frequency_change = result.frequency_change
        row.independence_change = result.independence_change
        row.novelty = result.novelty
        row.direction = result.direction
        row.data_mode = result.data_mode
        row.computed_at = datetime.now(tz=result.observation_end.tzinfo)
        row.inputs = result.inputs
        if existing is None:
            self.session.add(row)
        self.session.flush()
        return row
