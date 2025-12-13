/**
 * Research Agent Telegram Bot
 * 
 * A personal Telegram bot that accepts research prompts and returns PDFs.
 */

require('dotenv').config();
const TelegramBot = require('node-telegram-bot-api');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

// Configuration
const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CLI_PATH = process.env.RESEARCH_CLI_PATH || path.resolve(__dirname, '..');
const AUTHORIZED_USER_ID = process.env.AUTHORIZED_USER_ID || null;

if (!BOT_TOKEN) {
    console.error('❌ TELEGRAM_BOT_TOKEN not set in .env');
    console.error('   Get one from @BotFather on Telegram');
    process.exit(1);
}

// Model configuration
const MODELS = {
    fast: 'gemini/gemini-2.5-flash',
    powerful: 'gemini/gemini-2.5-pro-preview'
};
let currentModel = 'fast'; // Default to fast

// State
let activeProcess = null;
let lastPhase = '';

// Create bot
const bot = new TelegramBot(BOT_TOKEN, { polling: true });

console.log('🚀 Research Agent Telegram Bot starting...');
console.log('✅ CLI path:', CLI_PATH);

// Check CLI exists
const binPath = path.join(CLI_PATH, 'bin', 'research');
if (!fs.existsSync(binPath)) {
    console.error('❌ Research CLI not found at:', binPath);
    process.exit(1);
}

// Authorization check
function isAuthorized(msg) {
    if (!AUTHORIZED_USER_ID) return true;
    return msg.from.id.toString() === AUTHORIZED_USER_ID;
}

// /start command
bot.onText(/\/start/, (msg) => {
    if (!isAuthorized(msg)) return;

    const modelLabel = currentModel === 'fast' ? '⚡ Fast' : '🧠 Powerful';
    bot.sendMessage(msg.chat.id,
        `🤖 *Research Agent Bot*\n\n` +
        `Send me a research topic and I'll generate a PDF report.\n\n` +
        `*Commands:*\n` +
        `/research <topic> - Start research\n` +
        `/qa <question> - Query your library\n` +
        `/model - Toggle fast/powerful model\n` +
        `/status - Check if running\n` +
        `/cancel - Stop current task\n` +
        `/help - Show this message\n\n` +
        `_Current model: ${modelLabel}_`,
        { parse_mode: 'Markdown' }
    );
});

// /help command
bot.onText(/\/help/, (msg) => {
    if (!isAuthorized(msg)) return;

    const modelLabel = currentModel === 'fast' ? '⚡ Fast (Flash)' : '🧠 Powerful (Pro)';
    bot.sendMessage(msg.chat.id,
        `🤖 *Research Agent Commands*\n\n` +
        `/research <topic>\nStart a research task (15-45 min)\n\n` +
        `/qa <question>\nAsk about your indexed papers\n\n` +
        `/model\nToggle between fast/powerful model\n\n` +
        `/status\nCheck if research is running\n\n` +
        `/cancel\nStop current research\n\n` +
        `---\n_Current model: ${modelLabel}_\n\n` +
        `Just send a topic without /research to start quickly.`,
        { parse_mode: 'Markdown' }
    );
});

// /model command - toggle between fast and powerful
bot.onText(/\/model/, (msg) => {
    if (!isAuthorized(msg)) return;

    // Toggle model
    currentModel = currentModel === 'fast' ? 'powerful' : 'fast';
    const modelLabel = currentModel === 'fast' ? '⚡ Fast (Flash)' : '🧠 Powerful (Pro)';
    const modelName = MODELS[currentModel];

    bot.sendMessage(msg.chat.id,
        `🔄 *Model switched*\n\n` +
        `Now using: ${modelLabel}\n` +
        `_${modelName}_`,
        { parse_mode: 'Markdown' }
    );
});

// /status command
bot.onText(/\/status/, (msg) => {
    if (!isAuthorized(msg)) return;

    if (activeProcess) {
        bot.sendMessage(msg.chat.id, `⏳ Research in progress (${lastPhase || 'starting'})\n\nUse /cancel to stop.`);
    } else {
        bot.sendMessage(msg.chat.id, '✅ No active research. Send /research <topic> to start.');
    }
});

// /cancel command
bot.onText(/\/cancel/, (msg) => {
    if (!isAuthorized(msg)) return;

    if (activeProcess) {
        activeProcess.kill('SIGTERM');
        activeProcess = null;
        lastPhase = '';
        bot.sendMessage(msg.chat.id, '🛑 Research cancelled.');
    } else {
        bot.sendMessage(msg.chat.id, 'ℹ️ No active research to cancel.');
    }
});

// /research command
bot.onText(/\/research (.+)/, (msg, match) => {
    if (!isAuthorized(msg)) return;
    runResearch(msg.chat.id, match[1]);
});

