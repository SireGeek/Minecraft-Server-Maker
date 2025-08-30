// server.js — improved for CPU/memory stability
const fs = require("fs");
const path = require("path");
const express = require("express");
const bodyParser = require("body-parser");
const { Client, GatewayIntentBits, Partials, PermissionsBitField } = require("discord.js");
const { WebcastPushConnection, WebcastEvent } = require("tiktok-live-connector");

const CONFIG_PATH = path.join(__dirname, "config.json");
const DASHBOARD_DIR = path.join(__dirname, "public"); // adjust if different

// --- Config helpers ---
function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
  } catch (e) {
    console.error("Failed to load config.json:", e.message);
    return {};
  }
}
function saveConfig(cfg) {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2));
}

let config = loadConfig();

// --- Minimal rotating in-memory logs (bounded) ---
const MAX_LOGS = 200;
const logs = [];
function pushLog(line) {
  const t = new Date().toISOString();
  logs.push(`[${t}] ${line}`);
  if (logs.length > MAX_LOGS) logs.shift();
}
// optional small helper to print important logs only:
function info(msg) {
  pushLog(msg);
  // still print important messages to console
  console.info(msg);
}

// --- Express dashboard & API ---
const app = express();
app.use(bodyParser.json());
app.use(express.static(DASHBOARD_DIR));

app.get("/api/config", (req, res) => res.json(config));
app.post("/api/config", (req, res) => {
  config = req.body || {};
  saveConfig(config);
  info("Config updated via dashboard");
  res.json({ success: true });
});
app.get("/api/logs", (req, res) => res.json({ logs }));

const PORT = config.dashboardPort || 3000;
app.listen(PORT, () => info(`Dashboard running at http://localhost:${PORT}`));

// --- Discord client setup ---
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.GuildMembers,
  ],
  partials: [Partials.Message, Partials.Channel, Partials.Reaction],
});

let cachedChannels = []; // array of TextChannel objects
let cachedChannelNames = []; // names we used to cache
function cacheChannels() {
  if (!config.discordChannels || !Array.isArray(config.discordChannels)) return;
  cachedChannels = config.discordChannels
    .map((name) => client.channels.cache.find((ch) => ch.name === name && ch.isTextBased()))
    .filter(Boolean);
  cachedChannelNames = cachedChannels.map((c) => c.name);
  info(`Cached ${cachedChannels.length} channels: ${cachedChannelNames.join(", ")}`);
}

// --- Message batching / queueing to limit CPU + memory ---
/*
We keep a map: channelId -> array of lines.
Every FLUSH_INTERVAL ms we flush queues to Discord.
We chunk combined text to DISCORD_MAX_MSG_CHARS (safe margin).
We cap queue length to MAX_QUEUE_LEN to prevent memory blowup.
*/
const FLUSH_INTERVAL = 1000; // ms
const DISCORD_SAFE_CHAR_LIMIT = 1900;
const MAX_QUEUE_LEN = 500; // max messages per channel queue

const queues = new Map(); // channelId -> [strings]

// ensure queue exists
function ensureQueue(channel) {
  if (!queues.has(channel.id)) queues.set(channel.id, []);
  return queues.get(channel.id);
}

// queue a message for all cached channels
function queueToAll(message) {
  for (const ch of cachedChannels) {
    const q = ensureQueue(ch);
    q.push(message);
    if (q.length > MAX_QUEUE_LEN) {
      // drop oldest to keep memory bounded
      const dropCount = q.length - MAX_QUEUE_LEN;
      q.splice(0, dropCount);
      pushLog(`Dropped ${dropCount} old messages from queue for channel ${ch.name}`);
    }
  }
}

// flush queues periodically
setInterval(async () => {
  if (!client || !client.isReady()) return;
  for (const ch of cachedChannels) {
    const q = queues.get(ch.id);
    if (!q || q.length === 0) continue;

    // combine lines into chunks under DISCORD_SAFE_CHAR_LIMIT
    let chunk = "";
    while (q.length > 0) {
      const next = q.shift();
      if ((chunk + "\n" + next).length > DISCORD_SAFE_CHAR_LIMIT) {
        // send current chunk
        try {
          await ch.send(chunk);
        } catch (err) {
          pushLog(`Failed to send chunk to ${ch.name}: ${err.message}`);
        }
        chunk = next;
      } else {
        chunk = chunk ? chunk + "\n" + next : next;
      }

      // if queue is empty after taking next, send final chunk
      if (q.length === 0 && chunk.length > 0) {
        try {
          await ch.send(chunk);
        } catch (err) {
          pushLog(`Failed to send final chunk to ${ch.name}: ${err.message}`);
        }
        chunk = "";
      }
    }
    // ensure queue is empty
    queues.delete(ch.id);
  }
}, FLUSH_INTERVAL);

// --- TikTok connection handling (using WebcastPushConnection + WebcastEvent) ---
let tiktokConn = null;
let tiktokConnectedRoomId = null;
let tiktokConnecting = false;

