import os
import re
import shutil
import threading
import subprocess
import uuid
import time
import json
from collections import deque
from typing import Optional, Dict, Deque
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
app = Flask(__name__)  # create the app first
CORS(app)  # now enable CORS
import pathlib
import tempfile
import shutil as sh
import sys
import signal

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SERVERS_DIR = os.path.join(BASE_DIR, "servers")
os.makedirs(SERVERS_DIR, exist_ok=True)

JAVA_JDK_LINK = "https://www.oracle.com/java/technologies/javase-jdk17-downloads.html"

CONSOLE_BUFFER_LINES = 1000

app = Flask(__name__)

def sanitize_server_id(s: str) -> str:
    s = s.strip()
    s = re.sub(r'[^A-Za-z0-9_\-]', '_', s)
    return s[:128]

def java_exists() -> bool:
    from shutil import which
    return which('java') is not None

class ServerInstance:
    def __init__(self, server_id: str, server_dir: str):
        self.server_id = server_id
        self.server_dir = server_dir
        self.process: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.console_lines: Deque[str] = deque(maxlen=CONSOLE_BUFFER_LINES)
        self.stdout_thread: Optional[threading.Thread] = None
        self.alive = False
        self.exit_code: Optional[int] = None

    def append_console(self, line: str):
        if isinstance(line, bytes):
            try:
                line = line.decode(errors='replace')
            except:
                line = str(line)
        for l in line.splitlines():
            self.console_lines.append(l)

    def start(self, java_args=None, memory='1G'):
        with self.lock:
            if self.process and self.process.poll() is None:
                raise RuntimeError("Server already running")
            jar_path = os.path.join(self.server_dir, "server.jar")
            if not os.path.exists(jar_path):
                raise FileNotFoundError("server.jar not found in server directory")
            if not java_exists():
                raise EnvironmentError("Java not found")
            cmd = ['java', f'-Xmx{memory}', f'-Xms{memory}', '-jar', 'server.jar', 'nogui']
            if java_args:
                cmd = ['java'] + java_args + cmd[1:]
            self.process = subprocess.Popen(
                cmd,
                cwd=self.server_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True
            )
            self.alive = True
            self.exit_code = None
            self.stdout_thread = threading.Thread(target=self._reader_thread, daemon=True)
            self.stdout_thread.start()
            monitor = threading.Thread(target=self._monitor_thread, daemon=True)
            monitor.start()

    def _reader_thread(self):
        if not self.process:
            return
        try:
            for line in self.process.stdout:
                if line is None:
                    break
                self.append_console(line.rstrip('\n'))
        except Exception as e:
            self.append_console(f"[console reader error] {repr(e)}")

    def _monitor_thread(self):
        if not self.process:
            return
        try:
            rc = self.process.wait()
            self.exit_code = rc
            self.alive = False
            self.append_console(f"[process exited with code {rc}]")
        except Exception as e:
            self.append_console(f"[monitor error] {repr(e)}")
            self.alive = False

    def send_command(self, command: str):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                raise RuntimeError("Server not running")
            if not command.endswith("\n"):
                command = command + "\n"
            try:
                self.process.stdin.write(command)
                self.process.stdin.flush()
            except Exception as e:
                raise RuntimeError(f"Failed to send command: {e}")

    def stop(self, timeout=30):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                return
            try:
                self.send_command("stop")
            except Exception as e:
                self.append_console(f"[stop failed: {e}; will attempt kill]")
                try:
                    self.process.kill()
                except:
                    pass
        start = time.time()
        while True:
            if not self.process or self.process.poll() is not None:
                break
            if time.time() - start > timeout:
                try:
                    self.process.kill()
                except:
                    pass
                break
            time.sleep(0.2)

    def kill(self):
        with self.lock:
            if not self.process or self.process.poll() is not None:
                return
            try:
                self.process.kill()
            except Exception as e:
                self.append_console(f"[kill failed: {e}]")

    def status(self):
        with self.lock:
            running = False
            pid = None
            rc = self.exit_code
            if self.process:
                pid = getattr(self.process, "pid", None)
                running = (self.process.poll() is None)
            return {"server_id": self.server_id, "running": running, "pid": pid, "exit_code": rc}

    def get_console_lines(self, last_n: int = 200):
        with self.lock:
            items = list(self.console_lines)[-last_n:]
            return items

