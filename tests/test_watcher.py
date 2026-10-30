import pytest

from ps5_monitor.config import ConfigError, WatcherConfig, load_config, parse_watchers
from ps5_monitor.models import Outcome
from tests.conftest import FakeNotifier, Web, build_monitor

URL = "https://direct.playstation.com/de-de/buy-consoles/playstation5-pro-console-2-tb"
BUY = "https://www.playstation.com/de-de/ps5/ps5-pro/buy-now/"
HOSTS = ("direct.playstation.com", "playstation.com")
CFG = WatcherConfig("PS Direct", URL, HOSTS, ("derzeit nicht vorrätig", "zurzeit nicht verfügbar"))
FILLER = "PlayStation 5 Pro Konsole 2 TB Preis 899,99 € inklusive MwSt. Kostenloser Versand. " * 4


def page(*notices, script=""):
    body = " ".join(f"<p>{n}</p>" for n in notices)
    return f"<html><body><h1>PS5 Pro</h1><p>{FILLER}</p>{body}<script>{script}</script></body></html>"


UNAVAILABLE = page("Zurzeit nicht verfügbar", "Derzeit nicht vorrätig. Schau bald wieder vorbei")
AVAILABLE = page("Auf Lager", "In den Einkaufswagen")


def run(tmp_path, settings, web, notifier, **kw):
    return build_monitor(tmp_path, settings, web, [], notifier, watchers=[CFG], **kw)


def test_unavailable_page_never_alerts_and_baseline_is_saved(tmp_path, settings):
    n = FakeNotifier()
    mon = run(tmp_path, settings, Web({URL: UNAVAILABLE}), n)
    summary = mon.run_once()
    assert n.texts == [] and summary.watch_results[0].outcome is Outcome.OK
    assert summary.exit_code == 0  # a working watcher alone is a healthy run


def test_notice_disappearing_alerts_exactly_once(tmp_path, settings):
    n = FakeNotifier()
    web = Web({URL: UNAVAILABLE})
    mon = run(tmp_path, settings, web, n)
    mon.run_once()
    web.pages[URL] = AVAILABLE
    summary = mon.run_once()
    assert summary.notified == 1 and len(n.texts) == 1
    assert "POSSIBLY AVAILABLE" in n.texts[0] and "NOT verified" in n.texts[0] and URL in n.texts[0]
    mon.run_once()
    mon.run_once()
    assert len(n.texts) == 1  # no repeats while it stays available


def test_sold_out_again_then_next_restock_alerts_again(tmp_path, settings):
    n = FakeNotifier()
    web = Web({URL: UNAVAILABLE})
    mon = run(tmp_path, settings, web, n)
    mon.run_once()
    web.pages[URL] = AVAILABLE
    mon.run_once()
    web.pages[URL] = UNAVAILABLE
    mon.run_once()
    assert len(n.texts) == 1  # going back to unavailable is silent
    web.pages[URL] = AVAILABLE
    mon.run_once()
    assert len(n.texts) == 2


def test_first_check_without_notice_sends_a_heads_up(tmp_path, settings):
    n = FakeNotifier()
    run(tmp_path, settings, Web({URL: AVAILABLE}), n).run_once()
    assert len(n.texts) == 1 and "First check" in n.texts[0]


def test_notice_hidden_in_scripts_does_not_count_as_visible(tmp_path, settings):
    n = FakeNotifier()
    web = Web({URL: UNAVAILABLE})
    mon = run(tmp_path, settings, web, n)
    mon.run_once()
    web.pages[URL] = page("Auf Lager", script="var t = 'Derzeit nicht vorrätig';")
    mon.run_once()
    assert len(n.texts) == 1  # script text is ignored, so the notice is considered gone


def test_empty_or_broken_page_never_triggers_an_alert(tmp_path, settings):
    n = FakeNotifier()
    web = Web({URL: UNAVAILABLE})
    mon = run(tmp_path, settings, web, n)
    mon.run_once()
    web.pages[URL] = "<html><body>Loading...</body></html>"
    summary = mon.run_once()
    assert summary.watch_results[0].outcome is Outcome.NO_DATA and n.texts == []
    web.pages[URL] = (503, "down")
    assert mon.run_once().watch_results[0].outcome is Outcome.ERROR and n.texts == []
    web.pages[URL] = UNAVAILABLE
    mon.run_once()
    assert n.texts == []  # state survived the failures


