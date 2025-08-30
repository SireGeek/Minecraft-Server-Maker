/**
 * Minecraft Server Manager
 * - Create, list, delete server instances
 * - Upload JAR files
 * - Start/Stop servers
 * - View logs (simple endpoint; not streaming)
 * 
 * SECURITY NOTE: This is for local/home use. Do NOT expose to the public internet without auth and hardening.
 */

const express = require('express');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const multer = require('multer');
const cors = require('cors');

const app = express();
const PORT = process.env.PORT || 3000;

// Enable CORS
app.use(cors());

// JSON parsing
app.use(express.json());

// Static files (frontend)
app.use(express.static(path.join(__dirname, 'public')));

const DATA_DIR = path.join(__dirname, 'data');
const JAR_DIR = path.join(DATA_DIR, 'jars');
const SERVERS_DIR = path.join(DATA_DIR, 'servers');
const STATE_FILE = path.join(DATA_DIR, 'servers.json');

// Ensure folders
for (const d of [DATA_DIR, JAR_DIR, SERVERS_DIR]) {
  if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
}

// In-memory process map {id: childProcess}
const procMap = new Map();

// Load persisted servers
function loadServers() {
  if (!fs.existsSync(STATE_FILE)) return [];
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
  } catch (e) {
    console.error('Failed to parse servers.json:', e);
    return [];
  }
}

function saveServers(servers) {
  fs.writeFileSync(STATE_FILE, JSON.stringify(servers, null, 2));
}

function generateId(name) {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const rand = Math.random().toString(36).substring(2, 7);
  return `${slug}-${rand}`;
}

// Multer storage for JAR uploads
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, JAR_DIR),
  filename: (req, file, cb) => {
    // sanitize filename
    const base = path.basename(file.originalname).replace(/[^a-zA-Z0-9._-]/g, '_');
    cb(null, base);
  }
});
const upload = multer({
  storage,
  fileFilter: (req, file, cb) => {
    if (!file.originalname.endsWith('.jar')) return cb(new Error('Only .jar files are allowed'));
    cb(null, true);
  },
  limits: { fileSize: 1024 * 1024 * 200 } // 200 MB
});

// API: Upload JAR
app.post('/api/uploadJar', upload.single('jar'), (req, res) => {
  res.json({ ok: true, filename: path.basename(req.file.filename) });
});

// API: List available JARs
app.get('/api/jars', (req, res) => {
  if (!fs.existsSync(JAR_DIR)) return res.json([]);
  const jars = fs.readdirSync(JAR_DIR).filter(f => f.endsWith('.jar'));
  res.json(jars);
});

// API: List servers
app.get('/api/servers', (req, res) => {
  const servers = loadServers().map(s => {
    const running = procMap.has(s.id);
    return { ...s, running };
  });
  res.json(servers);
});

// API: Create server
app.post('/api/servers', (req, res) => {
  const { name, port, maxMemoryMB, jar } = req.body || {};
  if (!name || !port || !jar) {
    return res.status(400).json({ error: 'name, port, jar are required' });
  }
  const jarPath = path.join(JAR_DIR, path.basename(jar));
  if (!fs.existsSync(jarPath)) {
    return res.status(400).json({ error: 'JAR not found. Upload it first.' });
  }

  const id = generateId(name);
  const dir = path.join(SERVERS_DIR, id);
  fs.mkdirSync(dir, { recursive: true });

  // minimal server properties
  const serverPropPath = path.join(dir, 'server.properties');
  if (!fs.existsSync(serverPropPath)) {
    fs.writeFileSync(serverPropPath, `server-port=${port}\nmax-players=20\nenable-jmx-monitoring=false\n`);
  }
  // Accept EULA automatically
  fs.writeFileSync(path.join(dir, 'eula.txt'), 'eula=true\n');

  const servers = loadServers();
  const newS = { id, name, port, maxMemoryMB: maxMemoryMB || 1024, jar: path.basename(jar), dir };
  servers.push(newS);
  saveServers(servers);

  res.json(newS);
});

