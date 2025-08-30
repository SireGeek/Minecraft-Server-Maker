const api = {
  async getServers() {
    const res = await fetch('/api/servers');
    return res.json();
  },
  async getJars() {
    const res = await fetch('/api/jars');
    return res.json();
  },
  async uploadJar(file) {
    const fd = new FormData();
    fd.append('jar', file);
    const res = await fetch('/api/uploadJar', { method: 'POST', body: fd });
    if (!res.ok) throw new Error('Upload failed');
    return res.json();
  },
  async createServer(payload) {
    const res = await fetch('/api/servers', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  async start(id) {
    const res = await fetch(`/api/servers/${id}/start`, { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  async stop(id) {
    const res = await fetch(`/api/servers/${id}/stop`, { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  async del(id) {
    const res = await fetch(`/api/servers/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  async logs(id) {
    const res = await fetch(`/api/servers/${id}/logs`);
    return res.text();
  },
  async send(id, command) {
    const res = await fetch(`/api/servers/${id}/command`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command })
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }
};

const el = s => document.querySelector(s);
const serversEl = el('#servers');
const jarSelect = el('#jarSelect');
const jarsList = el('#jarsList');
const logsPanel = el('#logsPanel');
const logsText = el('#logsText');
const logsTitle = el('#logsTitle');

let allServers = [];
let logsTimer = null;
let currentLogServer = null;

function chip(text) {
  const c = document.createElement('span');
  c.className = 'chip';
  c.textContent = text;
  return c;
}

async function refreshJars() {
  const jars = await api.getJars();
  jarSelect.innerHTML = '';
  jarsList.innerHTML = '';
  jars.forEach(j => {
    const opt = document.createElement('option');
    opt.value = j; opt.textContent = j;
    jarSelect.appendChild(opt);
    jarsList.appendChild(chip(j));
  });
}

function renderServers(list) {
  serversEl.innerHTML = '';
  const tpl = document.getElementById('serverCard');
  list.forEach(s => {
    const node = tpl.content.cloneNode(true);
    node.querySelector('.name').textContent = s.name;
    const badge = node.querySelector('.badge');
    badge.textContent = s.running ? 'Running' : 'Stopped';
    badge.classList.toggle('running', !!s.running);
    node.querySelector('.meta').textContent = `Port ${s.port} • RAM ${s.maxMemoryMB}MB • JAR ${s.jar}`;
    // Actions
    node.querySelector('.start').onclick = async () => { await api.start(s.id); await refresh(); };
    node.querySelector('.stop').onclick = async () => { await api.stop(s.id); await refresh(); };
    node.querySelector('.delete').onclick = async () => {
      if (confirm(`Delete ${s.name}? This removes its world folder.`)) {
        await api.del(s.id);
        await refresh();
      }
    };
    node.querySelector('.logs').onclick = async () => openLogs(s);
    const sendBtn = node.querySelector('.send');
    const cmdInput = node.querySelector('.cmd');
    sendBtn.onclick = async () => {
      const cmd = cmdInput.value.trim();
      if (!cmd) return;
      await api.send(s.id, cmd);
      cmdInput.value = '';
      // refresh logs quickly
      if (currentLogServer?.id === s.id) pullLogs();
    };
    serversEl.appendChild(node);
  });
}

async function refresh() {
  const search = el('#search').value.trim().toLowerCase();
  allServers = await api.getServers();
  const view = search ? allServers.filter(s => s.name.toLowerCase().includes(search)) : allServers;
  renderServers(view);
  await refreshJars();
}

async function openLogs(server) {
  logsPanel.classList.remove('hidden');
  currentLogServer = server;
  logsTitle.textContent = `Logs — ${server.name}`;
  await pullLogs();
  if (logsTimer) clearInterval(logsTimer);
  logsTimer = setInterval(pullLogs, 2000);
}

async function pullLogs() {
  if (!currentLogServer) return;
  const text = await api.logs(currentLogServer.id);
  logsText.textContent = text || 'No logs yet.';
  logsText.scrollTop = logsText.scrollHeight;
}

document.getElementById('closeLogs').onclick = () => {
  logsPanel.classList.add('hidden');
  if (logsTimer) clearInterval(logsTimer);
  logsTimer = null; currentLogServer = null;
};

// Actions: upload JAR
document.getElementById('uploadJarBtn').onclick = async () => {
  const file = document.getElementById('jarFile').files[0];
  if (!file) return alert('Choose a .jar first');
  await api.uploadJar(file);
  await refreshJars();
  alert('Uploaded!');
};

// Create server
document.getElementById('createBtn').onclick = async () => {
  const name = document.getElementById('name').value.trim();
  const port = parseInt(document.getElementById('port').value, 10);
  const maxMemoryMB = parseInt(document.getElementById('maxMemoryMB').value || '1024', 10);
  const jar = document.getElementById('jarSelect').value;
  if (!name || !port || !jar) return alert('Fill out name, port, jar');
  await api.createServer({ name, port, maxMemoryMB, jar });
  await refresh();
  document.getElementById('name').value = '';
};

// Search
document.getElementById('search').addEventListener('input', () => {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const view = q ? allServers.filter(s => s.name.toLowerCase().includes(q)) : allServers;
  renderServers(view);
});

document.getElementById('refresh').onclick = refresh;

// First load
refresh().catch(err => console.error(err));
