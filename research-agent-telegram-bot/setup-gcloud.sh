#!/bin/bash
# Google Cloud Auto-Setup Script for Research Agent Telegram Bot
# Run on a fresh e2-micro Ubuntu 22.04 instance

set -e

echo "═══════════════════════════════════════════════════════════════"
echo "  Research Agent Telegram Bot - Google Cloud Setup"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Configuration
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
AUTHORIZED_USER_ID="${AUTHORIZED_USER_ID:-}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$GEMINI_API_KEY" ]; then
    echo "❌ Missing required environment variables"
    echo ""
    echo "Usage:"
    echo "  TELEGRAM_BOT_TOKEN=xxx GEMINI_API_KEY=xxx AUTHORIZED_USER_ID=xxx ./setup-gcloud.sh"
    exit 1
fi

echo "✅ Configuration received"

# Update system
echo "📦 Updating system..."
sudo apt update && sudo apt upgrade -y

# Install Node.js 20
echo "📦 Installing Node.js..."
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# Install Python 3.11
echo "📦 Installing Python..."
sudo apt install -y python3.11 python3.11-venv python3-pip git wget

# Install Typst (x86)
echo "📦 Installing Typst..."
wget -q https://github.com/typst/typst/releases/download/v0.12.0/typst-x86_64-unknown-linux-musl.tar.xz
tar -xf typst-x86_64-unknown-linux-musl.tar.xz
sudo mv typst-x86_64-unknown-linux-musl/typst /usr/local/bin/
rm -rf typst-*

# Clone repo
echo "📥 Cloning repository..."
cd ~
git clone https://github.com/gbrlpzz/research-agent-cli.git || (cd research-agent-cli && git pull)
cd research-agent-cli

# Python setup
echo "🐍 Setting up Python..."
python3.11 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Configure CLI
cat > .env << EOF
GEMINI_API_KEY=${GEMINI_API_KEY}
RESEARCH_REASONING_MODEL=gemini/gemini-2.5-flash
RESEARCH_RAG_MODEL=gemini/gemini-2.5-flash
EOF

# Setup Telegram bot
echo "🤖 Setting up bot..."
cd research-agent-telegram-bot
npm install --silent

cat > .env << EOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
RESEARCH_CLI_PATH=/home/${USER}/research-agent-cli
AUTHORIZED_USER_ID=${AUTHORIZED_USER_ID}
EOF

# Systemd service
echo "🔧 Creating service..."
sudo tee /etc/systemd/system/research-bot.service > /dev/null << EOF
[Unit]
Description=Research Telegram Bot
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=/home/${USER}/research-agent-cli/research-agent-telegram-bot
ExecStart=/usr/bin/node bot.js
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable research-bot
sudo systemctl start research-bot

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Setup Complete!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Bot is running. Check: sudo systemctl status research-bot"
echo ""