async function startTikTokListener() {
  if (!config.tiktokUsername) {
    pushLog("TikTok username not configured; cannot start TikTok listener");
    return;
  }
  if (tiktokConn || tiktokConnecting) {
    pushLog("TikTok listener already running or connecting");
    return;
  }

  tiktokConnecting = true;
  try {
    tiktokConn = new WebcastPushConnection(config.tiktokUsername);

    tiktokConn.on(WebcastEvent.CONNECTED, (state) => {
      tiktokConnectedRoomId = state?.roomId || null;
      info(`Connected to TikTok live @${config.tiktokUsername} (room ${tiktokConnectedRoomId})`);
      queueToAll(`📡 Started TikTok live chat logging for @${config.tiktokUsername}`);
    });

    tiktokConn.on(WebcastEvent.DISCONNECTED, () => {
      info(`Disconnected from TikTok live @${config.tiktokUsername}`);
      queueToAll(`📴 Stopped TikTok live chat logging for @${config.tiktokUsername}`);
      tiktokConn = null;
      tiktokConnectedRoomId = null;
    });

    // CHAT events — push to queues, not send immediately
    tiktokConn.on(WebcastEvent.CHAT, (data) => {
      // some events may be large; only push formatted small strings
      try {
        const user = data.uniqueId || data.user?.uniqueId || "unknown";
        const comment = (data.comment || data.msg || "").toString().trim();
        if (!comment) return;
        const line = `🎤 **${user}**: ${comment}`;
        queueToAll(line);
      } catch (e) {
        // swallow parse errors to avoid crash
      }
    });

    // other useful events can be handled similarly but avoid heavy work

    await tiktokConn.connect();
  } catch (err) {
    pushLog("TikTok connection failed: " + (err?.message || String(err)));
    // cleanup
    if (tiktokConn) {
      try { tiktokConn.disconnect(); } catch {}
      tiktokConn = null;
    }
  } finally {
    tiktokConnecting = false;
  }
}

function stopTikTokListener() {
  if (tiktokConn) {
    try {
      tiktokConn.disconnect();
    } catch (e) {}
    tiktokConn = null;
    tiktokConnectedRoomId = null;
    info("TikTok listener stopped");
  }
}

// --- Helper to send message(s) immediately to cached channels (rare use) ---
async function sendToAllImmediate(text) {
  for (const ch of cachedChannels) {
    try {
      await ch.send(text);
    } catch (e) {
      pushLog(`Immediate send failed to ${ch.name}: ${e.message}`);
    }
  }
}

// --- Discord event handlers (cache channels once, moderation commands) ---
client.once("ready", () => {
  info(`Discord ready: ${client.user.tag}`);
  cacheChannels();
  // optionally set presence from config
  if (config.botStatus) client.user.setActivity(config.botStatus, { type: 0 });

  // start tikTok if autoStart flag set
  if (config.autoStartTikTok) startTikTokListener();
});

client.on("messageCreate", async (message) => {
  if (message.author?.bot) return;
  if (!message.guild) return;

  // moderation: banned words (simple)
  if (Array.isArray(config.bannedWords) && config.bannedWords.length) {
    const lc = String(message.content || "").toLowerCase();
    for (const bad of config.bannedWords) {
      if (!bad) continue;
      if (lc.includes(String(bad).toLowerCase())) {
        try {
          await message.delete();
          await message.channel.send(`${message.author}, your message was removed (rule).`);
          pushLog(`Deleted message from ${message.author.tag} for banned word`);
        } catch (e) {
          pushLog(`Failed to moderate message: ${e.message}`);
        }
        return;
      }
    }
  }

  // basic moderation commands (requires permissions)
  const prefix = config.commandPrefix || "!";
  if (!message.content.startsWith(prefix)) return;
  const [cmd, ...args] = message.content.slice(prefix.length).trim().split(/\s+/);

  if (cmd === "kick" && message.member.permissions.has(PermissionsBitField.Flags.KickMembers)) {
    const member = message.mentions.members.first();
    if (member) {
      try {
        await member.kick();
        await message.reply(`👢 Kicked ${member.user.tag}`);
        pushLog(`Kicked ${member.user.tag} via command`);
      } catch (e) {
        pushLog(`Kick failed: ${e.message}`);
      }
    }
  } else if (cmd === "ban" && message.member.permissions.has(PermissionsBitField.Flags.BanMembers)) {
    const member = message.mentions.members.first();
    if (member) {
      try {
        await member.ban();
        await message.reply(`🔨 Banned ${member.user.tag}`);
        pushLog(`Banned ${member.user.tag} via command`);
      } catch (e) {
        pushLog(`Ban failed: ${e.message}`);
      }
    }
  } else if (cmd === "tiktokstart" && message.member.permissions.has(PermissionsBitField.Flags.ManageGuild)) {
    startTikTokListener();
    message.reply("✅ TikTok listener starting...");
  } else if (cmd === "tiktokstop" && message.member.permissions.has(PermissionsBitField.Flags.ManageGuild)) {
    stopTikTokListener();
    message.reply("🛑 TikTok listener stopped.");
  }
});

// --- Prevent unhandled promise rejections from crashing process, but log them ---
process.on("unhandledRejection", (reason) => {
  pushLog("UnhandledRejection: " + (reason?.stack || reason));
});
process.on("uncaughtException", (err) => {
  pushLog("UncaughtException: " + (err?.stack || err));
});

// --- Login and start ---
if (!config.discordToken) {
  console.error("discordToken missing in config.json");
  process.exit(1);
}
client.login(config.discordToken).catch((e) => {
  console.error("Discord login failed:", e.message);
  process.exit(1);
});

// expose a simple health endpoint
app.get("/api/health", (req, res) => {
  res.json({
    discordReady: client?.isReady ? client.isReady() : false,
    tikTokConnected: !!tiktokConn,
    queues: Array.from(queues.entries()).reduce((acc, [chId, q]) => {
      acc[chId] = q.length;
      return acc;
    }, {}),
    memory: process.memoryUsage(),
  });
});