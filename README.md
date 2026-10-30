# PS5 Pro Monitor (Germany)

Watches retailer product pages for a **new Sony PlayStation 5 Pro (2 TB, EU plug)** and sends a
**Telegram message** when a *verified* offer with a **total price (product + shipping) below €1,000** shows up.

> **It only notifies. It never logs in, never adds to a cart, never orders, never enters payment data.**

> **Honest status (please read):** the code and the logic are tested with local fixtures (133 tests).
> **No retailer adapter has been validated against a live product page yet**, and no real Telegram message has
> been sent from this code. See [Supported retailers](#3-supported-retailers-and-known-limitations).
> Run the manual dry-run (section 10) first and see which retailers actually return data.

## Watchers for official pages (main mechanism right now)

From GitHub-hosted runners the retailer pages cannot be used (first dry-run on 2026-10-30): MediaMarkt/Saturn answer
`HTTP 403` (not worked around), Expert has no product data in its HTML, and PlayStation Direct redirects to a retailer
overview. So `config/retailers.yml` watches **two official Sony pages** instead (section `watchers:`):

* `direct.playstation.com/de-de/buy-consoles/playstation5-pro-console-2-tb` (PS5 Pro 2 TB, listed at €899.99, currently "Zurzeit nicht verfügbar / Derzeit nicht vorrätig")
* `www.playstation.com/de-de/ps5/ps5-pro/buy-now/` ("Nicht zum Kauf erhältlich")

Each run the visible page text is checked for the configured "unavailable" phrases. When they **disappear**, you get a
Telegram message *"PS5 PRO: POSSIBLY AVAILABLE (unverified heads-up)"* with the link. Rules:

* This is an early signal only: price, stock and seller are **not** verified, the message says so.
* Going back to "unavailable" is silent; the next restock alerts again. No repeats while the state is unchanged.
* Empty/broken pages, HTTP errors and 403 never alert and never overwrite the saved state.
* If the notice is not on the page at the very first check, you get one heads-up ("either purchasable now or the notice is not visible to the monitor"). The dry-run output shows which phrases were found (`phrases=[...]`); if none are found although the page shows the notice, the text is probably rendered by JavaScript and this watcher cannot see it.
* Phrases are matched against visible text only (scripts/styles ignored). Add watchers for other official pages with `name`, `url`, `allowed_hosts`, `unavailable_phrases`.

The retailer adapters below remain in the code but are disabled in the shipped config.

## 1. What it does

1. Fetches each configured product page (polite, identifies itself, honours `robots.txt`, bounded retries).
2. Extracts price, stock, condition, seller and shipping from the page's schema.org **JSON-LD**.
3. Evaluates every offer with strict rules. Anything unverifiable is `UNVERIFIED` and **never** alerts.
4. Compares with the stored state and notifies only on *new* opportunities (no spam on every run).
5. Persists state between GitHub Actions runs on a dedicated `state` branch.

An offer **qualifies** only if all of these hold:

| Rule | Detail |
|---|---|
| Availability | Retailer explicitly says `IN_STOCK`. Unknown/missing ⇒ no alert. Preorders ⇒ no alert. |
| Condition | Explicitly *new*. Used/refurbished/open-box (field or title) ⇒ rejected. |
| Product | Title must be a PS5 Pro console. Controllers, drives alone, other storage sizes, unclear bundles ⇒ no alert. A console + disc drive bundle is accepted if the total is under the limit. |
| Seller | Sony/the retailer itself. Marketplace/third-party sellers rejected unless `allow_third_party: true`. Unknown seller ⇒ no alert. |
| Price | `total = price + shipping`, `Decimal` arithmetic. Shipping must be on the page or declared by you (`shipping_eur`). Unknown shipping ⇒ no alert. |
| Threshold | `total <= MAX_PRICE_EUR` (default `999.99`), so exactly `€1,000.00` never qualifies. |

Prices are assumed to include German VAT (consumer prices); if the page explicitly says VAT is excluded, the offer is `UNVERIFIED`.

## 2. Architecture

```
src/ps5_monitor/
├── main.py          CLI: run | run --dry-run | test-notify | health | check-config
├── config.py        env settings + retailers YAML, validation with clear errors
├── models.py        Offer, StockStatus, Verdict, Outcome, Decimal price parsing
├── http.py          polite fetcher: URL/redirect validation, robots.txt, retries + backoff
├── evaluate.py      the qualification rules (pure function, heavily tested)
├── monitor.py       orchestration, dedup/notification logic, health summary
├── storage.py       SQLite: offer state, price history (90 days), retailer health
├── notifier.py      Telegram Bot API client (token never logged)
├── logging_config.py JSON logs + secret redaction
└── retailers/       one adapter per retailer (common interface in base.py)
    ├── playstation_direct.py  mediamarkt.py  saturn.py  expert.py   (JSON-LD adapters)
    └── amazon.py                                                    (documented placeholder)
```

Stock states: `IN_STOCK`, `OUT_OF_STOCK`, `UNKNOWN`, `ERROR`. Per-retailer outcomes: `OK`, `NO_DATA` (layout changed),
`BLOCKED` (403/captcha/robots/429, **never bypassed**), `ERROR`, `UNSUPPORTED`.
If **no** retailer returns `OK`, the run is reported as a **monitoring failure** (exit code 1), never as "no offers".

**Notification logic** (per offer, identified by retailer + product id + seller + URL without query):

| Situation | Alert? |
|---|---|
| Qualifies for the first time | yes |
| Qualifies again after being out of stock / too expensive / rejected | yes (restock / dropped below threshold) |
| Already alerted and unchanged | no |
| Already alerted, total fell by ≥ `MIN_PRICE_DROP_EUR` (default €10) below the last alerted total | yes |
| Page glitch (stock/seller/shipping temporarily unverifiable) | no, and state is kept so no duplicate follows |
| Telegram send failed | not marked as sent ⇒ retried next run; workflow turns red |

## 3. Supported retailers and known limitations

| Retailer | Adapter | Status |
|---|---|---|
| PlayStation Direct DE | JSON-LD | **Implemented, needs live validation.** Stock may be rendered client-side or behind a queue; then the result is `NO_DATA`/`BLOCKED`, never in stock. |
| MediaMarkt DE | JSON-LD | **Implemented, needs live validation.** Site has bot protection; blocked ⇒ `BLOCKED`. Marketplace seller names unconfirmed. |
| Saturn DE | JSON-LD | **Implemented, needs live validation.** Same as MediaMarkt. |
| Expert DE | JSON-LD | **Implemented, needs live validation.** Independent stores: every offer stays `UNVERIFIED` until you add the exact seller to `trusted_sellers`. |
| Amazon DE | none | **Placeholder, not implemented.** Amazon's terms generally prohibit scraping; the permitted route is the Product Advertising API (needs an approved Associates account). |

Fixtures in `tests/fixtures/` are **synthetic** pages that follow the schema.org structure; they were **not** captured from live sites.
Other limitations:

* No URL discovery: you paste the exact product URLs (the tool never invents URLs).
* Only pages exposing schema.org JSON-LD work. No headless browser (Playwright) is used.
* GitHub-hosted runners use datacenter IPs; some retailers may refuse them. This is not worked around (no proxies, no identity rotation, no captcha solving).
* Stock "in stock" on a page does not guarantee you can complete the checkout (limits, queues, carts). Always check the link.
* Seller names are matched exactly (case/spacing-insensitive) against a list. Wrong list ⇒ no alert (safe), never a false alert.

## 4. Prerequisites

* Python 3.12+ (3.13 works), `git`, a GitHub account, [GitHub CLI `gh`](https://cli.github.com/) (optional but used below), a Telegram account.

## 5. Create the GitHub repository

```bash
cd ps5-pro-monitor
git init -b main
git add .
git commit -m "Initial commit"
gh auth login
gh repo create ps5-pro-monitor --public --source=. --remote=origin --push
```

**Public or private?** The repository contains no secrets (those live in Actions secrets), and the state database only holds
public retailer data, so **public is fine and gives free Actions minutes** (see section 16). Private works but needs a slower schedule
(see section 16).

## 6. Configure secrets and variables

```bash
gh secret set TELEGRAM_BOT_TOKEN      # paste the token when prompted
gh secret set TELEGRAM_CHAT_ID        # paste the chat id when prompted
gh variable set MAX_PRICE_EUR --body 999.99          # optional, default 999.99
gh variable set HEALTH_SUMMARY --body false          # optional daily health message
```

(or in the web UI: *Settings → Secrets and variables → Actions*).

## 7. Create the Telegram bot

1. In Telegram open **@BotFather** → `/newbot` → choose name and username → copy the **token** (`123456789:AA...`). You may also reuse an existing bot.
2. Open a chat with your bot (or add it to a group) and send any message such as `/start`.
3. Get the **chat id** (run locally; the URL contains your token, so do not paste it anywhere public):
   ```bash
   curl -s "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"
   ```
   Look for `"chat":{"id":123456789,...}`. Group ids are negative (e.g. `-1001234567890`). If `result` is empty, send another message to the bot and retry.

## 8. Enable scheduled monitoring

1. Copy and edit the retailer config, paste the **exact product URLs** and set `enabled: true`:
   ```bash
   cp config/retailers.example.yml config/retailers.yml
   ```
   Product URLs are not secret; commit the file:
   ```bash
   git add config/retailers.yml && git commit -m "Configure retailers" && git push
   ```
2. In the repo go to the **Actions** tab and enable workflows if GitHub asks.
3. Trigger one manual run first (section 10). The schedule (`*/15 * * * *`, every 15 minutes) then runs on its own.

**Do not expect exact intervals.** GitHub schedules are best effort and are often delayed or skipped under load (the minimum cron interval is 5 minutes; this repo uses 15 to stay polite). Alerts can therefore arrive late. For a scarce item this is the main limit of this setup.
Scheduled workflows in **public repos are disabled after 60 days without repository activity**; push a commit or re-enable in the Actions tab if that happens.

## 9. Run locally

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt && pip install -e . --no-deps
cp .env.example .env            # fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
cp config/retailers.example.yml config/retailers.yml    # add URLs, enabled: true
set -a; source .env; set +a      # load the env file (bash/zsh)
python -m ps5_monitor check-config
python -m ps5_monitor run --dry-run     # check + evaluate, nothing sent, nothing saved
python -m ps5_monitor run               # real run (stores state in state/ps5_monitor.db)
python -m pytest -q                     # tests (no network, no real Telegram)
```

Exit codes: `0` ok · `1` monitoring failure (no retailer OK) or Telegram failure · `2` configuration error.

## 10. Trigger a manual check

Web UI: *Actions → Monitor PS5 Pro → Run workflow → mode*. Or:

```bash
gh workflow run monitor.yml -f mode=dry-run   # check only, no notification, no state change
gh workflow run monitor.yml -f mode=run       # real run
gh run list --workflow monitor.yml --limit 5
gh run view "$(gh run list --workflow monitor.yml --limit 1 --json databaseId -q '.[0].databaseId')" --log   # latest run
```

## 11. Test Telegram notifications

```bash
python -m ps5_monitor test-notify             # locally
gh workflow run monitor.yml -f mode=test-notify   # from GitHub (uses your repo secrets)
```

## 12. Add or remove retailers

* **Disable/enable:** set `enabled` in `config/retailers.yml`.
* **Add a retailer:** create `src/ps5_monitor/retailers/<name>.py` with a class extending `JsonLdRetailer` (set `key`, `display_name`, `allowed_hosts`, `default_sellers`), register it in `retailers/__init__.py`, add a fixture and tests, and add it to the config example. For a site without JSON-LD, override `parse()` and return `Offer` objects (leave unknown fields `None`/`UNKNOWN`).
* Check the site's terms and `robots.txt` before adding it.

## 13. Change the price threshold

`MAX_PRICE_EUR` (default `999.99`): GitHub variable (`gh variable set MAX_PRICE_EUR --body 949.99`) or `.env` locally. The rule is `total <= MAX_PRICE_EUR`.
`MIN_PRICE_DROP_EUR` controls re-alerts for already alerted offers (default `10.00`).

## 14. Logs and troubleshooting

Logs are JSON lines (`LOG_FORMAT=text` for readable output) and never contain the token or chat id.
Each run prints a summary such as `PlayStation Direct Germany: OK (1 offers)`.

| Symptom | Meaning / fix |
|---|---|
| `NO_DATA` | Page loaded but no JSON-LD product data: layout changed or content is rendered by JavaScript. Not supported by this adapter. |
| `BLOCKED` | 403, captcha, rate limit or `robots.txt`. The tool stops; it does not circumvent. Try another retailer or a lower frequency. |
| `ERROR` | Network/HTTP failure after 3 attempts with exponential backoff. |
| Run is red: "MONITORING FAILURE" | No retailer returned `OK`. Check the step log. |
| Run is red: Telegram error | Wrong token/chat id, or you never messaged the bot. Run `test-notify`. |
| `configuration error: ...` | Exit code 2; the message says what is missing. |
| Offer visible but no alert | Run `run --dry-run` with `LOG_LEVEL=INFO` and read `reasons` (e.g. `shipping cost unknown`, `seller not an accepted seller`). |

If a failing monitor keeps turning runs red, GitHub e-mails you for each failure; disable the workflow while fixing.

## 15. How persistence works

Runners are ephemeral, so the SQLite file (`ps5_monitor.db`: offer state, price history, retailer health) is stored on an
orphan branch named **`state`**:

1. Before each run, the workflow fetches `state` and restores the database (first run: starts empty).
2. After each run (even a failed one) it creates **one fresh commit** with the database and **force-pushes** it to `state`. History does not grow.
3. The `concurrency` group prevents two runs from overwriting each other.

Limits: the database contains only public retailer data (no secrets). If the `state` branch is deleted, the next run starts fresh and may alert once for an already qualifying offer.
Reset on purpose: `git push origin --delete state`.

## 16. Costs and GitHub Actions limits

* The software and Telegram are free. GitHub Actions on **public** repositories with standard runners is free.
* On **private** repositories the free plan includes a limited monthly minute quota (check GitHub's current billing docs; 2,000 min/month at time of writing). Every run bills at least one minute, so a 5‑minute schedule (~8,600 runs/month) would exhaust it. The default here is `*/15 * * * *` (~2,900 runs/month): free on a public repo, too many for a private one (use `*/30` or slower there).
* Each run is capped at 5 minutes (`timeout-minutes`). HTTP retries are bounded (3 attempts, exponential backoff).

## Security notes

* Secrets only in GitHub Actions secrets / local `.env` (git-ignored). Tokens are redacted in logs and error messages.
* Every URL and every redirect hop is validated (https, allowed host, no credentials) before it is requested.
* Retailer pages are only parsed as data (`json.loads`, HTML parser); nothing from a page is executed.
* Scraping risk: retailers may forbid or block automated access. Keep the frequency low, respect `robots.txt` (`RESPECT_ROBOTS=true`, default), and use this only for personal notification.
* The monitor cannot guarantee that a product can actually be bought; it only reports what the page data says.

## License

MIT, see `LICENSE`.
