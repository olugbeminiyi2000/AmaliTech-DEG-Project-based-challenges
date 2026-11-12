import pytest

from app.config import Settings


def test_defaults_are_used_when_nothing_is_set():
    assert Settings.from_env({}) == Settings()


def test_values_are_read_from_the_environment():
    settings = Settings.from_env(
        {
            "PROCESSING_DELAY_SECONDS": "8",
            "BANK_TIMEOUT_SECONDS": "5",
            "MAX_WAIT_SECONDS": "3",
            "KEY_TTL_SECONDS": "60",
            "CLEANUP_INTERVAL_SECONDS": "10",
            "SIMULATION_MODE": "true",
        }
    )

    assert settings == Settings(
        processing_delay_seconds=8,
        bank_timeout_seconds=5,
        max_wait_seconds=3,
        key_ttl_seconds=60,
        cleanup_interval_seconds=10,
        simulation_mode=True,
    )


@pytest.mark.parametrize(
    "environ",
    [
        {"BANK_TIMEOUT_SECONDS": "five"},
        {"MAX_WAIT_SECONDS": "-1"},
        {"SIMULATION_MODE": "maybe"},
    ],
)
def test_invalid_values_stop_the_server_from_starting(environ):
    with pytest.raises(ValueError):
        Settings.from_env(environ)
