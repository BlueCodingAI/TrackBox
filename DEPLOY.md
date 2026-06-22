# Deploying TrackBox to an Ubuntu VPS

FastAPI + uvicorn, behind Nginx, managed by systemd. The default `scrape` mode
drives a **real headless browser** against parcelsapp.com, so the server needs a
browser and its system libraries (steps 4–5). Templates live in [`deploy/`](deploy/).

> **⚠️ Read this first — you need a real browser channel (Chrome/Edge).**
> parcelsapp **detects bundled headless Chromium** (the `sec-ch-ua` brand leaks
> `"HeadlessChrome"`) and returns `{"error":"NO_DATA"}`. You must install **Google
> Chrome** (or Edge) on the server and set `SCRAPE_BROWSER_CHANNEL=chrome` — see
> steps 4–6. From a **datacenter/VPS IP** parcelsapp may also rate-limit; a
> residential `SCRAPE_PROXY` makes it reliable. For a fully dependable path you
> can instead use the free official API: `PROVIDER_MODE=auto` +
> `SEVENTEENTRACK_API_KEY` (100 free lookups, https://api.17track.net).

Assumes Ubuntu 22.04 / 24.04 and a sudo user. **≥1 GB RAM** (2 GB recommended —
a headless browser is memory hungry).

---

## 1. System packages
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git nginx
```

## 2. Create an app user + get the code
```bash
sudo adduser --system --group --home /opt/trackbox trackbox
```
Copy your project into `/opt/trackbox`. Easiest options:

- **git** (recommended): push the project to a private repo, then
  `sudo -u trackbox git clone <repo-url> /opt/trackbox`.
- **scp/rsync from your machine** (run locally, from the project folder):
  ```bash
  rsync -avz --exclude .venv --exclude __pycache__ ./ user@SERVER_IP:/tmp/trackbox/
  ssh user@SERVER_IP "sudo cp -r /tmp/trackbox/. /opt/trackbox/ && sudo chown -R trackbox:trackbox /opt/trackbox"
  ```

## 3. Python venv + dependencies
```bash
cd /opt/trackbox
sudo -u trackbox python3 -m venv .venv
sudo -u trackbox .venv/bin/pip install --upgrade pip
sudo -u trackbox .venv/bin/pip install -r requirements.txt
```

## 4. Install browser system libraries (root)
```bash
sudo /opt/trackbox/.venv/bin/playwright install-deps
```

## 5. Install **Google Chrome** as the app user (required — see the warning above)
parcelsapp blocks bundled headless Chromium, so install the real Chrome channel:
```bash
sudo -u trackbox HOME=/opt/trackbox /opt/trackbox/.venv/bin/playwright install chrome
```
(If `install chrome` is unavailable on your distro, install Google Chrome via apt
— `google-chrome-stable` — and Playwright's `channel=chrome` will find it.)

## 6. Configure `.env`
```bash
sudo -u trackbox cp /opt/trackbox/.env.example /opt/trackbox/.env
sudo -u trackbox nano /opt/trackbox/.env
```
Set these for a Linux server (use the real Chrome channel from step 5):
```ini
PROVIDER_MODE=scrape
SCRAPE_BROWSER_CHANNEL=chrome  # real Chrome — bundled Chromium is blocked by parcelsapp
SCRAPE_HEADLESS=true
SCRAPE_FALLBACK_MOCK=false     # real data or honest "not found" — never fake data
ENABLE_DOCS=false             # keep the API docs hidden from users
INCLUDE_RAW=false
APP_PORT=8080                  # internal uvicorn port — change if 8080 is taken

# Recommended on a VPS — parcelsapp may rate-limit datacenter IPs:
# SCRAPE_PROXY=http://user:pass@host:port
```

> **Why a real browser channel?** parcelsapp inspects the `sec-ch-ua` client-hint
> brand; bundled headless Chromium reports `"HeadlessChrome"` and is served no
> data. The Chrome/Edge channel reports a genuine brand even when headless, which
> is what returns live results.

### Residential proxy (recommended from a VPS)

A datacenter/VPS IP can get rate-limited or blocked by parcelsapp. Routing the
browser through a **residential proxy** makes it look like a home connection.

1. Get a residential proxy from a provider (e.g. IPRoyal, Smartproxy, Oxylabs,
   Bright Data). A **"sticky"/"session" endpoint** is preferable so the IP stays
   stable for the life of the browser. They give you a URL like
   `http://user:pass@gateway:port`.
2. Put it in `/opt/trackbox/.env`:
   ```ini
   SCRAPE_PROXY=http://user:pass@gateway:port
   ```
   URL-encode any special characters in the username/password (e.g. `@` → `%40`).
3. `systemctl restart trackbox`, then watch a lookup:
   ```bash
   journalctl -u trackbox -f
   # "scrape: using proxy http://gateway:port"
   # "scrape: captured parcelsapp response for <num>"  ← real data 🎉
   ```

If it still fails, the proxy is likely rotating per request — switch to a
sticky/session IP from your provider.

> **Ports.** `APP_PORT` is the *internal* port the app listens on; Nginx proxies
> to it, so end users never see it (they hit ports 80/443). If you change
> `APP_PORT`, update `proxy_pass` in `deploy/nginx.conf` to match. To expose the
> app on a **custom public port** instead, see "Choosing the port" at the end.

## 7. Quick smoke test
```bash
cd /opt/trackbox
sudo -u trackbox HOME=/opt/trackbox .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080 &
sleep 3
curl -s localhost:8080/api/health        # -> {"status":"ok"}
curl -s "localhost:8080/api/track?number=00340434498968565356&carrier=7041" | head -c 300
kill %1
```

## 8. Run it as a service (systemd)
```bash
sudo cp /opt/trackbox/deploy/trackbox.service /etc/systemd/system/trackbox.service
sudo systemctl daemon-reload
sudo systemctl enable --now trackbox
sudo systemctl status trackbox          # should be "active (running)"
sudo journalctl -u trackbox -f          # live logs
```
> Keep `--workers 1` (in the unit file): each worker launches its own browser, so
> more workers = much more RAM. Bump to 2 only on a larger box.

## 9. Nginx reverse proxy
```bash
sudo cp /opt/trackbox/deploy/nginx.conf /etc/nginx/sites-available/trackbox
sudo nano /etc/nginx/sites-available/trackbox   # set server_name to your domain
sudo ln -s /etc/nginx/sites-available/trackbox /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 10. HTTPS (recommended, needs a domain pointing at the VPS)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## 11. Firewall
```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

Visit `http://your-domain.com` (or `https://…` after step 10). 🎉

---

## Updating after a code change
```bash
cd /opt/trackbox
sudo -u trackbox git pull        # or rsync again
sudo -u trackbox .venv/bin/pip install -r requirements.txt   # if deps changed
sudo systemctl restart trackbox
```

## Troubleshooting
| Symptom | Fix |
|---|---|
| `journalctl` shows *"browser launch failed"* | Re-run steps 4 & 5; confirm `SCRAPE_BROWSER_CHANNEL=chrome` and that Google Chrome is installed for the `trackbox` user. |
| Browser deps error (`libnss3` etc.) | `sudo /opt/trackbox/.venv/bin/playwright install-deps`. |
| Everything returns "not found" | Most often the browser channel: bundled Chromium is blocked by parcelsapp (`NO_DATA`). Install Chrome and set `SCRAPE_BROWSER_CHANNEL=chrome`. If still blocked, the VPS IP is rate-limited — add a residential `SCRAPE_PROXY`, or use `PROVIDER_MODE=auto` + a free key. Check `journalctl -u trackbox` for "no parcelsapp API response …". |
| Right courier, wrong/empty data | parcelsapp can guess the wrong origin country for ambiguous numbers and return no data; this is an upstream limitation. Try the official API path for that number. |
| 502 Bad Gateway | Service not running: `sudo systemctl status trackbox`, check logs. |
| Lookups time out at proxy | Raise `proxy_read_timeout` in the Nginx config. |
| High memory / OOM | Keep `--workers 1`; ensure ≥1 GB RAM; the unit caps at `MemoryMax=1500M`. |

---

## Choosing the port

There are two layers. Pick the scenario that matches your constraint:

### A. 8080 internal port is taken (most common) — keep Nginx on 80/443
Just change the internal port; users still hit the normal `http(s)://your-domain`.
1. Set `APP_PORT=9001` (or any free port) in `/opt/trackbox/.env`.
2. Set `proxy_pass http://127.0.0.1:9001;` in `deploy/nginx.conf`.
3. `sudo systemctl restart trackbox && sudo systemctl reload nginx`.

### B. Serve on a custom **public** port via Nginx (e.g. `http://your-server:8090`)
Keep the app internal; make Nginx listen on the public port.
1. In the Nginx config change `listen 80;` → `listen 8090;`.
2. Open it: `sudo ufw allow 8090/tcp`.
3. `sudo nginx -t && sudo systemctl reload nginx`. Visit `http://SERVER_IP:8090`.

### C. No Nginx — run the app directly on a public port
Simplest, but no HTTPS unless you add it yourself.
1. In `deploy/trackbox.service`, change the host so it's reachable:
   `--host 0.0.0.0 --port ${APP_PORT}` and set `APP_PORT=8090` in `.env`.
2. Open it: `sudo ufw allow 8090/tcp`.
3. `sudo systemctl daemon-reload && sudo systemctl restart trackbox`.
   Visit `http://SERVER_IP:8090`. (Skip the Nginx steps 9–10.)

> Whichever public port you expose, open it in the firewall (`ufw allow …`) and,
> if your VPS provider has its own network firewall/security group, allow it there
> too.
