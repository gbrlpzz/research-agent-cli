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

// Stuck detection (10 minutes)
const STUCK_TIMEOUT_MS = 10 * 60 * 1000;

// State
let activeProcess = null;
let lastPhase = '';
let lastActivityTime = null;
let stuckCheckInterval = null;
let lastReportDir = null;
let activeChatId = null;
let statusMessageId = null;

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
        `/resume - Resume last interrupted session\n` +
        `/qa <question> - Query your library\n` +
        `/model - Toggle fast/powerful model\n` +
        `/status - Check if running\n` +
        `/cancel - Stop current task\n\n` +
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
        `/resume\nResume last interrupted session\n\n` +
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
        const elapsed = lastActivityTime ? Math.round((Date.now() - lastActivityTime) / 60000) : 0;
        bot.sendMessage(msg.chat.id, `⏳ Research in progress (${lastPhase || 'starting'})\n\nLast activity: ${elapsed} min ago\nUse /cancel to stop.`);
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
        if (stuckCheckInterval) {
            clearInterval(stuckCheckInterval);
            stuckCheckInterval = null;
        }
        bot.sendMessage(msg.chat.id, `🛑 Research cancelled.${lastReportDir ? '\n\nUse /resume to continue later.' : ''}`);
    } else {
        bot.sendMessage(msg.chat.id, 'ℹ️ No active research to cancel.');
    }
});

