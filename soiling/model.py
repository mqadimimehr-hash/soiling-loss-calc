"""Daily soiling-loss model.

The model is deliberately simple and transparent:

* The soiling ratio (SR, 1.0 = clean) starts at 1.0 and falls linearly by
  ``rate_pct_per_day`` percentage points per day (scaled down by a coating's
  ``rate_reduction``), never dropping below ``min_ratio``.
* Scheduled cleaning every ``clean_every`` days resets SR to 1.0.
* Rain (optional) on a day with rainfall >= ``rain_threshold_mm`` recovers a
  fraction ``rain_recovery`` of the current soiling loss (1.0 = full reset).
* Daily energy = yield (kWh/kWp/day) * size (kWp) * (1 - transmittance
  penalty) * SR.

Losses are reported against a clean, uncoated reference system.

All default values in this module are EXAMPLE ASSUMPTIONS for demonstration
only. They are not measured data for any location. Use your own site's
measured soiling rate, yield, tariff and costs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np
import pandas as pd

ArrayLike = Union[float, Sequence[float], np.ndarray, pd.Series]


@dataclass(frozen=True)
class Coating:
    """Anti-soiling / self-cleaning coating (example assumptions only).

    rate_reduction: fraction by which the soiling rate is reduced (0..1).
        0.5 means dust accumulates at half the uncoated rate.
    transmittance_penalty: fractional permanent loss of light transmission
        (0..1), applied to every day's energy.
    cost_per_year: annualised coating cost (currency units per year).
    """

    rate_reduction: float = 0.0
    transmittance_penalty: float = 0.0
    cost_per_year: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.rate_reduction <= 1.0:
            raise ValueError("rate_reduction must be in [0, 1]")
        if not 0.0 <= self.transmittance_penalty < 1.0:
            raise ValueError("transmittance_penalty must be in [0, 1)")
        if self.cost_per_year < 0:
            raise ValueError("cost_per_year must be >= 0")


@dataclass(frozen=True)
class Scenario:
    """Inputs for one simulation. Defaults are example assumptions only."""

    name: str = "scenario"
    size_kwp: float = 10.0
    daily_yield: ArrayLike = 3.8  # kWh/kWp/day, constant or per-day series
    rate_pct_per_day: float = 0.2  # soiling-ratio drop, percentage points/day
    clean_every: Optional[int] = None  # days; None = never clean
    cost_per_clean: float = 0.0
    tariff: float = 0.5  # currency per kWh
    currency: str = "MYR"  # label only
    days: int = 365
    min_ratio: float = 0.0
    rain_mm: Optional[ArrayLike] = None  # daily rainfall series (mm)
    rain_threshold_mm: Optional[float] = None  # None disables rain cleaning
    rain_recovery: float = 1.0  # fraction of soiling loss removed by rain
    coating: Coating = Coating()

    def __post_init__(self) -> None:
        if self.size_kwp <= 0:
            raise ValueError("size_kwp must be > 0")
        if self.rate_pct_per_day < 0:
            raise ValueError("rate_pct_per_day must be >= 0")
        if self.clean_every is not None and self.clean_every < 1:
            raise ValueError("clean_every must be >= 1 or None")
        if self.days < 1:
            raise ValueError("days must be >= 1")
        if not 0.0 <= self.min_ratio <= 1.0:
            raise ValueError("min_ratio must be in [0, 1]")
        if not 0.0 <= self.rain_recovery <= 1.0:
            raise ValueError("rain_recovery must be in [0, 1]")
        if self.cost_per_clean < 0 or self.tariff < 0:
            raise ValueError("costs and tariff must be >= 0")


@dataclass(frozen=True)
class Result:
    scenario: Scenario
    daily: pd.DataFrame  # columns: yield, rain_mm, cleaned, rain_cleaned, soiling_ratio, energy_kwh, reference_kwh
    reference_kwh: float
    energy_kwh: float
    kwh_lost: float
    loss_pct: float
    revenue_lost: float
    n_cleanings: int
    cleaning_cost: float
    coating_cost: float

    @property
    def total_cost(self) -> float:
        """Revenue lost + cleaning cost + coating cost."""
        return self.revenue_lost + self.cleaning_cost + self.coating_cost

    def summary(self) -> dict:
        return {
            "scenario": self.scenario.name,
            "energy_kwh": self.energy_kwh,
            "kwh_lost": self.kwh_lost,
            "loss_pct": self.loss_pct,
            "revenue_lost": self.revenue_lost,
            "n_cleanings": self.n_cleanings,
            "cleaning_cost": self.cleaning_cost,
            "coating_cost": self.coating_cost,
            "total_cost": self.total_cost,
        }


def _series(value: ArrayLike, days: int, label: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 0:
        return np.full(days, float(arr))
    if arr.ndim != 1 or len(arr) < days:
        raise ValueError(f"{label} series must be 1-D with at least {days} values")
    arr = arr[:days]
    if np.isnan(arr).any():
        raise ValueError(f"{label} series contains missing values")
    return arr


def load_rain_csv(path: Union[str, Path], column: Optional[str] = None) -> pd.Series:
    """Read daily rainfall (mm) from a CSV.

    Uses ``column`` if given, otherwise a column named ``rain_mm``, otherwise
    the last numeric column. Lines starting with '#' are ignored.
    """
    df = pd.read_csv(path, comment="#")
    if column is None:
        if "rain_mm" in df.columns:
            column = "rain_mm"
        else:
            numeric = df.select_dtypes("number").columns
            if len(numeric) == 0:
                raise ValueError(f"no numeric rainfall column in {path}")
            column = numeric[-1]
    rain = pd.to_numeric(df[column], errors="raise").astype(float)
    if rain.isna().any() or (rain < 0).any():
        raise ValueError("rainfall must be non-negative with no missing values")
    return rain.reset_index(drop=True)


def simulate(scenario: Scenario) -> Result:
    """Run the daily simulation for one scenario."""
    s = scenario
    days = s.days
    yld = _series(s.daily_yield, days, "yield")
    rain = _series(s.rain_mm, days, "rain") if s.rain_mm is not None else np.zeros(days)
    rain_on = s.rain_mm is not None and s.rain_threshold_mm is not None

    rate = s.rate_pct_per_day / 100.0 * (1.0 - s.coating.rate_reduction)
    trans = 1.0 - s.coating.transmittance_penalty

    sr = np.empty(days)
    cleaned = np.zeros(days, dtype=bool)
    rain_cleaned = np.zeros(days, dtype=bool)
    ratio = 1.0
    for d in range(days):
        if s.clean_every is not None and d > 0 and d % s.clean_every == 0:
            ratio = 1.0
            cleaned[d] = True
        if rain_on and rain[d] >= s.rain_threshold_mm:
            ratio = ratio + s.rain_recovery * (1.0 - ratio)
            rain_cleaned[d] = True
        sr[d] = ratio
        ratio = max(s.min_ratio, ratio - rate)

    reference = yld * s.size_kwp
    energy = reference * trans * sr
    ref_total = float(reference.sum())
    e_total = float(energy.sum())
    lost = ref_total - e_total
    n_clean = int(cleaned.sum())
    daily = pd.DataFrame(
        {
            "day": np.arange(1, days + 1),
            "yield": yld,
            "rain_mm": rain,
            "cleaned": cleaned,
            "rain_cleaned": rain_cleaned,
            "soiling_ratio": sr,
            "energy_kwh": energy,
            "reference_kwh": reference,
        }
    )
    return Result(
        scenario=s,
        daily=daily,
        reference_kwh=ref_total,
        energy_kwh=e_total,
        kwh_lost=lost,
        loss_pct=100.0 * lost / ref_total if ref_total > 0 else 0.0,
        revenue_lost=lost * s.tariff,
        n_cleanings=n_clean,
        cleaning_cost=n_clean * s.cost_per_clean,
        coating_cost=s.coating.cost_per_year * days / 365.0,
    )


def compare(scenarios: Sequence[Scenario]) -> pd.DataFrame:
    """Simulate several scenarios and return one summary row each."""
    return pd.DataFrame([simulate(s).summary() for s in scenarios]).set_index("scenario")


def standard_scenarios(
    base: Scenario, clean_every: int, coating: Coating, coated_clean_every: Optional[int] = None
) -> list:
    """No cleaning / every N days / coating only / coating + less-frequent cleaning."""
    if coated_clean_every is None:
        coated_clean_every = clean_every * 2
    none = Coating()
    return [
        replace(base, name="no cleaning", clean_every=None, coating=none),
        replace(base, name=f"clean every {clean_every} d", clean_every=clean_every, coating=none),
        replace(base, name="coating, no cleaning", clean_every=None, coating=coating),
        replace(
            base,
            name=f"coating + clean every {coated_clean_every} d",
            clean_every=coated_clean_every,
            coating=coating,
        ),
    ]


def optimize_interval(base: Scenario, max_interval: int = 120, min_interval: int = 1):
    """Sweep cleaning intervals and find the one with the lowest total cost.

    Returns ``(best_interval, table)`` where ``best_interval`` is an int or
    ``None`` (never cleaning is cheapest) and ``table`` is indexed by interval
    (0 denotes "never clean").
    """
    if min_interval < 1 or max_interval < min_interval:
        raise ValueError("need 1 <= min_interval <= max_interval")
    rows = []
    for n in [None, *range(min_interval, max_interval + 1)]:
        r = simulate(replace(base, name=str(n or "never"), clean_every=n))
        row = r.summary()
        row["interval_days"] = 0 if n is None else n
        rows.append(row)
    table = pd.DataFrame(rows).set_index("interval_days").drop(columns="scenario")
    best = int(table["total_cost"].idxmin())  # first minimum; 0 (never) listed first
    return (None if best == 0 else best), table
