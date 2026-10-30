from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from ps5_monitor.models import Outcome
from ps5_monitor.storage import Storage
from tests.conftest import NOW, PS_URL, FakeNotifier, Web, build_monitor, fixture, make_offer, retailer_cfg
from ps5_monitor.storage import offer_key


def page(html):
    return Web({PS_URL: html})


def test_qualifying_offer_triggers_exactly_one_notification(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    mon = build_monitor(tmp_path, settings, page(fixture("ps_direct_in_stock.html")), [cfg], notifier)
    first = mon.run_once()
    assert first.notified == 1 and first.qualifying == 1 and first.exit_code == 0
    assert notifier.offers[0][1].total == Decimal("899.99")


def test_repeated_runs_do_not_duplicate_alerts(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    mon = build_monitor(tmp_path, settings, page(fixture("ps_direct_in_stock.html")), [cfg], notifier)
    for _ in range(5):
        mon.run_once()
    assert len(notifier.offers) == 1


def test_minor_html_change_does_not_cause_duplicate(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    web = page(fixture("ps_direct_in_stock.html"))
    mon = build_monitor(tmp_path, settings, web, [cfg], notifier)
    mon.run_once()
    web.pages[PS_URL] = fixture("ps_direct_in_stock.html").replace("Synthetic test fixture", "New banner text").replace(
        "Sony PlayStation 5 Pro Konsole 2 TB", "Sony PlayStation 5 Pro Konsole 2 TB (Neu!)")
    mon.run_once()
    assert len(notifier.offers) == 1


def test_restock_detection(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    web = page(fixture("ps_direct_in_stock.html"))
    mon = build_monitor(tmp_path, settings, web, [cfg], notifier)
    mon.run_once()
    web.pages[PS_URL] = fixture("ps_direct_out_of_stock.html")
    mon.run_once()
    assert len(notifier.offers) == 1  # nothing for the sell-out
    web.pages[PS_URL] = fixture("ps_direct_in_stock.html")
    mon.run_once()
    assert len(notifier.offers) == 2  # alert again after the restock


def _product_page(price):
    return fixture("ps_direct_in_stock.html").replace('"899.99"', f'"{price}"')


def test_price_drop_below_threshold_notifies(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    web = page(_product_page("1049.99"))
    mon = build_monitor(tmp_path, settings, web, [cfg], notifier)
    mon.run_once()
    assert notifier.offers == []  # too expensive: no alert
    web.pages[PS_URL] = _product_page("949.99")
    mon.run_once()
    assert len(notifier.offers) == 1


def test_meaningful_price_reduction_renotifies_but_small_one_does_not(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    web = page(_product_page("949.99"))
    mon = build_monitor(tmp_path, settings, web, [cfg], notifier)
    mon.run_once()
    web.pages[PS_URL] = _product_page("945.00")  # only 4.99 cheaper (< 10 EUR)
    mon.run_once()
    assert len(notifier.offers) == 1
    web.pages[PS_URL] = _product_page("929.99")  # 20 EUR cheaper than the alerted price
    mon.run_once()
    assert len(notifier.offers) == 2
    assert notifier.offers[1][1].total == Decimal("929.99")


def test_unknown_or_unverifiable_pages_never_alert(tmp_path, settings, cfg):
    for name in ["unknown_availability.html", "invalid_price.html", "accessory_controller.html", "refurbished.html"]:
        notifier = FakeNotifier()
        mon = build_monitor(tmp_path, settings, page(fixture(name)), [cfg], notifier, db=tmp_path / f"{name}.db")
        mon.run_once()
        assert notifier.offers == [], name


def test_marketplace_seller_never_alerts(tmp_path, settings):
    url = "https://www.mediamarkt.de/de/product/x.html"
    cfg = retailer_cfg("mediamarkt", url)
    notifier = FakeNotifier()
    mon = build_monitor(tmp_path, settings, Web({url: fixture("mediamarkt_marketplace_seller.html")}), [cfg], notifier)
    assert mon.run_once().qualifying == 0 and notifier.offers == []


def test_unverified_glitch_does_not_reset_state_and_cause_duplicate(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    web = page(fixture("ps_direct_in_stock.html"))
    mon = build_monitor(tmp_path, settings, web, [cfg], notifier)
    mon.run_once()
    web.pages[PS_URL] = fixture("unknown_availability.html")  # glitch: stock not stated
    mon.run_once()
    web.pages[PS_URL] = fixture("ps_direct_in_stock.html")
    mon.run_once()
    assert len(notifier.offers) == 1


def test_failed_retailer_request_does_not_stop_others_and_is_reported(tmp_path, settings, cfg):
    mm_url = "https://www.mediamarkt.de/de/product/x.html"
    web = Web({PS_URL: httpx.ConnectError("down"), mm_url: fixture("mediamarkt_in_stock_shipping.html")})
    notifier = FakeNotifier()
    mon = build_monitor(tmp_path, settings, web, [cfg, retailer_cfg("mediamarkt", mm_url)], notifier)
    summary = mon.run_once()
    outcomes = {r.retailer: r.outcome for r in summary.results}
    assert outcomes == {"playstation_direct": Outcome.ERROR, "mediamarkt": Outcome.OK}
    assert summary.exit_code == 0 and len(notifier.offers) == 1


def test_all_retailers_failing_is_a_monitoring_failure(tmp_path, settings, cfg):
    mon = build_monitor(tmp_path, settings, Web({PS_URL: (500, "oops")}), [cfg], FakeNotifier())
    summary = mon.run_once()
    assert summary.monitoring_failed and summary.exit_code == 1
    assert any("MONITORING FAILURE" in line for line in summary.lines())


def test_layout_change_everywhere_is_a_monitoring_failure(tmp_path, settings, cfg):
    mon = build_monitor(tmp_path, settings, page(fixture("no_jsonld.html")), [cfg], FakeNotifier())
    assert mon.run_once().exit_code == 1


def test_telegram_failure_sets_exit_code_and_is_retried_next_run(tmp_path, settings, cfg):
    web = page(fixture("ps_direct_in_stock.html"))
    broken = FakeNotifier(fail=True)
    mon = build_monitor(tmp_path, settings, web, [cfg], broken)
    summary = mon.run_once()
    assert summary.notify_failures == 1 and summary.exit_code == 1
    working = FakeNotifier()
    mon.notifier = working
    assert mon.run_once().exit_code == 0
    assert len(working.offers) == 1  # not lost: the failed alert was retried
    mon.run_once()
    assert len(working.offers) == 1


def test_state_persists_across_separate_executions(tmp_path, settings, cfg):
    db = tmp_path / "persist.db"
    web = page(fixture("ps_direct_in_stock.html"))
    first = FakeNotifier()
    build_monitor(tmp_path, settings, web, [cfg], first, db=db).run_once()
    # a brand-new process/runner: new Monitor, new connection, same database file
    second = FakeNotifier()
    mon2 = build_monitor(tmp_path, settings, web, [cfg], second, db=db)
    mon2.run_once()
    assert len(first.offers) == 1 and second.offers == []
    status = Storage(db).retailer_statuses()[0]
    assert status["last_outcome"] == "OK" and status["last_success_at"]


def test_last_success_survives_a_later_failure(tmp_path, settings, cfg):
    web = page(fixture("ps_direct_in_stock.html"))
    mon = build_monitor(tmp_path, settings, web, [cfg], FakeNotifier())
    mon.run_once()
    web.pages[PS_URL] = (500, "x")
    mon.run_once()
    row = mon.storage.retailer_statuses()[0]
    assert row["last_outcome"] == "ERROR" and row["last_success_at"] == NOW.isoformat()


def test_dry_run_sends_nothing_and_writes_nothing(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    mon = build_monitor(tmp_path, settings, page(fixture("ps_direct_in_stock.html")), [cfg], notifier)
    summary = mon.run_once(dry_run=True)
    assert summary.would_notify == 1 and notifier.offers == []
    assert mon.storage.known_offers() == [] and mon.storage.retailer_statuses() == []


def test_offer_key_ignores_title_and_query_string():
    a = make_offer(title="A", url=PS_URL + "?utm_source=x")
    b = make_offer(title="B", url=PS_URL)
    assert offer_key(a) == offer_key(b)
    assert offer_key(make_offer(seller="Other")) != offer_key(b)


def test_history_is_recorded_and_pruned(tmp_path, settings, cfg):
    clock = {"now": NOW}
    mon = build_monitor(tmp_path, settings, page(fixture("ps_direct_in_stock.html")), [cfg], FakeNotifier(), clock=lambda: clock["now"])
    mon.run_once()
    count = lambda: mon.storage.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert count() == 1
    clock["now"] = NOW + timedelta(days=120)
    mon.run_once()
    assert count() == 1  # the old observation was pruned, the new one added


def test_health_summary_is_off_by_default_and_sent_once_per_day(tmp_path, settings, cfg):
    notifier = FakeNotifier()
    clock = {"now": datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)}  # 10:00 Berlin
    mon = build_monitor(tmp_path, settings, page(fixture("ps_direct_in_stock.html")), [cfg], notifier, clock=lambda: clock["now"])
    mon.run_once()
    assert mon.maybe_send_health_summary() is False  # disabled by default
    mon.settings = replace(settings, health_summary=True)
    assert mon.maybe_send_health_summary() is True
    assert mon.maybe_send_health_summary() is False  # already sent today
    assert "playstation_direct" in notifier.texts[0] and "OK" in notifier.texts[0]
    clock["now"] += timedelta(days=1)
    assert mon.maybe_send_health_summary() is True