servers: Dict[str, ServerInstance] = {}
servers_lock = threading.Lock()

def get_server_dir(server_id: str) -> str:
    return os.path.join(SERVERS_DIR, server_id)

def ensure_server_loaded(server_id: str) -> ServerInstance:
    sid = sanitize_server_id(server_id)
    with servers_lock:
        if sid in servers:
            return servers[sid]
        server_dir = get_server_dir(sid)
        if os.path.isdir(server_dir):
            inst = ServerInstance(sid, server_dir)
            servers[sid] = inst
            return inst
        else:
            raise FileNotFoundError("Server not found")

@app.route("/connect", methods=["POST"])
def connect():
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id") or str(uuid.uuid4())
    return jsonify({"status": "ok", "client_id": client_id, "message": "Connected (placeholder)"}), 200

@app.route("/servers", methods=["POST"])
def create_server():
    server_id_raw = request.form.get("server_id", "") or request.args.get("server_id", "")
    if server_id_raw:
        server_id = sanitize_server_id(server_id_raw)
    else:
        server_id = f"server_{uuid.uuid4().hex[:8]}"

    server_dir = get_server_dir(server_id)
    if os.path.exists(server_dir):
        return jsonify({"error": "server_exists", "message": "Server ID already exists", "server_id": server_id}), 400
    os.makedirs(server_dir, exist_ok=False)

    jar_file = None
    if 'jar_file' in request.files:
        jar_file = request.files['jar_file']
        jar_path = os.path.join(server_dir, "server.jar")
        jar_file.save(jar_path)
    else:
        placeholder = os.path.join(server_dir, "server.jar")
        with open(placeholder, "wb") as f:
            f.write(b"")
    try:
        with open(os.path.join(server_dir, "eula.txt"), "w", encoding="utf-8") as ef:
            ef.write("# EULA accepted by server manager\n")
            ef.write("eula=true\n")
    except Exception:
        pass

    try:
        with open(os.path.join(server_dir, "server.properties"), "w", encoding="utf-8") as pf:
            pf.write("# Basic server.properties generated by manager\n")
            pf.write("motd=Managed by Python Server Manager\n")
    except Exception:
        pass

    with servers_lock:
        inst = ServerInstance(server_id, server_dir)
        servers[server_id] = inst

    return jsonify({"status": "created", "server_id": server_id, "path": server_dir}), 201

@app.route("/servers/<server_id>/start", methods=["POST"])
def start_server(server_id):
    inst = None
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404

    data = request.get_json(silent=True) or {}
    memory = data.get("memory", "1G")
    java_args = data.get("java_args")
    if not java_exists():
        return jsonify({
            "error": "java_not_found",
            "message": "Java runtime not found on server host",
            "java_jdk_download": JAVA_JDK_LINK
        }), 500
    try:
        inst.start(java_args=java_args, memory=memory)
    except FileNotFoundError as e:
        return jsonify({"error": "jar_missing", "message": str(e)}), 400
    except EnvironmentError as e:
        return jsonify({"error": "java_not_found", "message": str(e), "java_jdk_download": JAVA_JDK_LINK}), 500
    except Exception as e:
        return jsonify({"error": "start_failed", "message": str(e)}), 500
    return jsonify({"status": "started", "server_id": inst.server_id, "pid": inst.process.pid}), 200

@app.route("/servers/<server_id>/stop", methods=["POST"])
def stop_server(server_id):
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404
    try:
        inst.stop()
    except Exception as e:
        return jsonify({"error": "stop_failed", "message": str(e)}), 500
    return jsonify({"status": "stopped", "server_id": server_id}), 200

@app.route("/servers/<server_id>/restart", methods=["POST"])
def restart_server(server_id):
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404
    data = request.get_json(silent=True) or {}
    memory = data.get("memory", "1G")
    java_args = data.get("java_args")
    try:
        inst.stop()
        time.sleep(1)
        if not java_exists():
            return jsonify({
                "error": "java_not_found",
                "message": "Java runtime not found on server host",
                "java_jdk_download": JAVA_JDK_LINK
            }), 500
        inst.start(java_args=java_args, memory=memory)
    except Exception as e:
        return jsonify({"error": "restart_failed", "message": str(e)}), 500
    return jsonify({"status": "restarted", "server_id": server_id, "pid": inst.process.pid}), 200

