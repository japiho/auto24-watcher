# Running the watcher on an Android phone (Termux)

auto24 blocks GitHub's servers with a Cloudflare check, so the daily scrape runs on an always-on Android phone instead. The phone fetches the page over its own connection, commits the snapshot to this repo and opens the report as a GitHub issue, just as the Actions job would.

## 1. Install the apps (from F-Droid, not Google Play)

The Play Store builds of Termux are outdated and break. Install [F-Droid](https://f-droid.org), then from it install:

- **Termux**
- **Termux:API**: lets the script hold a wake lock during the run
- **Termux:Boot**: restarts the scheduler after a reboot

Open Termux:Boot once after installing, so Android registers it.

## 2. Stop Samsung from killing Termux

Without this, One UI will put Termux to sleep and the morning run won't happen.

- *Settings → Apps → Termux → Battery* → **Unrestricted**. Do the same for Termux:API and Termux:Boot.
- *Settings → Battery → Background usage limits* → add Termux to **Never sleeping apps**.

## 3. Install packages and clone the repo

In Termux:

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git cronie termux-services termux-api
pip install requests beautifulsoup4
git clone https://github.com/japiho/auto24-watcher ~/auto24-watcher
```

## 4. Test that auto24 lets the phone through (important!)

```bash
cd ~/auto24-watcher && python watcher.py
```

You want to see `parsed 38 listings (site says 38)` (or whatever the current count is). If you get `403 Forbidden`, the phone's connection is blocked as well. Stop here: this route won't work.

This test run writes a baseline. Delete it so the first real run posts the full list as an issue:

```bash
rm -f state/listings.json report.md
```

## 5. GitHub access token

On GitHub, go to *Settings → Developer settings → Fine-grained tokens → Generate new token*:

- Repository access: **Only select repositories → auto24-watcher**
- Permissions: **Contents: Read and write**, **Issues: Read and write**
- Expiration: up to 1 year (put a reminder in your calendar to renew it)

Then on the phone:

```bash
cat > ~/.auto24.env <<'EOF'
GITHUB_TOKEN=github_pat_PASTE_YOURS_HERE
GITHUB_REPO=japiho/auto24-watcher
EOF
chmod 600 ~/.auto24.env

cd ~/auto24-watcher
git config user.name "auto24-phone"
git config user.email "auto24-phone@users.noreply.github.com"
source ~/.auto24.env
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
chmod +x run_phone.sh
```

Do one full run by hand:

```bash
./run_phone.sh; tail -20 ~/auto24-watcher.log
```

A "First run — baseline saved" issue should appear in the repo, listing every car.

## 6. Schedule it daily

```bash
sv-enable crond
crontab -e
```

Add this line (the phone uses local Tallinn time, so 07:45 means 07:45):

```
45 7 * * * $HOME/auto24-watcher/run_phone.sh
```

So the scheduler comes back after a reboot:

```bash
mkdir -p ~/.termux/boot
printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nsv-enable crond\n' > ~/.termux/boot/start-cron
chmod +x ~/.termux/boot/start-cron
```

## Day to day

- **Reports** arrive as GitHub issues titled `auto24 DD.MM.YYYY`, only on days something changed.
- **If a run fails**, you get an issue titled `auto24 FAILED …` with the last log lines.
- **Full log:** `~/auto24-watcher.log` on the phone.
- **To change the search**, edit `searches.json` on GitHub. The phone pulls the latest version before every run.
- **If reports stop arriving with no failure issue**, the phone probably killed Termux or crond. Re-check step 2 and run `sv status crond`.
