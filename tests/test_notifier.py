import logging
from decimal import Decimal

import httpx
import pytest

from ps5_monitor.evaluate import evaluate
from ps5_monitor.logging_config import redact, register_secret, setup_logging
from ps5_monitor.notifier import NotificationError, TelegramNotifier, format_offer_message
from tests.conftest import make_offer, retailer_cfg

TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"


def notifier(handler, sleeps=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TelegramNotifier(TOKEN, "-100987654", client=client, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_message_format(settings, cfg):
    offer = make_offer(seller="Sony", price=Decimal("899.99"), shipping=Decimal("0.00"))
    text = format_offer_message(offer, evaluate(offer, cfg, settings))
    for expected in ["🎮 PS5 PRO AVAILABLE", "Retailer: PlayStation Direct Germany", "Condition: New", "Seller: Sony",
                     "Availability: In stock", "Product price: €899.99", "Shipping: €0.00", "Total: €899.99",
                     "Status: VERIFIED", "Last checked: ", "BUY NOW:\nhttps://"]:
        assert expected in text
    assert "13:32:08 Europe/Berlin" in text  # 12:32:08 UTC on 2026-10-29 is CET (UTC+1)


def test_successful_send_posts_to_bot_api():
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), req.read()
        return httpx.Response(200, json={"ok": True})

    notifier(handler).send_text("hi")
    assert seen["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage" and b'"chat_id":"-100987654"' in seen["body"].replace(b" ", b"")


def test_api_error_does_not_retry_and_never_leaks_token():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    with pytest.raises(NotificationError) as err:
        notifier(handler).send_text("hi")
    assert len(calls) == 1 and "401" in str(err.value) and TOKEN not in str(err.value)


def test_ok_false_with_http_200_is_a_failure():
    with pytest.raises(NotificationError):
        notifier(lambda r: httpx.Response(200, json={"ok": False, "description": "x"})).send_text("hi")


def test_rate_limit_honours_retry_after_then_succeeds():
    sleeps, calls = [], []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 3}})
        return httpx.Response(200, json={"ok": True})

    notifier(handler, sleeps).send_text("hi")
    assert sleeps == [3.0]


def test_server_errors_are_retried_a_bounded_number_of_times():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(NotificationError):
        notifier(handler).send_text("hi")
    assert len(calls) == 3


def test_network_error_message_has_no_token():
    def handler(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")

    with pytest.raises(NotificationError) as err:
        notifier(handler).send_text("hi")
    assert TOKEN not in str(err.value) and "ConnectError" in str(err.value)


def test_logs_never_contain_secrets(capsys):
    setup_logging("DEBUG", "json")
    register_secret("-100987654")
    logging.getLogger("x").info("token %s chat -100987654", TOKEN, extra={"detail": f"url bot{TOKEN}/send"})
    err = capsys.readouterr().err
    assert TOKEN not in err and "-100987654" not in err and "[REDACTED]" in err
    assert TOKEN not in redact(f"https://api.telegram.org/bot{TOKEN}/sendMessage")
