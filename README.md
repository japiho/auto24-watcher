# Car watcher (autoportaal.ee + autodiiler.ee)

A daily GitHub Actions job that runs your used-car searches and reports:

- 🆕 **new listings**
- 💶 **price changes** (old → new, with the difference)
- ❌ **removed listings** (probably sold, with roughly how long they were listed)

A car listed on more than one site is marked *also on …*. Each listing's price history is kept in `state/listings.json`, which the job commits back to the repo every day.

## Sites

| Site | Status | How |
|---|---|---|
| autoportaal.ee | ✅ | Filters go in the search URL; results are read from the HTML |
| autodiiler.ee | ✅ | Reads all listings for the make from the public search pages, then filters locally (model, body, year, price, kW, km) |
| auto24.ee | ⛔ disabled | Blocks scripts with a Cloudflare security check, even from home connections. Set `"enabled": true` in `searches.json` to retry. |
| Facebook Marketplace | ✖ not supported | Needs login; Meta's terms forbid scraping |

The scraper only reads pages that the sites' robots.txt allows, waits 2 seconds between pages and runs once a day.

## Getting the report

- **GitHub issue (default):** when something changes, the job opens an issue titled `Autod DD.MM.YYYY`. GitHub emails you, and it shows up in the GitHub mobile app. Days with no changes produce no issue.
- **Telegram (optional):** add repository secrets `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `TELEGRAM_CHAT_ID` (send your bot a message, then read `chat.id` from `https://api.telegram.org/bot<TOKEN>/getUpdates`).

If one site fails on a given day, the report says so, and that site's cars are **not** marked as sold.

## Changing the search

Edit `searches.json`:

- **autoportaal:** set up the search on autoportaal.ee/et/kuulutused and paste the URL. Keep `sort=id_desc` (newest first).
- **autodiiler:** the URL selects the make (`vt=1` = cars, `ba=18` = Mercedes-Benz). Model, body, year, price, power and mileage go in the `filter` block. `model_prefixes: ["C "]` matches C 180/200/220/300/43 AMG… but not CLA/CLS/CLK.

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
