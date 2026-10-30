from decimal import Decimal

import pytest

from ps5_monitor.config import ConfigError, load_retailers, load_settings, parse_retailers

GOOD_ENV = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}


def test_defaults():
    s = load_settings(GOOD_ENV)
    assert s.max_price == Decimal("999.99") and s.min_price_drop == Decimal("10.00")
    assert s.health_summary is False and s.respect_robots is True


def test_max_price_from_env():
    assert load_settings({**GOOD_ENV, "MAX_PRICE_EUR": "899,50"}).max_price == Decimal("899.50")


@pytest.mark.parametrize("env", [
    {"MAX_PRICE_EUR": "abc"}, {"MAX_PRICE_EUR": "-5"}, {"MIN_PRICE_DROP_EUR": "x"}, {"HTTP_TIMEOUT": "0"},
    {"HTTP_MAX_RETRIES": "100"}, {"HEALTH_SUMMARY": "maybe"}, {"LOG_FORMAT": "xml"}, {"HEALTH_SUMMARY_HOUR": "30"},
])
def test_invalid_values_are_reported(env):
    with pytest.raises(ConfigError):
        load_settings({**GOOD_ENV, **env})


def test_missing_telegram_credentials_have_clear_errors_and_all_problems_are_listed():
    with pytest.raises(ConfigError) as err:
        load_settings({})
    assert any("TELEGRAM_BOT_TOKEN" in p for p in err.value.problems)
    assert any("TELEGRAM_CHAT_ID" in p for p in err.value.problems)


def test_telegram_not_required_for_dry_run():
    assert load_settings({}, require_telegram=False).telegram_token is None


def test_secrets_are_not_in_repr():
    assert "123:secret" not in repr(load_settings({"TELEGRAM_BOT_TOKEN": "123:secret", "TELEGRAM_CHAT_ID": "999"}))


def cfg(**retailers):
    return {"retailers": retailers}


def test_valid_retailer_config():
    (r,) = parse_retailers(cfg(expert={"enabled": True, "url": "https://www.expert.de/p/1", "shipping_eur": "0", "trusted_sellers": ["Foo"]}))
    assert r.shipping_eur == Decimal("0.00") and r.accepted_sellers == ("Foo",)


@pytest.mark.parametrize("data,fragment", [
    (cfg(unknown={"enabled": True, "url": "https://x.de"}), "unknown retailer"),
    (cfg(saturn={"enabled": True, "url": ""}), "no 'url'"),
    (cfg(saturn={"enabled": True, "url": "http://www.saturn.de/x"}), "https"),
    (cfg(saturn={"enabled": True, "url": "https://www.saturn.de.evil.com/x"}), "https"),
    (cfg(saturn={"enabled": True, "url": "https://www.saturn.de/x", "shipping_eur": "gratis"}), "shipping_eur"),
    (cfg(saturn={"enabled": False, "url": ""}), "no retailer is enabled"),
    ({"nope": 1}, "retailers:"),
])
def test_invalid_retailer_config(data, fragment):
    with pytest.raises(ConfigError, match=fragment):
        parse_retailers(data)


def test_missing_and_example_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_retailers(tmp_path / "nope.yml")
    bad = tmp_path / "bad.yml"
    bad.write_text("retailers: [unclosed")
    with pytest.raises(ConfigError, match="YAML"):
        load_retailers(bad)


def test_example_config_parses_structurally_but_has_nothing_enabled():
    from pathlib import Path
    with pytest.raises(ConfigError, match="no retailer is enabled"):
        load_retailers(Path(__file__).parent.parent / "config" / "retailers.example.yml")
