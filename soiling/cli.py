"""Command-line interface: ``soiling simulate | compare | optimize``."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Optional, Sequence

import pandas as pd

from . import __version__
from .model import Coating, Scenario, compare, load_rain_csv, optimize_interval, simulate, standard_scenarios

DISCLAIMER = (
    "Note: default inputs are example assumptions, not measured data for any location. "
    "Use your own site's measured soiling rate."
)


def _common(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("system and economics (defaults are example assumptions)")
    g.add_argument("--size", type=float, default=10.0, help="system size, kWp (default 10)")
    g.add_argument("--yield", dest="daily_yield", type=float, default=3.8,
                   help="clean daily specific yield, kWh/kWp/day (default 3.8)")
    g.add_argument("--yield-file", help="CSV with a per-day 'yield' column (overrides --yield)")
    g.add_argument("--rate", type=float, default=0.2,
                   help="soiling rate, %% soiling-ratio drop per day (default 0.2)")
    g.add_argument("--min-ratio", type=float, default=0.0, help="lowest soiling ratio allowed (default 0)")
    g.add_argument("--tariff", type=float, default=0.5, help="value of energy per kWh (default 0.5)")
    g.add_argument("--currency", default="MYR", help="currency label only (default MYR)")
    g.add_argument("--clean-cost", type=float, default=0.0, help="cost per cleaning (default 0)")
    g.add_argument("--days", type=int, default=365, help="days to simulate (default 365; rain file length if given)")
    r = p.add_argument_group("rain cleaning")
    r.add_argument("--rain", help="CSV of daily rainfall in mm (column 'rain_mm' or last numeric)")
    r.add_argument("--rain-threshold", type=float, default=5.0,
                   help="rain (mm/day) that triggers cleaning (default 5, example)")
    r.add_argument("--rain-recovery", type=float, default=1.0,
                   help="fraction of soiling loss removed by such rain (default 1 = full reset)")
    c = p.add_argument_group("coating")
    c.add_argument("--coating-reduction", type=float, default=0.0,
                   help="fractional reduction of soiling rate, 0-1 (default 0)")
    c.add_argument("--coating-penalty", type=float, default=0.0,
                   help="fractional transmittance loss, 0-1 (default 0)")
    c.add_argument("--coating-cost", type=float, default=0.0, help="coating cost per year (default 0)")


def _base(a: argparse.Namespace, clean_every: Optional[int]) -> Scenario:
    rain = None
    days = a.days
    if a.rain:
        rain = load_rain_csv(a.rain)
        days = len(rain)
    daily_yield = a.daily_yield
    if a.yield_file:
        ys = pd.read_csv(a.yield_file, comment="#")["yield"].astype(float)
        daily_yield = ys.to_numpy()
        days = min(days, len(ys)) if a.rain else len(ys)
    return Scenario(
        name="simulation",
        size_kwp=a.size,
        daily_yield=daily_yield,
        rate_pct_per_day=a.rate,
        clean_every=clean_every,
        cost_per_clean=a.clean_cost,
        tariff=a.tariff,
        currency=a.currency,
        days=days,
        min_ratio=a.min_ratio,
        rain_mm=rain,
        rain_threshold_mm=a.rain_threshold if a.rain else None,
        rain_recovery=a.rain_recovery,
        coating=Coating(a.coating_reduction, a.coating_penalty, a.coating_cost),
    )


def _fmt_table(df: pd.DataFrame, cur: str) -> str:
    out = df.copy()
    for col in ("energy_kwh", "kwh_lost"):
        out[col] = out[col].map("{:,.0f}".format)
    out["loss_pct"] = out["loss_pct"].map("{:.2f}".format)
    for col in ("revenue_lost", "cleaning_cost", "coating_cost", "total_cost"):
        out[col] = out[col].map("{:,.2f}".format)
    out = out.rename(columns={c: f"{c} ({cur})" for c in
                              ("revenue_lost", "cleaning_cost", "coating_cost", "total_cost")})
    return out.to_string()


def cmd_simulate(a: argparse.Namespace) -> int:
    s = _base(a, a.clean_every)
    r = simulate(s)
    cur = s.currency
    print(f"Days simulated:        {s.days}")
    print(f"Clean reference:       {r.reference_kwh:,.0f} kWh")
    print(f"Energy with soiling:   {r.energy_kwh:,.0f} kWh")
    print(f"Energy lost:           {r.kwh_lost:,.0f} kWh ({r.loss_pct:.2f} %)")
    print(f"Revenue lost:          {r.revenue_lost:,.2f} {cur}")
    print(f"Cleanings:             {r.n_cleanings} (cost {r.cleaning_cost:,.2f} {cur})")
    if s.rain_mm is not None:
        print(f"Rain cleaning days:    {int(r.daily['rain_cleaned'].sum())}")
    if s.coating.cost_per_year:
        print(f"Coating cost:          {r.coating_cost:,.2f} {cur}")
    print(f"Total cost of soiling: {r.total_cost:,.2f} {cur}")
    print(f"Mean soiling ratio:    {r.daily['soiling_ratio'].mean():.4f}")
    if a.output:
        r.daily.to_csv(a.output, index=False)
        print(f"Daily results written to {a.output}")
    print(DISCLAIMER)
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    base = _base(a, None)
    coating = base.coating
    if coating.rate_reduction == 0 and coating.transmittance_penalty == 0:
        print("Warning: no coating parameters given; coating scenarios equal uncoated ones.",
              file=sys.stderr)
    table = compare(standard_scenarios(base, a.clean_every, coating, a.coated_clean_every))
    print(_fmt_table(table, base.currency))
    print(DISCLAIMER)
    return 0


def cmd_optimize(a: argparse.Namespace) -> int:
    base = _base(a, None)
    best, table = optimize_interval(base, a.max_interval, a.min_interval)
    cur = base.currency
    if a.output:
        table.to_csv(a.output)
    top = table.sort_values("total_cost").head(a.top)
    top.index = ["never" if i == 0 else str(i) for i in top.index]
    top.index.name = "interval_days"
    print(_fmt_table(top, cur))
    row = table.loc[best or 0]
    label = "never clean" if best is None else f"clean every {best} days"
    print(f"\nCost-optimal: {label} -> total cost {row['total_cost']:,.2f} {cur}/period "
          f"(loss {row['loss_pct']:.2f} %, {int(row['n_cleanings'])} cleanings)")
    if a.output:
        print(f"Full sweep written to {a.output}")
    print(DISCLAIMER)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="soiling", description="PV soiling loss calculator. " + DISCLAIMER)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("simulate", help="simulate one cleaning schedule")
    _common(s)
    s.add_argument("--clean-every", type=int, default=None, help="clean every N days (default: never)")
    s.add_argument("-o", "--output", help="write daily results CSV")
    s.set_defaults(func=cmd_simulate)

    c = sub.add_parser("compare", help="compare no cleaning / cleaning / coating scenarios")
    _common(c)
    c.add_argument("--clean-every", type=int, default=30, help="interval for uncoated cleaning (default 30)")
    c.add_argument("--coated-clean-every", type=int, default=None,
                   help="interval for coated cleaning (default 2x --clean-every)")
    c.set_defaults(func=cmd_compare)

    o = sub.add_parser("optimize", help="find the cost-optimal cleaning interval")
    _common(o)
    o.add_argument("--min-interval", type=int, default=1)
    o.add_argument("--max-interval", type=int, default=120)
    o.add_argument("--top", type=int, default=5, help="rows to print (default 5)")
    o.add_argument("-o", "--output", help="write full sweep CSV")
    o.set_defaults(func=cmd_optimize)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
