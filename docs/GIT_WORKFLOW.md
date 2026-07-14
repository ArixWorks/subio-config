# GitHub workflow — SubIO

Repo: https://github.com/ArixWorks/subio-config

## What is committed vs secret

| Committed | Never commit (local only) |
|-----------|---------------------------|
| Source code, Dockerfiles, `install.sh`, `update.sh` | `.env` on both servers |
| `.env.example` | Bot token, HMAC, payload key, S3 secrets |
| Docs | Telethon session strings |

## One-time: push full project from Windows (Cursor)

In PowerShell (project root):

```powershell
cd F:\Subio\telegram-config-subio\project
git init
git add .
git status   # confirm .env is NOT listed
git commit -m "Initial SubIO platform (main + iran-tester)"
git branch -M main
git remote add origin https://github.com/ArixWorks/subio-config.git
# overwrite the empty README-only first commit:
git push -u origin main --force
```

Or with GitHub CLI: `gh auth login` then the same push.

## One-time: link each VPS to the repo (keep .env)

### Iran

```bash
# backup current folder + keep .env
cp ~/iran-tester/.env /root/iran-tester.env.bak
cd ~
mv iran-tester iran-tester.old
git clone https://github.com/ArixWorks/subio-config.git /opt/subio
cp /root/iran-tester.env.bak /opt/subio/iran-tester/.env
sed -i 's/\r$//' /opt/subio/iran-tester/.env
cd /opt/subio/iran-tester
chmod +x install.sh update.sh
sudo ./update.sh
```

### Abroad (Main)

```bash
cp ~/main-server/.env /root/main-server.env.bak
cd ~
mv main-server main-server.old
# if not cloned yet:
git clone https://github.com/ArixWorks/subio-config.git /opt/subio
# if Iran already cloned /opt/subio, reuse it:
cp /root/main-server.env.bak /opt/subio/main-server/.env
sed -i 's/\r$//' /opt/subio/main-server/.env
cd /opt/subio/main-server
chmod +x install.sh update.sh
sudo ./update.sh
```

## Daily workflow (what you asked for)

1. Edit code in Cursor (Windows)
2. Commit + push:

```powershell
cd F:\Subio\telegram-config-subio\project
git add .
git commit -m "describe the fix"
git push
```

3. On each server, **one command**:

```bash
# Iran
cd /opt/subio/iran-tester && sudo ./update.sh

# Abroad
cd /opt/subio/main-server && sudo ./update.sh
```

That pulls from GitHub, rebuilds Docker images, restarts services, keeps `.env` untouched.
