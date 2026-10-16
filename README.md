# auto24 watcher

A daily GitHub Actions job that checks auto24.ee searches and reports:

- 🆕 **new listings**
- 💶 **price changes** (old → new, with the difference)
- ❌ **removed listings** (probably sold, with roughly how long they were listed)

Each listing's price history is kept in `state/listings.json`, which the job commits back to the repo every day.

> **Where it runs:** auto24 blocks GitHub's servers with a Cloudflare security check, so the daily run happens on an always-on Android phone via Termux. See **[PHONE_SETUP.md](PHONE_SETUP.md)**. The GitHub Actions workflow is kept for manual runs only. Below, "the job" means the phone run.

## How you get the report

- **GitHub issue (default, no setup):** when something changes, the job opens an issue titled `auto24 DD.MM.YYYY`. GitHub emails you about it and it shows up in the GitHub mobile app. Days with no changes produce no issue.
- **Telegram (optional):** add two repository secrets under *Settings → Secrets and variables → Actions*:
  - `TELEGRAM_BOT_TOKEN`: create a bot with [@BotFather](https://t.me/BotFather)
  - `TELEGRAM_CHAT_ID`: send your bot a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`

## Changing what is watched

Edit `searches.json`. Set up the search on auto24.ee in your browser and paste the resulting URL. You can add several searches; their results are merged by listing ID.

Tip: keep `ae=1` (newest first) and `af=100` (100 per page). The script also follows pagination if a search grows beyond one page.

## Schedule

`.github/workflows/watch.yml` runs at 04:47 UTC, which is about 07:47 in Tallinn in summer and 06:47 in winter. GitHub's scheduled runs can start 5–30 minutes late. You can also start a run by hand: *Actions → auto24 watcher → Run workflow*.

Note: GitHub pauses scheduled workflows in a repo with no activity for 60 days. The daily state commit counts as activity, but only on days when something changed. If the job ever stops, re-enable it in the Actions tab.

## When it breaks

The job fails, and GitHub emails you, if auto24 reports results but none can be parsed. It also fails if the listing count suddenly halves, so a site redesign or a block doesn't make every car look "sold". After a failure, the raw HTML is uploaded as a `debug-html` artifact on the failed run, so the parser can be fixed.

## Run locally

```bash
pip install -r requirements.txt
python watcher.py
cat report.md
```
