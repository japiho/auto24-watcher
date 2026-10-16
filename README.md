# Car watcher (autoportaal, autodiiler, veego, longo, carprof)

A daily GitHub Actions job that runs your used-car searches and reports:

- 🆕 **new listings**
- 💶 **price changes** (old → new, with the difference)
- ❌ **removed listings** (probably sold, with roughly how long they were listed)

A car listed on more than one site is marked *also on …*. Each listing's price history is kept in `state/listings.json`, which the job commits back to the repo every day.

## Sites

| Site | Status | How |
|---|---|---|
| autoportaal.ee | ✅ | Filtered search pages (HTML) |
| autodiiler.ee | ✅ | All listings per make from public pages, filtered locally (its API is off-limits by robots.txt) |
| veego.ee | ✅ | The site's own search API (`api.veego.ee`, allowed by its robots.txt) |
| longo.ee | ✅ | Filtered catalog pages, one query per drive type |
| carprof.ee | ✅ | Catalog page plus the detail page of each candidate |
| auto24.ee | ⛔ | Cloudflare security check blocks scripts. The original auto24 URLs are kept in `searches.json` for reference |
| okidoki.ee | ⛔ | Same Cloudflare block |
| osta.ee | ⛔ | robots.txt blocks AI agents and scripted searches |
| Facebook Marketplace | ⛔ | Needs login; Meta's terms forbid scraping |

The scraper waits between requests and runs once a day.

**Drive type (veoskeem)** is shown on every row. Where a listing doesn't say, the watcher checks the car's own page, then the same car on another site (same year and exact mileage). If it's still unknown, the row shows *veoskeem ?* instead of the car being dropped.

## AI overview: top 3 picks

Each report starts with a short **Top 3 picks** section written by Claude (`claude-haiku-5-5`, the same model as nexara). It covers why each car is good value, its running and repair costs, and what to check before buying. It's based on the listing data and general knowledge of each engine and gearbox, not an inspection.

Setup: add the repository secret `ANTHROPIC_API_KEY` (the same key nexara uses). Without it, the overview is skipped and everything else works as before. Cost is a fraction of a cent per day.

## Getting the report

- **GitHub issue (default):** when something changes, the job opens an issue titled `Autod DD.MM.YYYY`. GitHub emails you, and it shows up in the GitHub mobile app. Days with no changes produce no issue.
- **Email:** the report as a formatted email, with the subject summarising the day (e.g. `Autod 17.10.2026: 3 new, 2 price changes`). It's sent through the same mail server as the nexara app: veebimajutus, sent as `jakob@kergeraskus.ee`, port 465 SSL. Setup:
  - Add the repository secret `SMTP_PASSWORD` (the jakob@kergeraskus.ee mailbox password) under *Settings → Secrets and variables → Actions*.
  - Recipients default to jakob.pihol@gmail.com. To change them, add a repository **variable** `EMAIL_TO` (comma-separated).
- **Telegram (optional):** add repository secrets `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `TELEGRAM_CHAT_ID` (send your bot a message, then read `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`).

If one site fails on a given day, the report says so, and that site's cars are **not** marked as sold.

## Changing the searches

Edit `searches.json`. Each search is defined once and runs on every site:

- `make`: one of Mercedes-Benz, BMW, Skoda, Subaru, Volkswagen. Other makes need their site IDs added in `sources.py`.
- `model_regex`: matched against the model part of the title. For example, `^C(\b|\d)` matches C 180/220/C-Class but not CLA/CLS.
- `year_min`, `price_max`, `power_min` (kW), `mileage_max`
- `bodies`: universaal, mahtuniversaal, sedaan, luukpära, maastur
- `drive`: nelikvedu / esivedu / tagavedu / null (any)
- `veego_terms`: free-text terms for veego's search

For a test run, use *Actions → car watcher → Run workflow* and tick **dry run**. It emails a `[test]` preview (including the AI overview) but posts no issue and saves no state. The log is saved to the `debug` branch.

## Schedule and manual runs

The job runs at 04:47 UTC, which is about 07:47 in Tallinn in summer and 06:47 in winter. GitHub may start it 5–30 minutes late. To run it by hand: *Actions → car watcher → Run workflow*.

GitHub pauses scheduled workflows in repos with no activity for 60 days. The daily state commit counts as activity, but only on days when something changed. If the job stops, re-enable it in the Actions tab.

## When it breaks

If every site fails, the job fails and GitHub emails you. The log and the fetched pages are pushed to the `debug` branch so the parser can be fixed.

## Run locally

```bash
pip install -r requirements.txt
python watcher.py
cat report.md
```
