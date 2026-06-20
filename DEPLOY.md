# Deploying TrackBox to an Ubuntu VPS

FastAPI + uvicorn, behind Nginx, managed by systemd. The default `scrape` mode
drives a **headless Chromium**, so the server needs the browser and its system
libraries (steps 4–5). Templates live in [`deploy/`](deploy/).

> **⚠️ Read this first — datacenter IP + Cloudflare.**
> `scrape` mode works by clearing 17track's Cloudflare in a real browser. From a
> **datacenter/VPS IP**, Cloudflare challenges much more aggressively than from a
> home connection, so scraping may be blocked more often (it degrades to demo
> data automatically). For **reliable production data**, use the free official
> API instead: set `PROVIDER_MODE=auto` and add a free `SEVENTEENTRACK_API_KEY`
> (100 free lookups, from https://api.17track.net). You can run both — `auto`
> with a key is the dependable path.

Assumes Ubuntu 22.04 / 24.04 and a sudo user. **≥1 GB RAM** (2 GB recommended —
headless Chromium is memory hungry).

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

## 4. Install the headless browser system libraries (root)
```bash
sudo /opt/trackbox/.venv/bin/playwright install-deps
```

## 5. Download Chromium **as the app user** (so it lands in its HOME)
```bash
sudo -u trackbox HOME=/opt/trackbox /opt/trackbox/.venv/bin/playwright install chromium
```

## 6. Configure `.env`
```bash
sudo -u trackbox cp /opt/trackbox/.env.example /opt/trackbox/.env
sudo -u trackbox nano /opt/trackbox/.env
```
Set these for a Linux server (no Edge here → use bundled Chromium):
```ini
PROVIDER_MODE=scrape
SCRAPE_BROWSER_CHANNEL=        # empty = use the Chromium installed in step 5
SCRAPE_HEADLESS=true
SCRAPE_FALLBACK_MOCK=false     # real data or honest "not found" — never fake data
ENABLE_DOCS=false             # keep the API docs hidden from users
INCLUDE_RAW=false
APP_PORT=8080                  # internal uvicorn port — change if 8080 is taken

# Captcha/Cloudflare solver — used only when the browser can't clear Cloudflare.
SOLVER_PROVIDER=twocaptcha
SOLVER_API_KEY=your_2captcha_key
SOLVER_TIMEOUT=180
# If solving still fails from the VPS (token bound to a different IP), add a
# residential proxy so the browser itself clears Cloudflare:
# SCRAPE_PROXY=http://user:pass@host:port
```

> **About the solver.** A real browser clears most Cloudflare challenges on its
> own; the solver only kicks in when it can't. The solved token is generated on
> 2Captcha's IP (proxyless), while Cloudflare often binds clearance to *your*
> server's IP — so it helps but isn't guaranteed from a datacenter VPS. The
> reliable fix on a VPS is a residential proxy ↓ (then the browser clears
> Cloudflare directly and the solver is rarely needed).

### Residential proxy (the reliable way to scrape from a VPS)

A datacenter/VPS IP gets challenged hard by Cloudflare. Routing the browser
through a **residential proxy** makes it look like a home connection.

1. Get a residential proxy from a provider (e.g. IPRoyal, Smartproxy, Oxylabs,
   Bright Data). **Use a "sticky"/"session" endpoint** — the IP must stay the
   same for the life of the browser, or the cleared Cloudflare cookie keeps
   getting invalidated. They give you a URL like `http://user:pass@gateway:port`.
2. Put it in `/opt/trackbox/.env` (or `/root/workspace/TrackBox/.env`):
   ```ini
   SCRAPE_PROXY=http://user:pass@gateway:port
   ```
   URL-encode any special characters in the username/password (e.g. `@` → `%40`).
3. `systemctl restart trackbox`, then watch a lookup:
   ```bash
   journalctl -u trackbox -f
   # "scrape: using proxy http://gateway:port"
   # "scrape: captured matching tracking response for <num>"  ← real data 🎉
   ```

If it works, the proxy (not the solver) is doing the heavy lifting; you can keep
`SOLVER_*` set as a backup. If it still fails, the proxy is likely rotating per
request — switch to a sticky/session IP from your provider.

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
| `journalctl` shows *"browser launch failed"* | Re-run steps 4 & 5; confirm `SCRAPE_BROWSER_CHANNEL=` is empty. |
| Browser deps error (`libnss3` etc.) | `sudo /opt/trackbox/.venv/bin/playwright install-deps`. |
| Everything returns "not found" | Cloudflare is blocking the VPS IP so the scrape never completes. Add a residential `SCRAPE_PROXY`, or use `PROVIDER_MODE=auto` + a free key. Check `journalctl -u trackbox` for "no tracking response …". |
| Showed *wrong* parcel's data | Fixed: the scraper now verifies the captured response matches the requested number (ignores 17track's default sample / stale responses). Make sure the deployed code is up to date (`git pull` + restart). |
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
