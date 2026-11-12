"""Optional per-request control of the simulated bank, for testing.

Only read when the server runs in simulation mode. Otherwise the headers are
ignored and every request uses the normal bank behaviour.
"""

from dataclasses import dataclass
from typing import Optional

from app.validation import ValidationFailed

DELAY_HEADER = "X-Simulate-Bank-Delay"
FAILURE_HEADER = "X-Simulate-Bank-Failure"
MAX_SIMULATED_DELAY_SECONDS = 30.0


@dataclass(frozen=True)
class BankSimulation:
    # None means use the normal processing delay
    delay_seconds: Optional[float] = None
    fail: bool = False


NO_SIMULATION = BankSimulation()


def parse_simulation_headers(raw_delay: Optional[str], raw_failure: Optional[str]) -> BankSimulation:
    delay = None
    if raw_delay is not None:
        try:
            delay = float(raw_delay)
        except ValueError:
            delay = -1.0
        if not 0 <= delay <= MAX_SIMULATED_DELAY_SECONDS:
            raise ValidationFailed(
                f"{DELAY_HEADER} must be a number of seconds between 0 and {MAX_SIMULATED_DELAY_SECONDS:g}."
            )

    fail = False
    if raw_failure is not None:
        value = raw_failure.strip().lower()
        if value not in ("true", "false"):
            raise ValidationFailed(f"{FAILURE_HEADER} must be true or false.")
        fail = value == "true"

    return BankSimulation(delay_seconds=delay, fail=fail)