// /qa command
bot.onText(/\/qa (.+)/, async (msg, match) => {
    if (!isAuthorized(msg)) return;

    const question = match[1];
    bot.sendMessage(msg.chat.id, '🔍 Searching library...');

    const venvActivate = path.join(CLI_PATH, '.venv', 'bin', 'activate');
    const cmd = fs.existsSync(venvActivate)
        ? `source ${venvActivate} && ${binPath} qa "${question}"`
        : `${binPath} qa "${question}"`;

    const proc = spawn('bash', ['-c', cmd], { cwd: CLI_PATH, env: { ...process.env, NO_COLOR: '1' } });
    let output = '';

    proc.stdout.on('data', (data) => { output += data.toString(); });
    proc.stderr.on('data', (data) => { output += data.toString(); });

    proc.on('close', (code) => {
        if (code === 0 && output.trim()) {
            // Strip ANSI escape codes and Rich formatting
            let clean = output
                .replace(/\x1b\[[0-9;]*m/g, '')  // ANSI colors
                .replace(/\x1b\[\?.*?[a-zA-Z]/g, '')  // ANSI control sequences
                .replace(/[─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬]/g, '')  // Box drawing
                .replace(/[▀▁▂▃▄▅▆▇█▉▊▋▌▍▎▏]/g, '')  // Block elements
                .replace(/\s*\n\s*\n\s*\n+/g, '\n\n')  // Multiple blank lines
                .trim();

            // Truncate if too long
            if (clean.length > 4000) {
                clean = clean.substring(0, 3900) + '\n...(truncated)';
            }

            bot.sendMessage(msg.chat.id, `📖 *Answer*\n\n${clean}`, { parse_mode: 'Markdown' }).catch(() => {
                bot.sendMessage(msg.chat.id, `📖 Answer\n\n${clean}`);
            });
        } else {
            bot.sendMessage(msg.chat.id, '❌ Could not find an answer in the library.');
        }
    });
});

// Plain text (treat as research topic)
bot.on('message', (msg) => {
    if (!isAuthorized(msg)) return;
    if (msg.text?.startsWith('/')) return; // Skip commands
    if (!msg.text || msg.text.length < 10) return; // Skip short messages

    runResearch(msg.chat.id, msg.text);
});

// Main research function
function runResearch(chatId, topic) {
    if (activeProcess) {
        bot.sendMessage(chatId, '⏳ Research already in progress. Use /cancel first.');
        return;
    }

    const modelLabel = currentModel === 'fast' ? '⚡ Fast' : '🧠 Powerful';
    const modelName = MODELS[currentModel];

    bot.sendMessage(chatId,
        `🔬 *Starting research*\\n\\n_"${topic}"_\\n\\nModel: ${modelLabel}\\nThis will take 15-45 minutes.`,
        { parse_mode: 'Markdown' }
    );

    const venvActivate = path.join(CLI_PATH, '.venv', 'bin', 'activate');
    const escapedTopic = topic.replace(/"/g, '\\"');
    const baseCmd = `${binPath} agent --json-output --reasoning-model "${modelName}" "${escapedTopic}"`;
    const cmd = fs.existsSync(venvActivate)
        ? `source ${venvActivate} && ${baseCmd}`
        : baseCmd;

    const proc = spawn('bash', ['-c', cmd], { cwd: CLI_PATH });
    activeProcess = proc;
    lastPhase = '';

    let pdfPath = null;

    proc.stdout.on('data', (data) => {
        const lines = data.toString().split('\n');
        for (const line of lines) {
            if (!line.trim()) continue;

            try {
                const update = JSON.parse(line);

                // Phase update
                if (update.phase && update.phase !== lastPhase) {
                    lastPhase = update.phase;
                    const emoji = {
                        'Starting': '🚀',
                        'Planning': '📋',
                        'ArgumentMap': '🗺️',
                        'Drafting': '✍️',
                        'Review': '🔎',
                        'Revision': '📝',
                        'Complete': '✅'
                    };
                    bot.sendMessage(chatId, `${emoji[update.phase] || '➤'} ${update.phase}`);
                }

                // PDF path
                if (update.pdf_path) {
                    pdfPath = update.pdf_path;
                }
            } catch {
                // Not JSON, check for PDF path in plain output
                const match = line.match(/reports\/[^\s]+\/main\.pdf/);
                if (match) {
                    pdfPath = path.join(CLI_PATH, match[0]);
                }
            }
        }
    });

    proc.on('close', async (code) => {
        activeProcess = null;

        if (code === 0 && pdfPath && fs.existsSync(pdfPath)) {
            bot.sendMessage(chatId, '✅ Research complete! Sending PDF...');

            try {
                await bot.sendDocument(chatId, pdfPath, {
                    caption: `📄 ${topic.substring(0, 50)}...`
                });
            } catch (err) {
                bot.sendMessage(chatId, `⚠️ PDF ready but couldn't send: ${err.message}\n\nPath: ${pdfPath}`);
            }
        } else if (code === 0) {
            bot.sendMessage(chatId, '✅ Research complete but PDF not found.');
        } else {
            bot.sendMessage(chatId, `❌ Research failed (exit code ${code})`);
        }

        lastPhase = '';
    });

    proc.on('error', (err) => {
        activeProcess = null;
        bot.sendMessage(chatId, `❌ Error: ${err.message}`);
    });
}

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('\nShutting down...');
    if (activeProcess) activeProcess.kill('SIGTERM');
    bot.stopPolling();
    process.exit(0);
});

console.log('✅ Bot ready! Send /start to your bot on Telegram.');
