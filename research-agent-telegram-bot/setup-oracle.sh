#!/bin/bash
# Oracle Cloud Auto-Setup Script for Research Agent Telegram Bot
# Run this on a fresh Ubuntu 22.04 ARM instance

set -e

echo "═══════════════════════════════════════════════════════════════"
echo "  Research Agent Telegram Bot - Oracle Cloud Setup"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# Configuration (edit these before running)
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
AUTHORIZED_USER_ID="${AUTHORIZED_USER_ID:-}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"

# Check required env vars
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN not set"
    echo ""
    echo "Run with:"
    echo "  TELEGRAM_BOT_TOKEN=your_token GEMINI_API_KEY=your_key AUTHORIZED_USER_ID=your_id ./setup-oracle.sh"
    exit 1
fi

if [ -z "$GEMINI_API_KEY" ]; then
    echo "❌ GEMINI_API_KEY not set"
    exit 1
fi

echo "✅ Configuration received"
echo ""

# Update system
echo "📦 Updating system..."
sudo apt update && sudo apt upgrade -y

# Install Node.js 20
echo "📦 Installing Node.js 20..."
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt install -y nodejs
fi
echo "   Node.js $(node -v)"

# Install Python 3.11
echo "📦 Installing Python 3.11..."
sudo apt install -y python3.11 python3.11-venv python3-pip git

# Install Typst (ARM)
echo "📦 Installing Typst..."
if ! command -v typst &> /dev/null; then
    wget -q https://github.com/typst/typst/releases/download/v0.12.0/typst-aarch64-unknown-linux-musl.tar.xz
    tar -xf typst-aarch64-unknown-linux-musl.tar.xz
    sudo mv typst-aarch64-unknown-linux-musl/typst /usr/local/bin/
    rm -rf typst-*
fi
echo "   Typst $(typst --version)"

# Clone research-agent-cli
echo ""
echo "📥 Cloning research-agent-cli..."
cd ~
if [ -d "research-agent-cli" ]; then
    cd research-agent-cli
    git pull
else
    git clone https://github.com/gbrlpzz/research-agent-cli.git
    cd research-agent-cli
fi

# Setup Python venv
echo ""
echo "🐍 Setting up Python environment..."
python3.11 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Configure research-agent-cli
echo ""
echo "⚙️  Configuring research-agent-cli..."
cat > .env << EOF
GEMINI_API_KEY=${GEMINI_API_KEY}
RESEARCH_REASONING_MODEL=gemini/gemini-2.5-flash
RESEARCH_RAG_MODEL=gemini/gemini-2.5-flash
RESEARCH_EMBEDDING_MODEL=openai/text-embedding-3-large
EOF

# Setup Telegram bot
echo ""
echo "🤖 Setting up Telegram bot..."
cd research-agent-telegram-bot
npm install --silent

cat > .env << EOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
RESEARCH_CLI_PATH=/home/ubuntu/research-agent-cli
AUTHORIZED_USER_ID=${AUTHORIZED_USER_ID}
EOF

# Create systemd service
echo ""
echo "🔧 Creating systemd service..."
sudo tee /etc/systemd/system/research-bot.service > /dev/null << EOF
[Unit]
Description=Research Agent Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/research-agent-cli/research-agent-telegram-bot
ExecStart=/usr/bin/node bot.js
Restart=on-failure
RestartSec=10
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable research-bot
sudo systemctl start research-bot

# Done!
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ Setup Complete!"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Your Telegram bot is now running 24/7."
echo ""
echo "  Commands:"
echo "    sudo systemctl status research-bot   # Check status"
echo "    sudo journalctl -u research-bot -f   # View logs"
echo "    sudo systemctl restart research-bot  # Restart"
echo ""
echo "  Message your bot on Telegram to test!"
echo ""
