# Research Agent Telegram Bot

Personal Telegram bot that runs research and answers questions from your paper library.

## Local Setup

```bash
cd research-agent-telegram-bot
npm install
cp .env.example .env
# Edit .env: add TELEGRAM_BOT_TOKEN and AUTHORIZED_USER_ID
npm start
```

## Commands

| Command | Description |
|---------|-------------|
| `/research <topic>` | Run full research (15-45 min) |
| `/qa <question>` | Query your paper library |
| `/status` | Check if research is running |
| `/cancel` | Stop current research |
| Just send text | Treated as research topic |

---

## Oracle Cloud Free Tier Deployment

Always-free ARM VM (4 CPUs, 24GB RAM) for 24/7 operation.

### 1. Create Oracle Cloud Account

1. Sign up at [cloud.oracle.com](https://cloud.oracle.com)
2. Wait for activation (1-24 hours)

### 2. Create VM

1. **Compute → Instances → Create Instance**
2. **Shape**: Ampere VM.Standard.A1.Flex (4 OCPUs, 24GB RAM)
3. **Image**: Ubuntu 22.04
4. Add your SSH public key
5. Create instance

### 3. Connect & Install

```bash
ssh ubuntu@<instance-ip>

# Install Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Install Python 3.11 + tools
sudo apt install -y python3.11 python3.11-venv python3-pip git

# Install Typst (ARM)
wget https://github.com/typst/typst/releases/download/v0.12.0/typst-aarch64-unknown-linux-musl.tar.xz
tar -xf typst-aarch64-unknown-linux-musl.tar.xz
sudo mv typst-aarch64-unknown-linux-musl/typst /usr/local/bin/
```

### 4. Clone Research CLI

```bash
cd ~
git clone https://github.com/gbrlpzz/research-agent-cli.git
cd research-agent-cli
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Add API keys
```

### 5. Setup Telegram Bot

```bash
cd ~/research-agent-cli/research-agent-telegram-bot
npm install
nano .env  # Add token and user ID
```

### 6. Sync Library with Syncthing

To keep your local library synced with the server:

**On your Mac:**
```bash
brew install syncthing
syncthing  # Opens localhost:8384
```

**On Oracle Cloud:**
```bash
sudo apt install syncthing
syncthing &
```

Then pair devices and sync the `library/` folder.

### 7. Run as Service

```bash
sudo nano /etc/systemd/system/research-bot.service
```

```ini
[Unit]
Description=Research Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/research-agent-cli/research-agent-telegram-bot
ExecStart=/usr/bin/node bot.js
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable research-bot
sudo systemctl start research-bot

# Check status
sudo systemctl status research-bot
journalctl -u research-bot -f
```

---

## Security

- `AUTHORIZED_USER_ID` restricts bot to only your Telegram account
- Never commit `.env` to git