// API: Delete server
app.delete('/api/servers/:id', async (req, res) => {
  const { id } = req.params;
  const servers = loadServers();
  const s = servers.find(x => x.id === id);
  if (!s) return res.status(404).json({ error: 'Not found' });
  if (procMap.has(id)) return res.status(400).json({ error: 'Stop the server first' });

  // remove folder
  fs.rmSync(s.dir, { recursive: true, force: true });
  saveServers(servers.filter(x => x.id !== id));
  res.json({ ok: true });
});

// Helper: get server by id
function getServer(id) {
  const servers = loadServers();
  return servers.find(x => x.id === id);
}

// API: Start server
app.post('/api/servers/:id/start', (req, res) => {
  const { id } = req.params;
  if (procMap.has(id)) return res.status(400).json({ error: 'Already running' });
  const s = getServer(id);
  if (!s) return res.status(404).json({ error: 'Not found' });

  const jarPath = path.join(JAR_DIR, s.jar);
  if (!fs.existsSync(jarPath)) return res.status(400).json({ error: 'JAR missing on disk' });

  const args = [];
  if (s.maxMemoryMB) {
    args.push(`-Xmx${s.maxMemoryMB}M`, `-Xms${Math.min(512, s.maxMemoryMB)}M`);
  }
  args.push('-jar', jarPath, 'nogui');

  const child = spawn('java', args, {
    cwd: s.dir,
    stdio: ['pipe', 'pipe', 'pipe']
  });

  procMap.set(id, child);

  // append logs
  const logFile = path.join(s.dir, 'console.log');
  const logStream = fs.createWriteStream(logFile, { flags: 'a' });
  child.stdout.on('data', d => logStream.write(d));
  child.stderr.on('data', d => logStream.write(d));

  child.on('close', (code) => {
    procMap.delete(id);
    logStream.end(`\n[process exited with code ${code}]\n`);
  });

  res.json({ ok: true, pid: child.pid });
});

// API: Stop server (graceful "stop" command to console)
app.post('/api/servers/:id/stop', (req, res) => {
  const { id } = req.params;
  const child = procMap.get(id);
  if (!child) return res.status(400).json({ error: 'Not running' });

  try {
    child.stdin.write('stop\n');
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: 'Failed to send stop', details: String(e) });
  }
});

// API: Send command
app.post('/api/servers/:id/command', (req, res) => {
  const { id } = req.params;
  const { command } = req.body || {};
  const child = procMap.get(id);
  if (!child) return res.status(400).json({ error: 'Not running' });
  if (!command) return res.status(400).json({ error: 'command required' });
  try {
    child.stdin.write(command.trim() + '\n');
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: 'Failed to send command', details: String(e) });
  }
});

// API: Get logs (last 2000 lines or 1 MB)
app.get('/api/servers/:id/logs', (req, res) => {
  const { id } = req.params;
  const s = getServer(id);
  if (!s) return res.status(404).json({ error: 'Not found' });
  const logFile = path.join(s.dir, 'console.log');
  if (!fs.existsSync(logFile)) return res.send('');
  try {
    const stat = fs.statSync(logFile);
    const size = Math.min(stat.size, 1024 * 1024);
    const fd = fs.openSync(logFile, 'r');
    const buffer = Buffer.alloc(size);
    fs.readSync(fd, buffer, 0, size, stat.size - size);
    fs.closeSync(fd);
    const lines = buffer.toString('utf8').split(/\r?\n/);
    const tail = lines.slice(-2000).join('\n');
    res.type('text/plain').send(tail);
  } catch (e) {
    res.status(500).json({ error: 'Failed to read logs', details: String(e) });
  }
});

// Simple health
app.get('/api/health', (req, res) => res.json({ ok: true }));

app.listen(PORT, () => {
  console.log(`Minecraft Server Manager running at http://localhost:${PORT}`);
});
