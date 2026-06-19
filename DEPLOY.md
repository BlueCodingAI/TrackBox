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
ENABLE_DOCS=false              # keep the API docs hidden from users
INCLUDE_RAW=false
# For reliable data (recommended for production), instead use:
# PROVIDER_MODE=auto
# SEVENTEENTRACK_API_KEY=your_free_key
```

## 7. Quick smoke test
```bash
cd /opt/trackbox
sudo -u trackbox HOME=/opt/trackbox .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
sleep 3
curl -s localhost:8000/api/health        # -> {"status":"ok"}
curl -s "localhost:8000/api/track?number=00340434498968565356&carrier=7041" | head -c 300
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
| Everything returns demo data | Cloudflare is blocking the VPS IP — switch to `PROVIDER_MODE=auto` + a free key. |
| 502 Bad Gateway | Service not running: `sudo systemctl status trackbox`, check logs. |
| Lookups time out at proxy | Raise `proxy_read_timeout` in the Nginx config. |
| High memory / OOM | Keep `--workers 1`; ensure ≥1 GB RAM; the unit caps at `MemoryMax=1500M`. |
