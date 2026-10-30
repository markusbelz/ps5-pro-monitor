"""Command line entry point: python -m ps5_monitor <command>."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ConfigError, Settings, load_retailers, load_settings
from .http import Fetcher, make_client
from .logging_config import register_secret, setup_logging
from .monitor import Monitor
from .notifier import NotificationError, TelegramNotifier
from .retailers import REGISTRY
from .storage import Storage

log = logging.getLogger("ps5_monitor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ps5_monitor", description="PS5 Pro availability monitor (notify only)")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="check all enabled retailers once and notify on new opportunities")
    run.add_argument("--dry-run", action="store_true", help="check and evaluate, but send nothing and write no state")
    sub.add_parser("test-notify", help="send a test message to Telegram")
    sub.add_parser("check-config", help="validate configuration and list retailers")
    sub.add_parser("health", help="send the health summary now")
    return parser


def _notifier(settings: Settings) -> TelegramNotifier:
    assert settings.telegram_token and settings.telegram_chat_id
    return TelegramNotifier(
        settings.telegram_token, settings.telegram_chat_id,
        timeout=settings.http_timeout, max_attempts=settings.http_max_retries,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    needs_telegram = args.command in {"test-notify", "health"} or (args.command == "run" and not args.dry_run)
    try:
        settings = load_settings(require_telegram=needs_telegram)
    except ConfigError as exc:
        setup_logging()
        for problem in exc.problems:
            log.error("configuration error: %s", problem)
        return 2
    setup_logging(settings.log_level, settings.log_format)
    register_secret(settings.telegram_token)
    register_secret(settings.telegram_chat_id)

    if args.command == "test-notify":
        try:
            _notifier(settings).send_text("✅ PS5 Pro monitor: Telegram test message. Notifications work.")
        except NotificationError as exc:
            log.error("%s", exc)
            return 1
        print("Test message sent.")
        return 0

    try:
        configs = load_retailers(settings.retailers_config)
    except ConfigError as exc:
        for problem in exc.problems:
            log.error("configuration error: %s", problem)
        return 2

    if args.command == "check-config":
        print(f"Max total price: {settings.max_price} EUR")
        for cfg in configs:
            cls = REGISTRY[cfg.key]
            state = "implemented, needs live validation" if not cls.live_validated else "validated"
            print(f"- {cfg.key}: {cfg.url} [{state}]")
        return 0

    retailers = [REGISTRY[cfg.key](cfg) for cfg in configs]
    for retailer in retailers:
        if not retailer.live_validated:
            log.info("adapter not live-validated", extra={"retailer": retailer.key, "note": retailer.limitation})

    storage = Storage(settings.db_path)
    client = make_client(settings.http_timeout)
    try:
        fetcher = Fetcher(client, max_attempts=settings.http_max_retries, respect_robots=settings.respect_robots)
        notifier = _notifier(settings) if settings.telegram_token and settings.telegram_chat_id else None
        monitor = Monitor(settings, retailers, fetcher, storage, notifier)

        if args.command == "health":
            try:
                monitor.maybe_send_health_summary(force=True)
            except NotificationError as exc:
                log.error("%s", exc)
                return 1
            return 0

        summary = monitor.run_once(dry_run=args.dry_run)
        for line in summary.lines():
            print(line)
        if args.dry_run:
            print(f"dry-run: {summary.would_notify} notification(s) would have been sent")
        else:
            try:
                monitor.maybe_send_health_summary()
            except NotificationError as exc:
                log.error("health summary failed: %s", exc)
                return 1
        return summary.exit_code
    finally:
        client.close()
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
