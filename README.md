# soiling-loss-calc

Estimate the energy and money a PV system loses to **soiling** (dust and dirt on the modules), and compare
cleaning schedules and anti-soiling / self-cleaning coatings. Small, transparent, dependency-light
(numpy + pandas).

> **Important: use your own data.** Every default value in this package (soiling rate, yield, tariff,
> rain threshold, costs) is an **example assumption for demonstration only**. None of them is measured
> data for any location. Soiling rates vary widely between sites and seasons, so use your own site's
> **measured** soiling rate (for example from a soiling station or clean/dirty reference cells), your
> own yield data, tariff and cleaning quotes. The results are only as good as those inputs.

## Install

```bash
pip install -e .              # core (numpy, pandas)
pip install -e ".[plot,dev]"  # optional: matplotlib, pytest
```

## Model

The simulation runs one day at a time:

| Step | Rule |
|---|---|
| Soiling | Soiling ratio SR starts at 1.0 (clean) and drops by `rate` percentage points per day, never below `--min-ratio`. |
| Scheduled cleaning | On day N, 2N, 3N, ... SR is reset to 1.0 and one cleaning cost is charged. |
| Rain (optional) | If daily rain >= `--rain-threshold` mm, a fraction `--rain-recovery` of the current loss is removed (1 = full reset). |
| Coating (optional) | Soiling rate is multiplied by `1 - reduction`; energy is multiplied by `1 - transmittance penalty`; an annual coating cost can be added. |
| Energy | `yield (kWh/kWp/day) x size (kWp) x (1 - penalty) x SR` |

Losses are measured against a clean, uncoated reference system. Outputs: energy loss (%), kWh lost,
revenue lost (`kWh lost x tariff`), cleaning cost, coating cost and total cost
(revenue lost + cleaning + coating). The currency is only a label (`--currency`, default `MYR`).

This is a linear-accumulation model. It does not model non-linear soiling, cementation, soiling
that rain cannot remove, or the effect of soiling on the inverter operating point.

## Worked example

Inputs below are **example assumptions**, not measurements: a 10 kWp system, clean yield
3.8 kWh/kWp/day, soiling rate 0.2 %/day, energy valued at 0.5 per kWh.

### 1. Simulate one schedule

```console
$ soiling simulate --size 10 --yield 3.8 --rate 0.2 --clean-every 30 --tariff 0.5
Days simulated:        365
Clean reference:       13,870 kWh
Energy with soiling:   13,473 kWh
Energy lost:           397 kWh (2.87 %)
Revenue lost:          198.74 MYR
Cleanings:             12 (cost 0.00 MYR)
Total cost of soiling: 198.74 MYR
Mean soiling ratio:    0.9713
Note: default inputs are example assumptions, not measured data for any location. Use your own site's measured soiling rate.
```

Add `-o daily.csv` to write the day-by-day soiling ratio and energy.

### 2. Compare scenarios

With an assumed cleaning cost of 40 per visit and an assumed coating that halves the soiling rate,
costs 1 % transmittance and 100 per year:

```console
$ soiling compare --size 10 --yield 3.8 --rate 0.2 --clean-every 30 --tariff 0.5 \
    --clean-cost 40 --coating-reduction 0.5 --coating-penalty 0.01 --coating-cost 100
                           energy_kwh kwh_lost loss_pct revenue_lost (MYR)  n_cleanings cleaning_cost (MYR) coating_cost (MYR) total_cost (MYR)
scenario                                                                                                                                       
no cleaning                     8,821    5,049    36.40           2,524.34            0                0.00               0.00         2,524.34
clean every 30 d               13,473      397     2.87             198.74           12              480.00               0.00           678.74
coating, no cleaning           11,232    2,638    19.02           1,318.90            0                0.00             100.00         1,418.90
coating + clean every 60 d     13,331      539     3.88             269.30            6              240.00             100.00           609.30
Note: default inputs are example assumptions, not measured data for any location. Use your own site's measured soiling rate.
```

The coated system is cleaned every `2 x --clean-every` days unless you set `--coated-clean-every`.

### 3. Find the cost-optimal cleaning interval

Sweeps intervals from 1 to 120 days (plus "never") and minimises total cost:

```console
$ soiling optimize --size 10 --yield 3.8 --rate 0.2 --tariff 0.5 --clean-cost 40
              energy_kwh kwh_lost loss_pct revenue_lost (MYR)  n_cleanings cleaning_cost (MYR) coating_cost (MYR) total_cost (MYR)
interval_days                                                                                                                     
46                13,251      619     4.46             309.62            7              280.00               0.00           589.62
47                13,247      623     4.49             311.49            7              280.00               0.00           591.49
41                13,321      549     3.96             274.59            8              320.00               0.00           594.59
53                13,159      711     5.12             355.26            6              240.00               0.00           595.26
48                13,239      631     4.55             315.48            7              280.00               0.00           595.48

Cost-optimal: clean every 46 days -> total cost 589.62 MYR/period (loss 4.46 %, 7 cleanings)
Note: default inputs are example assumptions, not measured data for any location. Use your own site's measured soiling rate.
```

Use `-o sweep.csv` to save the whole sweep. Combine `optimize` with the coating options to find the
best interval for a coated system.

### 4. Rain cleaning

[`examples/rain_example.csv`](examples/rain_example.csv) is **synthetic** rainfall made with a random
number generator. It is not data for any place. Replace it with your own daily rainfall (a
`rain_mm` column, one row per day; the file length sets the number of simulated days).

```console
$ soiling simulate --size 10 --yield 3.8 --rate 0.2 --clean-every 60 --tariff 0.5 \
    --clean-cost 40 --rain examples/rain_example.csv --rain-threshold 5
Days simulated:        365
Clean reference:       13,870 kWh
Energy with soiling:   13,671 kWh
Energy lost:           199 kWh (1.43 %)
Revenue lost:          99.26 MYR
Cleanings:             6 (cost 240.00 MYR)
Rain cleaning days:    40
Total cost of soiling: 339.26 MYR
Mean soiling ratio:    0.9857
Note: default inputs are example assumptions, not measured data for any location. Use your own site's measured soiling rate.
```

Use `--rain-recovery 0.5` if a rain event removes only part of the dust at your site.
A per-day yield series can be supplied with `--yield-file` (CSV with a `yield` column).

## Python API

```python
from dataclasses import replace
from soiling import Scenario, Coating, simulate, compare, standard_scenarios, optimize_interval

base = Scenario(size_kwp=10, daily_yield=3.8, rate_pct_per_day=0.2,  # example assumptions
                tariff=0.5, cost_per_clean=40)
r = simulate(replace(base, clean_every=30))
print(r.loss_pct, r.kwh_lost, r.revenue_lost, r.total_cost)
r.daily.head()                                   # day-by-day DataFrame

print(compare(standard_scenarios(base, 30, Coating(rate_reduction=0.5, transmittance_penalty=0.01))))
best, sweep = optimize_interval(base, max_interval=120)   # best is None if never cleaning is cheapest
```

`daily_yield` and `rain_mm` accept a constant or a per-day array/Series.
`load_rain_csv(path)` reads a rainfall file.

## Tests

```bash
pytest
```

## License

MIT (c) 2026 Mohammad Ghadimimehr. See [LICENSE](LICENSE).