// /resume command
bot.onText(/\/resume/, (msg) => {
    if (!isAuthorized(msg)) return;

    if (activeProcess) {
        bot.sendMessage(msg.chat.id, '⏳ Research already in progress. Use /cancel first.');
        return;
    }

    // Find latest interrupted session
    const reportsDir = path.join(CLI_PATH, 'reports');
    if (!fs.existsSync(reportsDir)) {
        bot.sendMessage(msg.chat.id, '❌ No reports directory found.');
        return;
    }

    // Find directories with checkpoint but no main.pdf
    const dirs = fs.readdirSync(reportsDir)
        .filter(d => d.match(/^20\\d{6}_/))
        .map(d => path.join(reportsDir, d))
        .filter(d => fs.statSync(d).isDirectory())
        .filter(d => fs.existsSync(path.join(d, 'artifacts', 'checkpoint.json')) && !fs.existsSync(path.join(d, 'main.pdf')))
        .sort()
        .reverse();

    if (dirs.length === 0) {
        bot.sendMessage(msg.chat.id, '✅ No interrupted sessions found. All research completed!');
        return;
    }

    const latestDir = dirs[0];
    runResearchResume(msg.chat.id, latestDir);
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

    proc.stdout.on('data', (data) => {
        process.stdout.write(data);
        output += data.toString();
    });
    proc.stderr.on('data', (data) => {
        process.stderr.write(data);
        output += data.toString();
    });

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
// Main research function
async function runResearch(chatId, topic) {
    if (activeProcess) {
        bot.sendMessage(chatId, '⏳ Research already in progress. Use /cancel first.');
        return;
    }

    const modelLabel = currentModel === 'fast' ? '⚡ Fast' : '🧠 Powerful';
    const modelName = MODELS[currentModel];

    const sentMsg = await bot.sendMessage(chatId,
        `🔬 *Starting research*\n\n_"${topic}"_\n\nModel: ${modelLabel}\nI'll notify you when it's done or if something goes wrong.`,
        { parse_mode: 'Markdown' }
    );
    // statusMessageId = sentMsg.message_id; // No longer needed, Python handles its own status msg

    const venvActivate = path.join(CLI_PATH, '.venv', 'bin', 'activate');
    const escapedTopic = topic.replace(/"/g, '\\"');
    // Pass chat ID explicitly to Python agent
    const baseCmd = `${binPath} agent --json-output --reasoning-model "${modelName}" --telegram-chat-id "${chatId}" "${escapedTopic}"`;
    const cmd = fs.existsSync(venvActivate)
        ? `source ${venvActivate} && ${baseCmd}`
        : baseCmd;

    startResearchProcess(chatId, cmd, topic);
}

// Resume research function
// Resume research function
async function runResearchResume(chatId, reportDir) {
    const modelLabel = currentModel === 'fast' ? '⚡ Fast' : '🧠 Powerful';
    const modelName = MODELS[currentModel];

    const dirName = path.basename(reportDir);
    const sentMsg = await bot.sendMessage(chatId,
        `🔄 *Resuming research*\n\n_${dirName}_\n\nModel: ${modelLabel}`,
        { parse_mode: 'Markdown' }
    );
    statusMessageId = sentMsg.message_id;

    const venvActivate = path.join(CLI_PATH, '.venv', 'bin', 'activate');
    const baseCmd = `${binPath} agent --json-output --reasoning-model "${modelName}" --resume "${reportDir}"`;
    const cmd = fs.existsSync(venvActivate)
        ? `source ${venvActivate} && ${baseCmd}`
        : baseCmd;

    startResearchProcess(chatId, cmd, dirName);
}

// Shared research process handler
function startResearchProcess(chatId, cmd, label) {
    const proc = spawn('bash', ['-c', cmd], { cwd: CLI_PATH });
    activeProcess = proc;
    activeChatId = chatId;
    lastPhase = '';
    lastActivityTime = Date.now();
    lastReportDir = null;

    let pdfPath = null;
    let stuckWarned = false;
    const startTime = Date.now();

    // Stuck detection interval
    stuckCheckInterval = setInterval(() => {
        if (lastActivityTime && (Date.now() - lastActivityTime) > STUCK_TIMEOUT_MS && !stuckWarned) {
            stuckWarned = true;
            bot.sendMessage(chatId, `⚠️ *No activity for 10+ minutes*\n\nPhase: ${lastPhase || 'unknown'}\n\nUse /cancel to stop, then /resume later.`, { parse_mode: 'Markdown' });
        }
    }, 60000); // Check every minute

    proc.stdout.on('data', (data) => {
        lastActivityTime = Date.now(); // Reset stuck timer on any output
        process.stdout.write(data); // Stream to parent terminal

        // Python agent handles its own status updates now
        // We just track report dir for resume capability
        const lines = data.toString().split('\n');
        for (const line of lines) {
            if (!line.trim()) continue;
            try {
                const update = JSON.parse(line);
                if (update.phase) lastPhase = update.phase;
                if (update.report_dir) lastReportDir = update.report_dir;
            } catch {
                const dirMatch = line.match(/reports\/(20\d{6}_[^\s\/]+)/);
                if (dirMatch) lastReportDir = path.join(CLI_PATH, 'reports', dirMatch[1]);
            }
        }
    });

    proc.stderr.on('data', (data) => {
        lastActivityTime = Date.now(); // Reset stuck timer
        process.stderr.write(data); // Stream to parent terminal
        // Log errors but don't spam user
        // console.error('Agent stderr:', data.toString());
    });

    proc.on('close', async (code) => {
        activeProcess = null;
        if (stuckCheckInterval) {
            clearInterval(stuckCheckInterval);
            stuckCheckInterval = null;
        }

        // Python agent sends the final PDF itself now.
        // We only notify on failure/crash.
        if (code !== 0) {
            const resumeHint = lastReportDir ? `\n\nUse /resume to continue from checkpoint.` : '';
            bot.sendMessage(chatId, `❌ Research failed (exit code ${code})\n\nPhase: ${lastPhase || 'unknown'}${resumeHint}`);
        } else {
            console.log('✅ process finished successfully. (Agent sent PDF directly)');
        }

        lastPhase = '';
    });

    proc.on('error', (err) => {
        activeProcess = null;
        if (stuckCheckInterval) {
            clearInterval(stuckCheckInterval);
            stuckCheckInterval = null;
        }
        bot.sendMessage(chatId, `❌ Error: ${err.message}`);
    });
}

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('\nShutting down...');
    if (stuckCheckInterval) clearInterval(stuckCheckInterval);
    if (activeProcess) activeProcess.kill('SIGTERM');
    bot.stopPolling();
    process.exit(0);
});

console.log('✅ Bot ready! Send /start to your bot on Telegram.');