def test_forbidden_is_blocked_not_available(tmp_path, settings):
    n = FakeNotifier()
    summary = run(tmp_path, settings, Web({URL: (403, "no")}), n).run_once()
    assert summary.watch_results[0].outcome is Outcome.BLOCKED and summary.monitoring_failed and n.texts == []


def test_redirect_to_allowed_official_host_is_followed(tmp_path, settings):
    import httpx
    web = Web({BUY: UNAVAILABLE})
    orig = web.handler

    def handler(req):
        if str(req.url) == URL:
            return httpx.Response(302, headers={"location": BUY})
        return orig(req)

    web.handler = handler
    summary = run(tmp_path, settings, web, FakeNotifier()).run_once()
    assert summary.watch_results[0].outcome is Outcome.OK
    assert "www.playstation.com/de-de/ps5/ps5-pro/buy-now/" in summary.watch_results[0].detail


def test_redirect_to_foreign_host_is_refused(tmp_path, settings):
    import httpx
    web = Web({})
    web.handler = lambda req: httpx.Response(302, headers={"location": "https://evil.example.com/x"})
    assert run(tmp_path, settings, web, FakeNotifier()).run_once().watch_results[0].outcome is Outcome.BLOCKED


def test_telegram_failure_keeps_state_so_the_alert_is_retried(tmp_path, settings):
    web = Web({URL: UNAVAILABLE})
    mon = run(tmp_path, settings, web, FakeNotifier())
    mon.run_once()
    web.pages[URL] = AVAILABLE
    mon.notifier = FakeNotifier(fail=False)
    mon.notifier.send_text = lambda text: (_ for _ in ()).throw(__import__("ps5_monitor.notifier", fromlist=["x"]).NotificationError("boom"))
    summary = mon.run_once()
    assert summary.notify_failures == 1 and summary.exit_code == 1
    good = FakeNotifier()
    mon.notifier = good
    mon.run_once()
    assert len(good.texts) == 1


def test_dry_run_sends_nothing_and_saves_nothing(tmp_path, settings):
    n = FakeNotifier()
    mon = run(tmp_path, settings, Web({URL: AVAILABLE}), n)
    summary = mon.run_once(dry_run=True)
    assert summary.would_notify == 1 and n.texts == []
    assert mon.storage.get_meta("watch:PS Direct") is None


def test_state_survives_a_new_process(tmp_path, settings):
    db = tmp_path / "w.db"
    web = Web({URL: UNAVAILABLE})
    run(tmp_path, settings, web, FakeNotifier(), db=db).run_once()
    web.pages[URL] = AVAILABLE
    n = FakeNotifier()
    run(tmp_path, settings, web, n, db=db).run_once()
    assert len(n.texts) == 1


def test_watcher_appears_in_health_summary(tmp_path, settings):
    from dataclasses import replace
    mon = run(tmp_path, settings, Web({URL: UNAVAILABLE}), FakeNotifier())
    mon.run_once()
    assert "watch:PS Direct" in mon.health_text()


# --- configuration ---------------------------------------------------------
def watcher_yaml(**over):
    base = {"name": "x", "url": URL, "allowed_hosts": ["direct.playstation.com"], "unavailable_phrases": ["Derzeit  nicht vorrätig"]}
    base.update(over)
    return {"watchers": [base]}


def test_valid_watcher_config_normalises_phrases():
    (w,) = parse_watchers(watcher_yaml())
    assert w.unavailable_phrases == ("derzeit nicht vorrätig",)


@pytest.mark.parametrize("over", [
    {"url": "http://direct.playstation.com/x"},
    {"allowed_hosts": []},
    {"allowed_hosts": ["example.com"]},
    {"unavailable_phrases": []},
    {"url": "https://user:pw@direct.playstation.com/x"},
])
def test_invalid_watcher_config(over):
    with pytest.raises(ConfigError):
        parse_watchers(watcher_yaml(**over))


def test_config_with_only_watchers_is_valid(tmp_path):
    import yaml
    f = tmp_path / "c.yml"
    f.write_text(yaml.safe_dump(watcher_yaml()))
    retailers, watchers = load_config(f)
    assert retailers == [] and len(watchers) == 1


def test_config_with_nothing_enabled_is_rejected(tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("retailers:\n  saturn: {enabled: false}\n")
    with pytest.raises(ConfigError, match="nothing is enabled"):
        load_config(f)


def test_shipped_config_is_valid():
    from pathlib import Path
    retailers, watchers = load_config(Path(__file__).parent.parent / "config" / "retailers.yml")
    assert retailers == [] and len(watchers) == 2
