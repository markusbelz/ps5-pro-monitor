"""End-to-end through the CLI with mocked HTTP (no live sites, no real Telegram)."""

import httpx
import pytest

from ps5_monitor import main as cli
from ps5_monitor.notifier import TelegramNotifier
from tests.conftest import PS_URL, fixture

TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789"


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "retailers.yml"
    cfg.write_text(f'retailers:\n  playstation_direct:\n    enabled: true\n    url: "{PS_URL}"\n    shipping_eur: 0\n')
    for k, v in {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "42", "RETAILERS_CONFIG": str(cfg),
                 "DB_PATH": str(tmp_path / "s.db"), "RESPECT_ROBOTS": "false", "LOG_FORMAT": "text"}.items():
        monkeypatch.setenv(k, v)
    return tmp_path


def wire(monkeypatch, page, telegram):
    def site(req):
        return httpx.Response(200, text=page) if str(req.url) == PS_URL else httpx.Response(404)

    monkeypatch.setattr(cli, "make_client", lambda timeout: httpx.Client(transport=httpx.MockTransport(site)))
    monkeypatch.setattr(
        cli, "TelegramNotifier",
        lambda token, chat, **kw: TelegramNotifier(token, chat, client=httpx.Client(transport=httpx.MockTransport(telegram)),
                                                   sleep=lambda s: None),
    )


def test_run_sends_one_alert_then_stays_quiet(env, monkeypatch, capsys):
    sent = []

    def telegram(req):
        sent.append(req.read())
        return httpx.Response(200, json={"ok": True})

    wire(monkeypatch, fixture("ps_direct_in_stock.html"), telegram)
    assert cli.main(["run"]) == 0
    assert cli.main(["run"]) == 0
    assert len(sent) == 1 and b"PS5 PRO AVAILABLE" in sent[0]
    assert TOKEN not in capsys.readouterr().err


def test_run_exits_nonzero_when_telegram_is_down(env, monkeypatch):
    wire(monkeypatch, fixture("ps_direct_in_stock.html"), lambda req: httpx.Response(500))
    assert cli.main(["run"]) == 1


def test_run_exits_nonzero_when_site_unreachable(env, monkeypatch):
    wire(monkeypatch, "", lambda req: httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(cli, "make_client", lambda t: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    assert cli.main(["run"]) == 1


def test_test_notify_command(env, monkeypatch):
    sent = []
    wire(monkeypatch, "", lambda req: (sent.append(req.read()), httpx.Response(200, json={"ok": True}))[1])
    assert cli.main(["test-notify"]) == 0 and b"test message" in sent[0]


def test_dry_run_needs_no_telegram_credentials(env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    wire(monkeypatch, fixture("ps_direct_in_stock.html"), lambda req: pytest.fail("no telegram in dry-run"))
    assert cli.main(["run", "--dry-run"]) == 0


def test_missing_credentials_exit_code_2(env, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert cli.main(["test-notify"]) == 2


def test_loop_mode_runs_multiple_rounds(monkeypatch):
    from ps5_monitor import main as main_mod

    calls = []

    class FakeSummary:
        exit_code = 0
        would_notify = 0
        def lines(self): return []

    class FakeMonitor:
        def __init__(self, *a, **k): pass
        def run_once(self, dry_run=False):
            calls.append(dry_run)
            return FakeSummary()
        def maybe_send_health_summary(self, force=False): pass

    monkeypatch.setattr(main_mod, "Monitor", FakeMonitor)
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)
    ticks = iter(range(0, 1000, 5))
    monkeypatch.setattr(main_mod.time, "monotonic", lambda: float(next(ticks)))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("DB_PATH", ":memory:")
    rc = main_mod.main(["run", "--loop-seconds", "60", "--interval", "10"])
    assert rc == 0 and len(calls) > 1 and not any(calls)
