# Minecraft Server Manager (Node.js + HTML)
A minimal web UI + API to create, start/stop, and delete local Minecraft Java Edition servers. Upload your server JAR (vanilla/paper/spigot/fabric/etc), create instances on different ports, and manage them from a slick dark neon dashboard.

> ⚠️ For local/home use. Do **not** expose this directly to the internet. Add authentication, HTTPS, reverse proxy, sandboxing if needed.

## Requirements
- Node.js 18+
- Java 17+ installed and available on PATH (run `java -version`)
- Enough RAM and open ports for your instances

## Quick Start
```bash
npm install
npm start
```
- Visit http://localhost:3000
- Upload a `.jar` (e.g., `paper-1.20.6.jar`)
- Create a server with a name, port, RAM, and choose the jar
- Start/Stop servers, view logs, send console commands
- Delete removes the entire instance directory (world, plugins, etc).

## File Layout
```
.
├── server.js          # Express API + process control
├── package.json
├── public/            # Frontend (HTML/CSS/JS)
│   ├── index.html
│   ├── style.css
│   └── app.js
└── data/
    ├── jars/          # Uploaded jars
    ├── servers/       # Server instance folders
    └── servers.json   # Instance metadata
```

## Notes
- Accepts Mojang EULA automatically by writing `eula=true` in each instance. Be sure you agree to the EULA.
- Logs are tailed by reading the `console.log` written from the process stdout/stderr.
- CORS is enabled (so you can build a separate frontend if you want).
- Tested on Linux & Windows; on Windows ensure your `java` is on PATH. If not, edit `server.js` to use a full path to java.
- This is intentionally simple: no auth, no websockets, no plugin management. Add those if needed.
