import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class Settings:
    # How long the simulated bank takes to charge a payment
    processing_delay_seconds: float = 2.0
    # A bank call that takes longer than this is treated as failed
    bank_timeout_seconds: float = 5.0
    # How long a duplicate request waits for an in-flight one before giving up
    max_wait_seconds: float = 10.0
    # How long a completed key is remembered (24 hours)
    key_ttl_seconds: float = 24 * 60 * 60
    # How often the background cleaner removes expired keys
    cleanup_interval_seconds: float = 60.0
    # Lets X-Simulate-Bank-* headers control the simulated bank. Off by
    # default so a deployed server cannot be told to slow down or fail.
    simulation_mode: bool = False

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        """Build settings from environment variables, falling back to the defaults."""
        environ = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            processing_delay_seconds=_seconds(environ, "PROCESSING_DELAY_SECONDS", defaults.processing_delay_seconds),
            bank_timeout_seconds=_seconds(environ, "BANK_TIMEOUT_SECONDS", defaults.bank_timeout_seconds),
            max_wait_seconds=_seconds(environ, "MAX_WAIT_SECONDS", defaults.max_wait_seconds),
            key_ttl_seconds=_seconds(environ, "KEY_TTL_SECONDS", defaults.key_ttl_seconds),
            cleanup_interval_seconds=_seconds(environ, "CLEANUP_INTERVAL_SECONDS", defaults.cleanup_interval_seconds),
            simulation_mode=_flag(environ, "SIMULATION_MODE", defaults.simulation_mode),
        )


def _seconds(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number of seconds, got {raw!r}.")
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {raw!r}.")
    return value


def _flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}.")