@app.route("/servers/<server_id>/kill", methods=["POST"])
def kill_server(server_id):
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404
    try:
        inst.kill()
    except Exception as e:
        return jsonify({"error": "kill_failed", "message": str(e)}), 500
    return jsonify({"status": "killed", "server_id": server_id}), 200

@app.route("/servers/<server_id>/delete", methods=["DELETE"])
def delete_server(server_id):
    try:
        sid = sanitize_server_id(server_id)
        inst = ensure_server_loaded(sid)
    except FileNotFoundError:
        sid = sanitize_server_id(server_id)
        server_dir = get_server_dir(sid)
        if not os.path.isdir(server_dir):
            return jsonify({"error": "not_found", "message": "Server not found"}), 404
        inst = None

    try:
        if inst:
            inst.stop()
    except Exception:
        pass
    with servers_lock:
        servers.pop(sid, None)
    server_dir = get_server_dir(sid)
    try:
        shutil.rmtree(server_dir)
    except Exception as e:
        return jsonify({"error": "delete_failed", "message": str(e)}), 500
    return jsonify({"status": "deleted", "server_id": sid}), 200

@app.route("/servers/<server_id>/console", methods=["GET"])
def get_console(server_id):
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404

    last_n = int(request.args.get("last_n", 200))
    lines = inst.get_console_lines(last_n=last_n)
    return jsonify({"server_id": server_id, "lines": lines}), 200

@app.route("/servers/<server_id>/console/stream", methods=["GET"])
def stream_console(server_id):
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404

    def event_stream(instance: ServerInstance):
        last_index = 0
        with instance.lock:
            existing = list(instance.console_lines)
        for line in existing:
            yield f"data: {json.dumps({'line': line})}\n\n"
        last_index = len(existing)
        while True:
            with instance.lock:
                all_lines = list(instance.console_lines)
            if len(all_lines) > last_index:
                for ln in all_lines[last_index:]:
                    yield f"data: {json.dumps({'line': ln})}\n\n"
                last_index = len(all_lines)
            if not instance.alive and (instance.process is None or instance.process.poll() is not None):
                break
            time.sleep(0.2)
        yield f"data: {json.dumps({'line': '[console stream ended]'})}\n\n"

    return Response(stream_with_context(event_stream(inst)), mimetype="text/event-stream")

@app.route("/servers/<server_id>/command", methods=["POST"])
def send_command(server_id):
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404
    data = request.get_json(silent=True) or {}
    cmd = data.get("command")
    if not cmd:
        return jsonify({"error": "missing_command", "message": "No command provided"}), 400
    try:
        inst.send_command(cmd)
    except Exception as e:
        return jsonify({"error": "send_failed", "message": str(e)}), 500
    return jsonify({"status": "sent", "command": cmd}), 200

@app.route("/servers/<server_id>/status", methods=["GET"])
def server_status(server_id):
    try:
        inst = ensure_server_loaded(server_id)
    except FileNotFoundError:
        return jsonify({"error": "not_found", "message": "Server not found"}), 404
    return jsonify(inst.status()), 200

@app.route("/servers", methods=["GET"])
def list_servers():
    items = []
    for name in os.listdir(SERVERS_DIR):
        p = os.path.join(SERVERS_DIR, name)
        if os.path.isdir(p):
            with servers_lock:
                if name not in servers:
                    servers[name] = ServerInstance(name, p)
                inst = servers[name]
            items.append({"server_id": name, "path": p, "running": inst.status().get("running", False)})
    return jsonify({"servers": items}), 200

@app.route("/download/<server_id>/<path:filename>", methods=["GET"])
def download_file(server_id, filename):
    sid = sanitize_server_id(server_id)
    server_dir = get_server_dir(sid)
    if not os.path.isdir(server_dir):
        return jsonify({"error":"not_found","message":"Server not found"}), 404
    safe_fn = os.path.basename(filename)
    return send_from_directory(server_dir, safe_fn, as_attachment=True)

def shutdown_server():
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()

@app.route("/shutdown", methods=["POST"])
def shutdown():
    with servers_lock:
        for inst in list(servers.values()):
            try:
                inst.stop()
            except Exception:
                pass
    shutdown_server()
    return jsonify({"status": "shutting_down"}), 200

if __name__ == "__main__":
    print("Starting Minecraft Server Manager backend on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True, threaded=True)