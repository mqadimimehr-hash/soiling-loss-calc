from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from soiling import Coating, Scenario, compare, load_rain_csv, optimize_interval, simulate, standard_scenarios
from soiling.cli import main

EXAMPLE_RAIN = Path(__file__).resolve().parents[1] / "examples" / "rain_example.csv"
BASE = Scenario(size_kwp=10, daily_yield=4.0, rate_pct_per_day=0.2, tariff=0.5, days=365)


def test_no_soiling_no_loss():
    r = simulate(replace(BASE, rate_pct_per_day=0.0))
    assert r.kwh_lost == pytest.approx(0.0)
    assert r.energy_kwh == pytest.approx(10 * 4.0 * 365)


def test_linear_decline_matches_closed_form():
    r = simulate(replace(BASE, days=10))
    expected_sr = 1 - 0.002 * np.arange(10)
    np.testing.assert_allclose(r.daily["soiling_ratio"], expected_sr)
    assert r.kwh_lost == pytest.approx(40 * (0.002 * np.arange(10)).sum())
    assert r.revenue_lost == pytest.approx(r.kwh_lost * 0.5)


def test_min_ratio_floor():
    r = simulate(replace(BASE, rate_pct_per_day=10, min_ratio=0.6))
    assert r.daily["soiling_ratio"].min() == pytest.approx(0.6)


def test_cleaning_resets_and_counts():
    r = simulate(replace(BASE, clean_every=30, cost_per_clean=100))
    assert r.n_cleanings == 12  # days 30, 60, ..., 360
    assert r.cleaning_cost == 1200
    assert r.daily.loc[30, "soiling_ratio"] == 1.0
    assert r.daily.loc[29, "soiling_ratio"] == pytest.approx(1 - 29 * 0.002)
    assert r.loss_pct < simulate(BASE).loss_pct


def test_rain_full_and_partial_recovery():
    rain = np.zeros(20)
    rain[10] = 12.0
    rain[15] = 2.0  # below threshold
    full = simulate(replace(BASE, days=20, rain_mm=rain, rain_threshold_mm=5))
    assert full.daily.loc[10, "soiling_ratio"] == 1.0
    assert full.daily["rain_cleaned"].sum() == 1
    half = simulate(replace(BASE, days=20, rain_mm=rain, rain_threshold_mm=5, rain_recovery=0.5))
    assert half.daily.loc[10, "soiling_ratio"] == pytest.approx(1 - 0.5 * 10 * 0.002)
    # rain given but threshold None -> ignored
    off = simulate(replace(BASE, days=20, rain_mm=rain))
    assert off.kwh_lost == pytest.approx(simulate(replace(BASE, days=20)).kwh_lost)


def test_coating_rate_reduction_and_penalty():
    c = simulate(replace(BASE, coating=Coating(rate_reduction=0.5)))
    half_rate = simulate(replace(BASE, rate_pct_per_day=0.1))
    assert c.kwh_lost == pytest.approx(half_rate.kwh_lost)
    p = simulate(replace(BASE, rate_pct_per_day=0, coating=Coating(transmittance_penalty=0.02, cost_per_year=50)))
    assert p.loss_pct == pytest.approx(2.0)
    assert p.coating_cost == pytest.approx(50)
    assert p.total_cost == pytest.approx(p.revenue_lost + 50)


def test_yield_series_and_validation():
    y = np.linspace(3, 5, 365)
    r = simulate(replace(BASE, daily_yield=y, rate_pct_per_day=0))
    assert r.energy_kwh == pytest.approx(10 * y.sum())
    with pytest.raises(ValueError):
        simulate(replace(BASE, daily_yield=[1.0, 2.0]))
    for bad in (dict(size_kwp=0), dict(rate_pct_per_day=-1), dict(clean_every=0), dict(rain_recovery=2)):
        with pytest.raises(ValueError):
            replace(BASE, **bad)
    with pytest.raises(ValueError):
        Coating(rate_reduction=1.5)


def test_compare_standard_scenarios():
    table = compare(standard_scenarios(replace(BASE, cost_per_clean=50), 30, Coating(0.5, 0.01)))
    assert len(table) == 4
    assert table.loc["no cleaning", "loss_pct"] > table.loc["clean every 30 d", "loss_pct"]
    assert "coating + clean every 60 d" in table.index


def test_optimize_matches_brute_force():
    base = replace(BASE, cost_per_clean=20)
    best, table = optimize_interval(base, max_interval=90)
    costs = {n: simulate(replace(base, clean_every=n)).total_cost for n in range(1, 91)}
    assert best == min(costs, key=costs.get)
    assert table.loc[best, "total_cost"] == pytest.approx(costs[best])
    assert 0 in table.index and len(table) == 91


def test_optimize_free_cleaning_and_expensive_cleaning():
    assert optimize_interval(replace(BASE, cost_per_clean=0), max_interval=10)[0] == 1
    assert optimize_interval(replace(BASE, cost_per_clean=1e6), max_interval=10)[0] is None


def test_load_rain_csv_example():
    rain = load_rain_csv(EXAMPLE_RAIN)
    assert len(rain) == 365 and (rain >= 0).all()


def test_cli_commands(capsys, tmp_path):
    assert main(["simulate", "--size", "10", "--yield", "3.8", "--rate", "0.2",
                 "--clean-every", "30", "--tariff", "0.5", "-o", str(tmp_path / "d.csv")]) == 0
    out = capsys.readouterr().out
    assert "Energy lost" in out and "example assumptions" in out
    assert (tmp_path / "d.csv").exists()
    assert main(["simulate", "--rain", str(EXAMPLE_RAIN), "--rain-threshold", "5"]) == 0
    assert "Rain cleaning days" in capsys.readouterr().out
    assert main(["optimize", "--clean-cost", "30", "--max-interval", "60"]) == 0
    assert "Cost-optimal" in capsys.readouterr().out
    assert main(["compare", "--coating-reduction", "0.5", "--clean-cost", "30"]) == 0
    assert "no cleaning" in capsys.readouterr().out
    assert main(["simulate", "--rate", "-1"]) == 2
