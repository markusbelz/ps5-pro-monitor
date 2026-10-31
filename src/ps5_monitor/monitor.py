"""Monitoring engine: check retailers, evaluate offers, deduplicate, notify, persist."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from .config import RetailerConfig, Settings, WatcherConfig
from .evaluate import evaluate
from .http import Fetcher
from .models import Evaluation, Offer, Outcome, RetailerResult, Verdict
from .notifier import BERLIN, NotificationError, Notifier, berlin_time, euro
from .retailers import Retailer
from .storage import OfferState, Storage, offer_key
from .watcher import WatchResult, check_watcher, load_state, state_key

log = logging.getLogger(__name__)

COUNTED_OUTCOMES = {Outcome.OK, Outcome.NO_DATA, Outcome.BLOCKED, Outcome.ERROR}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RunSummary:
    results: list[RetailerResult] = field(default_factory=list)
    watch_results: list[WatchResult] = field(default_factory=list)
    offers_seen: int = 0
    qualifying: int = 0
    notified: int = 0
    notify_failures: int = 0
    would_notify: int = 0  # dry-run only

    @property
    def monitoring_failed(self) -> bool:
        """True when no retailer could be checked successfully: 'no offers' would be a lie."""
        checked = [r for r in [*self.results, *self.watch_results] if r.outcome in COUNTED_OUTCOMES]
        return not checked or all(r.outcome is not Outcome.OK for r in checked)

    @property
    def exit_code(self) -> int:
        return 1 if self.monitoring_failed or self.notify_failures else 0

    def lines(self) -> list[str]:
        out = []
        for r in self.results:
            detail = f" - {r.error}" if r.error else ""
            out.append(f"{r.retailer_name}: {r.outcome.value} ({len(r.offers)} offers){detail}")
        for w in self.watch_results:
            detail = w.detail if w.outcome is Outcome.OK else (w.error or "")
            out.append(f"[watcher] {w.name}: {w.outcome.value} - {detail}")
        out.append(
            f"offers seen={self.offers_seen} qualifying={self.qualifying} "
            f"notified={self.notified} notify_failures={self.notify_failures}"
        )
        if self.monitoring_failed:
            out.append("MONITORING FAILURE: no retailer could be checked successfully")
        return out


def notification_reason(prev: OfferState | None, evaluation: Evaluation, settings: Settings) -> str | None:
    """Why a QUALIFYING offer should trigger a notification now, or None to stay quiet."""
    total = evaluation.total
    if prev is None:
        return "first qualifying observation"
    if not prev.qualifying:
        if prev.total is not None and prev.total > settings.max_price and prev.stock == "IN_STOCK":
            return "price dropped below threshold"
        return "available again (restock)"
    if (
        total is not None
        and prev.last_notified_total is not None
        and prev.last_notified_total - total >= settings.min_price_drop
    ):
        return "meaningful price reduction"
    return None


class Monitor:
    def __init__(
        self,
        settings: Settings,
        retailers: list[Retailer],
        fetcher: Fetcher,
        storage: Storage,
        notifier: Notifier | None,
        clock: Callable[[], datetime] = utcnow,
        watchers: list[WatcherConfig] | None = None,
    ):
        self.watchers = watchers or []
        self.settings = settings
        self.retailers = retailers
        self.fetcher = fetcher
        self.storage = storage
        self.notifier = notifier
        self.clock = clock

    def run_once(self, *, dry_run: bool = False) -> RunSummary:
        summary = RunSummary()
        for retailer in self.retailers:
            now = self.clock()
            result = retailer.check(self.fetcher, now)
            summary.results.append(result)
            log.info(
                "retailer checked",
                extra={"retailer": result.retailer, "outcome": result.outcome.value, "offers": len(result.offers),
                       "error": result.error or ""},
            )
            if not dry_run:
                self.storage.record_retailer(result, now)
            for offer in result.offers:
                self._handle_offer(offer, retailer.cfg, summary, dry_run)
        alerted_pages: set[str] = set()
        for watcher in self.watchers:
            self._handle_watcher(watcher, summary, dry_run, alerted_pages)
        if not dry_run:
            self.storage.prune_history(self.clock())
        return summary

    def _handle_watcher(self, cfg: WatcherConfig, summary: RunSummary, dry_run: bool, alerted_pages: set[str]) -> None:
        now = self.clock()
        prev = load_state(self.storage.get_meta(state_key(cfg)))
        result = check_watcher(cfg, self.fetcher, prev, now)
        summary.watch_results.append(result)
        log.info("watcher checked", extra={"watcher": cfg.name, "outcome": result.outcome.value,
                                           "detail": result.detail, "error": result.error or ""})
        if not dry_run:
            self.storage.record_retailer(
                RetailerResult(retailer=f"watch:{cfg.name}", retailer_name=cfg.name,
                               outcome=result.outcome, error=result.error or result.detail), now)
        if result.outcome is not Outcome.OK or result.new_state is None:
            return
        if result.alert and dry_run:
            summary.would_notify += 1
            return
        if dry_run:
            return
        page = result.new_state.get("final", "")
        if result.alert and page in alerted_pages:
            log.info("duplicate watcher alert suppressed (same page already reported)", extra={"watcher": cfg.name})
        elif result.alert and self.notifier is not None:
            try:
                self.notifier.send_text(result.alert)
                alerted_pages.add(page)
                summary.notified += 1
                log.info("watcher notification sent", extra={"watcher": cfg.name})
            except NotificationError as exc:
                summary.notify_failures += 1
                log.error("watcher notification failed", extra={"error": str(exc), "watcher": cfg.name})
                return  # state NOT saved, so the alert is retried on the next run
        self.storage.set_meta(state_key(cfg), json.dumps(result.new_state))

    def _handle_offer(self, offer: Offer, cfg: RetailerConfig, summary: RunSummary, dry_run: bool) -> None:
        summary.offers_seen += 1
        evaluation = evaluate(offer, cfg, self.settings)
        key = offer_key(offer)
        prev = self.storage.get_offer(key)
        log.info(
            "offer evaluated",
            extra={"retailer": offer.retailer, "verdict": evaluation.verdict.value, "stock": offer.stock.value,
                   "price": str(offer.price), "total": str(evaluation.total), "seller": offer.seller or "",
                   "reasons": "; ".join(evaluation.reasons)},
        )

        if evaluation.verdict is Verdict.QUALIFIES:
            summary.qualifying += 1
            reason = notification_reason(prev, evaluation, self.settings)
            sent = False
            if reason and dry_run:
                summary.would_notify += 1
                log.info("dry-run: would notify", extra={"reason": reason, "retailer": offer.retailer})
            elif reason and self.notifier is not None:
                try:
                    self.notifier.send_offer(offer, evaluation)
                    sent = True
                    summary.notified += 1
                    log.info("notification sent", extra={"reason": reason, "retailer": offer.retailer})
                except NotificationError as exc:
                    summary.notify_failures += 1
                    log.error("notification failed", extra={"error": str(exc), "retailer": offer.retailer})
            if dry_run:
                return
            if reason and not sent:
                # Send failed: keep the *previous* qualifying state so the next run tries again.
                qualifying = prev.qualifying if prev else False
            else:
                qualifying = True
            self.storage.save_offer(
                key, offer, evaluation.verdict.value, qualifying=qualifying,
                notified_total=evaluation.total if sent else None,
                notified_at=offer.checked_at if sent else None,
            )
            self.storage.record_observation(key, offer, evaluation.verdict.value)
            return

        if dry_run:
            return
        # REJECTED resets "qualifying" so a later restock/price drop alerts again.
        # UNVERIFIED keeps the previous state: a parse hiccup must not cause a duplicate alert later.
        qualifying = prev.qualifying if (evaluation.verdict is Verdict.UNVERIFIED and prev) else False
        self.storage.save_offer(key, offer, evaluation.verdict.value, qualifying=qualifying)
        self.storage.record_observation(key, offer, evaluation.verdict.value)

    # --- health summary ---------------------------------------------------
    def health_text(self) -> str:
        now = self.clock()
        lines = [f"🩺 PS5 Pro monitor health ({berlin_time(now)})", ""]
        statuses = self.storage.retailer_statuses()
        if not statuses:
            lines.append("No checks recorded yet.")
        for row in statuses:
            ok = row["last_success_at"]
            ok_text = berlin_time(datetime.fromisoformat(ok)) if ok else "never"
            err = f" ({row['last_error']})" if row["last_error"] else ""
            lines.append(f"{row['retailer']}: {row['last_outcome']}{err}; last success {ok_text}")
        offers = self.storage.known_offers()
        if offers:
            lines.append("")
            lines.append("Last known offers:")
            for o in offers:
                total = euro(Decimal(o["total"])) if o["total"] else "total unknown"
                lines.append(f"- {o['retailer']} / {o['seller'] or 'seller ?'}: {o['stock']}, {total}, {o['verdict']}")
        return "\n".join(lines)

    def maybe_send_health_summary(self, *, force: bool = False) -> bool:
        if self.notifier is None or not (force or self.settings.health_summary):
            return False
        local = self.clock().astimezone(BERLIN)
        today = local.date().isoformat()
        if not force and (local.hour < self.settings.health_summary_hour or self.storage.get_meta("health_sent") == today):
            return False
        self.notifier.send_text(self.health_text())
        self.storage.set_meta("health_sent", today)
        return True
