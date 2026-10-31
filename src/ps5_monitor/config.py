"""Configuration: environment variables for settings/secrets, YAML for retailers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

import yaml

from .models import parse_price, quantize


class ConfigError(Exception):
    """Raised with a human-readable list of everything that is wrong."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class Settings:
    max_price: Decimal = Decimal("999.99")
    min_price_drop: Decimal = Decimal("10.00")
    db_path: Path = Path("state/ps5_monitor.db")
    retailers_config: Path = Path("config/retailers.yml")
    telegram_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)
    health_summary: bool = False
    health_summary_hour: int = 8
    http_timeout: float = 15.0
    http_max_retries: int = 3
    respect_robots: bool = True
    log_level: str = "INFO"
    log_format: str = "json"


@dataclass(frozen=True)
class RetailerConfig:
    key: str
    url: str
    enabled: bool = True
    shipping_eur: Decimal | None = None  # user-declared shipping cost, used if page has none
    allow_third_party: bool = False
    accepted_sellers: tuple[str, ...] = ()  # adapter defaults + trusted_sellers


@dataclass(frozen=True)
class WatcherConfig:
    """An official page that is watched for its 'currently unavailable' notice to disappear."""

    name: str
    url: str
    allowed_hosts: tuple[str, ...]  # hosts this page (and its redirects) may use
    unavailable_phrases: tuple[str, ...]  # lower-case phrases shown while the product is unavailable
    alert_on_first_check: bool = True  # heads-up if no notice is present on the very first check


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def load_settings(env: Mapping[str, str] | None = None, *, require_telegram: bool = True) -> Settings:
    env = os.environ if env is None else env
    problems: list[str] = []

    def get(name: str) -> str | None:
        value = env.get(name)
        return value.strip() if value and value.strip() else None

    max_price = Decimal("999.99")
    if get("MAX_PRICE_EUR"):
        parsed = parse_price(get("MAX_PRICE_EUR"))
        if parsed is None:
            problems.append("MAX_PRICE_EUR must be a positive amount with at most 2 decimals, e.g. 999.99")
        else:
            max_price = parsed

    min_drop = Decimal("10.00")
    if get("MIN_PRICE_DROP_EUR"):
        try:
            min_drop = quantize(Decimal(get("MIN_PRICE_DROP_EUR") or ""))
            if min_drop < 0:
                raise InvalidOperation
        except InvalidOperation:
            problems.append("MIN_PRICE_DROP_EUR must be a non-negative amount, e.g. 10.00")

    def number(name: str, default: float, *, integer: bool = False, low: float = 0, high: float = 1e9):
        raw = get(name)
        if raw is None:
            return default
        try:
            value = int(raw) if integer else float(raw)
        except ValueError:
            problems.append(f"{name} must be a number")
            return default
        if not low <= value <= high:
            problems.append(f"{name} must be between {low} and {high}")
            return default
        return value

    timeout = number("HTTP_TIMEOUT", 15.0, low=1, high=120)
    retries = number("HTTP_MAX_RETRIES", 3, integer=True, low=1, high=6)
    hour = number("HEALTH_SUMMARY_HOUR", 8, integer=True, low=0, high=23)

    try:
        health = _bool(get("HEALTH_SUMMARY"), False)
        robots = _bool(get("RESPECT_ROBOTS"), True)
    except ValueError as exc:
        problems.append(f"{exc} (use true/false)")
        health, robots = False, True

    log_format = (get("LOG_FORMAT") or "json").lower()
    if log_format not in {"json", "text"}:
        problems.append("LOG_FORMAT must be 'json' or 'text'")
    log_level = (get("LOG_LEVEL") or "INFO").upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        problems.append("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    token, chat_id = get("TELEGRAM_BOT_TOKEN"), get("TELEGRAM_CHAT_ID")
    if require_telegram:
        if not token:
            problems.append("TELEGRAM_BOT_TOKEN is not set (see README: Telegram setup)")
        if not chat_id:
            problems.append("TELEGRAM_CHAT_ID is not set (see README: Telegram setup)")

    if problems:
        raise ConfigError(problems)

    return Settings(
        max_price=max_price,
        min_price_drop=min_drop,
        db_path=Path(get("DB_PATH") or "state/ps5_monitor.db"),
        retailers_config=Path(get("RETAILERS_CONFIG") or "config/retailers.yml"),
        telegram_token=token,
        telegram_chat_id=chat_id,
        health_summary=health,
        health_summary_hour=int(hour),
        http_timeout=float(timeout),
        http_max_retries=int(retries),
        respect_robots=robots,
        log_level=log_level,
        log_format=log_format,
    )


def parse_retailers(data: object, *, allow_empty: bool = False) -> list[RetailerConfig]:
    """Validate the YAML structure. Only enabled retailers are returned."""
    from .retailers import REGISTRY  # local import: avoids a cycle with retailers.base

    problems: list[str] = []
    if not isinstance(data, dict):
        raise ConfigError(["config must be a YAML mapping with 'retailers:' and/or 'watchers:'"])
    retailers_raw = data.get("retailers")
    if retailers_raw is None and allow_empty:
        retailers_raw = {}
    if not isinstance(retailers_raw, dict):
        raise ConfigError(["retailers config must contain a 'retailers:' mapping"])

    result: list[RetailerConfig] = []
    for key, raw in retailers_raw.items():
        if key not in REGISTRY:
            problems.append(f"unknown retailer '{key}' (known: {', '.join(sorted(REGISTRY))})")
            continue
        raw = raw or {}
        if not isinstance(raw, dict):
            problems.append(f"retailer '{key}' must be a mapping")
            continue
        if not raw.get("enabled", False):
            continue
        adapter = REGISTRY[key]
        url = str(raw.get("url") or "").strip()
        if not url:
            problems.append(f"retailer '{key}' is enabled but has no 'url' (product URLs are never guessed)")
            continue
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (
            parts.scheme != "https"
            or parts.username
            or not any(host == h or host.endswith("." + h) for h in adapter.allowed_hosts)
        ):
            problems.append(
                f"retailer '{key}': url must be https and on {', '.join(adapter.allowed_hosts)}"
            )
            continue
        shipping = None
        if raw.get("shipping_eur") is not None:
            shipping = parse_price(raw["shipping_eur"], allow_zero=True)
            if shipping is None:
                problems.append(f"retailer '{key}': shipping_eur must be an amount like 0 or 4.99")
                continue
        trusted = raw.get("trusted_sellers") or []
        if not isinstance(trusted, list) or not all(isinstance(t, str) for t in trusted):
            problems.append(f"retailer '{key}': trusted_sellers must be a list of strings")
            continue
        result.append(
            RetailerConfig(
                key=key,
                url=url,
                enabled=True,
                shipping_eur=shipping,
                allow_third_party=bool(raw.get("allow_third_party", False)),
                accepted_sellers=tuple(adapter.default_sellers) + tuple(trusted),
            )
        )
    if problems:
        raise ConfigError(problems)
    if not result and not allow_empty:
        raise ConfigError(["no retailer is enabled; set 'enabled: true' and a 'url' in the retailers config"])
    return result


def parse_watchers(data: object) -> list[WatcherConfig]:
    raw_list = data.get("watchers") if isinstance(data, dict) else None
    if raw_list is None:
        return []
    problems: list[str] = []
    result: list[WatcherConfig] = []
    if not isinstance(raw_list, list):
        raise ConfigError(["'watchers:' must be a list"])
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            problems.append(f"watcher #{i + 1} must be a mapping")
            continue
        if not raw.get("enabled", True):
            continue
        name = str(raw.get("name") or f"watcher-{i + 1}")
        url = str(raw.get("url") or "").strip()
        hosts = raw.get("allowed_hosts")
        phrases = raw.get("unavailable_phrases")
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or not parts.hostname:
            problems.append(f"watcher '{name}': url must be an https URL")
            continue
        if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) and h for h in hosts):
            problems.append(f"watcher '{name}': allowed_hosts must be a non-empty list of host names")
            continue
        if not any(parts.hostname == h or parts.hostname.endswith("." + h) for h in hosts):
            problems.append(f"watcher '{name}': url host is not covered by allowed_hosts")
            continue
        if not isinstance(phrases, list) or not phrases or not all(isinstance(p, str) and p.strip() for p in phrases):
            problems.append(f"watcher '{name}': unavailable_phrases must be a non-empty list of texts")
            continue
        result.append(
            WatcherConfig(
                name=name, url=url, allowed_hosts=tuple(hosts),
                unavailable_phrases=tuple(" ".join(p.lower().split()) for p in phrases),
                alert_on_first_check=bool(raw.get("alert_on_first_check", True)),
            )
        )
    if problems:
        raise ConfigError(problems)
    return result


def load_config(path: Path) -> tuple[list[RetailerConfig], list[WatcherConfig]]:
    """Retailers and watchers from the YAML file; at least one of them must be enabled."""
    if not path.exists():
        raise ConfigError([f"retailers config not found: {path} (copy config/retailers.example.yml)"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError([f"retailers config is not valid YAML: {exc}"]) from exc
    retailers = parse_retailers(data, allow_empty=True)
    watchers = parse_watchers(data)
    if not retailers and not watchers:
        raise ConfigError(["nothing is enabled; enable a retailer or a watcher in the config"])
    return retailers, watchers


def load_retailers(path: Path) -> list[RetailerConfig]:
    if not path.exists():
        raise ConfigError([f"retailers config not found: {path} (copy config/retailers.example.yml)"])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError([f"retailers config is not valid YAML: {exc}"]) from exc
    return parse_retailers(data)
