"""soiling-loss-calc: estimate PV soiling losses and compare cleaning strategies."""
from .model import (
    Coating,
    Result,
    Scenario,
    compare,
    load_rain_csv,
    optimize_interval,
    simulate,
    standard_scenarios,
)

__version__ = "0.1.0"
__all__ = [
    "Coating", "Result", "Scenario", "compare", "load_rain_csv",
    "optimize_interval", "simulate", "standard_scenarios", "__version__",
]
