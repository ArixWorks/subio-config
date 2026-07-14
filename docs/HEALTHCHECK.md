# SubIO connectivity & functionality checklist

Run these on the **abroad Main VPS** unless marked Iran.

## A) Quick host checks (Main)

```bash
cd /opt/subio/main-server
docker compose ps
curl -s http://127.0.0.1:8000/health/live
curl -s http://127.0.0.1:8000/health/ready
curl -sI https://api.subio.vip/health/live | head -5
```

Expected: containers `Up`/`healthy`, JSON `{"status":"ok"}` / `ready`.

## B) Shared secrets match Iran

```bash
# Main
grep -E '^(INTERNAL_HMAC_KEY|PAYLOAD_ENCRYPTION_KEY)=' /opt/subio/main-server/.env

# Iran
grep -E '^(INTERNAL_HMAC_KEY|PAYLOAD_ENCRYPTION_KEY)=' /opt/subio/iran-tester/.env
```

Values must be **identical**.

## C) Main → Iran tester (HMAC)

```bash
# From Main host
curl -s https://tester.subio.vip/health/live
curl -s https://tester.subio.vip/health/ready

# Admin API probe (replace TOKEN)
TOKEN='YOUR_ADMIN_TOKEN'
curl -s -X POST https://api.subio.vip/admin/communication/probe \
  -H "Authorization: Bearer $TOKEN" | head
```

In admin UI: click **Health Probe** — mode should become/stay `direct` if reachable.

## D) Admin panel save auth

```bash
TOKEN='YOUR_ADMIN_TOKEN'   # same as ADMIN_TOKEN in main .env
curl -s https://api.subio.vip/admin/dashboard -H "Authorization: Bearer $TOKEN"
curl -s -X PUT https://api.subio.vip/admin/scanner-settings \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"npv_to_v2ray":true,"decrypt_bot":true,"protocols":{"vless":true,"vmess":true,"trojan":true,"ss":true,"wireguard":false}}'
```

Browser: paste token → **Load**. Errors now show as red toast (after update).

## E) Public configs pipeline

```bash
docker compose --profile monitoring ps   # monitor must be Up
docker compose logs --tail=50 monitor
docker compose logs --tail=50 worker
docker compose exec -T postgres \
  psql -U subio -d subio -c "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE is_enabled AND score>=50) AS healthy FROM vpn_configs WHERE scope='public';"
```

Empty healthy = normal until new Telegram messages arrive and Iran tests pass.

## F) Iran tester + user sub edge

```bash
# Iran
curl -s http://127.0.0.1:8080/health/live
curl -sI https://subio.vip/sub/REPLACE_TOKEN | head -10
```

## G) Fix stuck update.sh (local dirty files)

```bash
cd /opt/subio
git fetch origin
git reset --hard origin/main   # does NOT delete .env
cd main-server && sudo ./update.sh
```
