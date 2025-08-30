#!/usr/bin/env python3
"""
server_gui.py

Tkinter GUI controller for your Minecraft Server Manager Flask backend.
- Fetches servers from GET /servers
- Start/Stop/Restart/Kill/Delete via POST/DELETE endpoints
- Sends commands to /servers/<id>/command
- Streams console via /servers/<id>/console/stream (SSE-like)
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from tkinter import font as tkfont
import threading
import requests
import time
import json

# Change if your backend is on a different host/port
API_URL = "http://127.0.0.1:5000"

# ---------- RoundedButton from your provided code (slightly adapted) ----------
class RoundedButton(tk.Canvas):
    def __init__(self, parent, width, height, cornerradius, padding,
                 bg="#e0e0e0", fg="#000000", hoverbg="#d0d0d0",
                 text="", command=None, font=None, *args, **kwargs):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bg=parent["bg"], *args, **kwargs)
        self.width = width
        self.height = height
        self.corner_radius = cornerradius
        self.padding = padding
        self.bg = bg
        self.fg = fg
        self.hoverbg = hoverbg
        self.text = text
        self.command = command
        self.font = font or ("Segoe UI", 10)

        self.rect = self.create_rounded_rect(0, 0, width, height, self.corner_radius,
                                             fill=self.bg, outline="#b0b0b0")
        self.label = self.create_text(width//2, height//2, text=self.text, fill=self.fg, font=self.font)

        # Bind hover and click events
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)
        self.tag_bind(self.rect, "<Enter>", self.on_enter)
        self.tag_bind(self.rect, "<Leave>", self.on_leave)
        self.tag_bind(self.rect, "<Button-1>", self.on_click)
        self.tag_bind(self.label, "<Enter>", self.on_enter)
        self.tag_bind(self.label, "<Leave>", self.on_leave)
        self.tag_bind(self.label, "<Button-1>", self.on_click)

    def create_rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1+r, y1,
            x2-r, y1,
            x2, y1,
            x2, y1+r,
            x2, y2-r,
            x2, y2,
            x2-r, y2,
            x1+r, y2,
            x1, y2,
            x1, y2-r,
            x1, y1+r,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=36, **kwargs)

    def on_enter(self, event):
        self.itemconfig(self.rect, fill=self.hoverbg)

    def on_leave(self, event):
        self.itemconfig(self.rect, fill=self.bg)

    def on_click(self, event):
        if self.command:
            # run command in main thread (command should spawn its own threads/network calls)
            try:
                self.command()
            except Exception as e:
                print("RoundedButton command error:", e)

    def configure_state(self, enabled=True):
        # Simple visual disable: dim colors and ignore clicks by removing the command
        if not enabled:
            self.itemconfig(self.rect, fill="#e6e6e6")
            self.itemconfig(self.label, fill="#a0a0a0")
            self.command = None
        else:
            self.itemconfig(self.rect, fill=self.bg)
            self.itemconfig(self.label, fill=self.fg)

# ---------- Main GUI ----------
class ServerManagerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Server Control Panel")
        self.geometry("900x620")
        self.minsize(720, 480)
        self.configure(bg="#f5f5f5")

        # fonts
        self.header_font = tkfont.Font(family="Segoe UI", size=18, weight="bold")
        self.table_header_font = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.table_font = tkfont.Font(family="Segoe UI", size=10)
        self.status_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        # model
        self.servers = []  # will be fetched from backend
        self.selected_server_id = None

        # console stream control
        self.console_thread = None
        self.stop_console_flag = threading.Event()

        # build UI
        self._build_header()
        self._build_main_area()
        self._build_footer()

        # load servers initially
        self.load_servers()

        # graceful shutdown
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_header(self):
        header = tk.Frame(self, bg="#e1e1e1", height=56)
        header.pack(fill="x", side="top")
        title = tk.Label(header, text="Server Control Panel", bg="#e1e1e1", fg="#222222", font=self.header_font)
        title.pack(padx=20, pady=12, anchor="w")

    def _build_main_area(self):
        main = tk.Frame(self, bg=self["bg"])
        main.pack(fill="both", expand=True, padx=16, pady=(8, 12))

        # Left column: servers list + actions
        left = tk.Frame(main, bg=self["bg"])
        left.pack(side="left", fill="y", padx=(0, 10))

        # Refresh button
        refresh_btn = RoundedButton(left, width=110, height=34, cornerradius=10, padding=3,
                                    bg="#1976d2", fg="#ffffff", hoverbg="#1565c0",
                                    text="Refresh", font=("Segoe UI", 10, "bold"), command=self.load_servers)
        refresh_btn.pack(padx=4, pady=(4, 10), anchor="e")

        # Servers container (scrollable)
        container = tk.Frame(left, bg=self["bg"])
        container.pack(fill="y", expand=True)

        self.canvas = tk.Canvas(container, bg=self["bg"], highlightthickness=0, width=360)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.list_frame = tk.Frame(self.canvas, bg=self["bg"])
        self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        # Table headers
        hdr_name = tk.Label(self.list_frame, text="Server Name", bg=self["bg"], fg="#444444", font=self.table_header_font,
                            anchor="w")
        hdr_status = tk.Label(self.list_frame, text="Status", bg=self["bg"], fg="#444444", font=self.table_header_font)
        hdr_actions = tk.Label(self.list_frame, text="Actions", bg=self["bg"], fg="#444444", font=self.table_header_font)

        hdr_name.grid(row=0, column=0, sticky="w", padx=(12,0), pady=(6, 10))
        hdr_status.grid(row=0, column=1, padx=8, pady=(6, 10))
        hdr_actions.grid(row=0, column=2, padx=(8,12), pady=(6, 10))
        self.list_frame.grid_columnconfigure(0, weight=3)
        self.list_frame.grid_columnconfigure(1, weight=1)
        self.list_frame.grid_columnconfigure(2, weight=2)

        # Right column: console and command entry
        right = tk.Frame(main, bg=self["bg"])
        right.pack(side="right", fill="both", expand=True)

        # Status label and selected server
        self.status_label = tk.Label(right, text="No server selected.", bg=self["bg"], fg="#333333",
                                     font=self.table_font, anchor="w")
        self.status_label.pack(fill="x", padx=6, pady=(0,6))

        # Console display
        console_frame = tk.Frame(right, bg="#1e1e1e", bd=2, relief=tk.SUNKEN)
        console_frame.pack(fill="both", expand=True, padx=(0,0), pady=(0,8))
        self.console_text = scrolledtext.ScrolledText(console_frame, bg="#000000", fg="#a8ff9e",
                                                      font=("Consolas", 11), state=tk.DISABLED, wrap=tk.WORD)
        self.console_text.pack(fill="both", expand=True, padx=6, pady=6)

        # Command entry
        cmd_frame = tk.Frame(right, bg=self["bg"])
        cmd_frame.pack(fill="x", pady=(0,6))
        self.command_var = tk.StringVar()
        self.command_entry = ttk.Entry(cmd_frame, textvariable=self.command_var, font=("Segoe UI", 11))
        self.command_entry.pack(side="left", fill="x", expand=True, padx=(6,6))
        self.command_entry.bind("<Return>", self._on_send_command)

        send_btn = RoundedButton(cmd_frame, width=92, height=34, cornerradius=8, padding=3,
                                 bg="#00adb5", fg="#ffffff", hoverbg="#019ca1",
                                 text="Send", font=("Segoe UI", 10, "bold"), command=self._on_send_command)
        send_btn.pack(side="left", padx=(0,10))

    def _build_footer(self):
        footer = tk.Frame(self, bg=self["bg"])
        footer.pack(fill="x", side="bottom", padx=16, pady=(0,12))

        # control buttons single-row for start/stop/restart/kill/delete
        btn_frame = tk.Frame(footer, bg=self["bg"])
        btn_frame.pack(anchor="e", fill="x")

        self.btn_start = RoundedButton(btn_frame, width=90, height=34, cornerradius=8, padding=3,
                                       bg="#d1e7dd", fg="#0f5132", hoverbg="#badbcc",
                                       text="Start", font=("Segoe UI", 10, "bold"), command=self._on_start)
        self.btn_start.pack(side="right", padx=6)

        self.btn_stop = RoundedButton(btn_frame, width=90, height=34, cornerradius=8, padding=3,
                                      bg="#f8d7da", fg="#842029", hoverbg="#f1b0b7",
                                      text="Stop", font=("Segoe UI", 10, "bold"), command=self._on_stop)
        self.btn_stop.pack(side="right", padx=6)

        self.btn_restart = RoundedButton(btn_frame, width=100, height=34, cornerradius=8, padding=3,
                                         bg="#ffd54f", fg="#5d4037", hoverbg="#ffca28",
                                         text="Restart", font=("Segoe UI", 10, "bold"), command=self._on_restart)
        self.btn_restart.pack(side="right", padx=6)

        self.btn_kill = RoundedButton(btn_frame, width=90, height=34, cornerradius=8, padding=3,
                                      bg="#ef9a9a", fg="#7f0000", hoverbg="#ef6c6c",
                                      text="Kill", font=("Segoe UI", 10, "bold"), command=self._on_kill)
        self.btn_kill.pack(side="right", padx=6)

        self.btn_delete = RoundedButton(btn_frame, width=100, height=34, cornerradius=8, padding=3,
                                        bg="#b71c1c", fg="#ffffff", hoverbg="#7f0000",
                                        text="Delete", font=("Segoe UI", 10, "bold"), command=self._on_delete)
        self.btn_delete.pack(side="right", padx=6)

        # start disabled until a server is selected
        self._set_action_buttons_enabled(False)

    # ---------- server list population ----------
    def load_servers(self):
        """Fetch the servers list from the backend and populate the left table."""
        try:
            r = requests.get(f"{API_URL}/servers", timeout=6)
            r.raise_for_status()
            data = r.json()
            self.servers = data.get("servers", [])
            # repopulate rows
            self._populate_server_rows()
            # preserve selection if possible
            if self.selected_server_id:
                found = any(s.get("server_id") == self.selected_server_id for s in self.servers)
                if not found:
                    self.selected_server_id = None
                    self.status_label.config(text="No server selected.")
                    self._set_action_buttons_enabled(False)
            # if only one server, auto-select it
            if not self.selected_server_id and len(self.servers) == 1:
                self._select_server(self.servers[0]["server_id"])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load servers:\n{e}")
            self.servers = []
            self._populate_server_rows()

    def _populate_server_rows(self):
        # remove rows with row>0 (keep header)
        for widget in list(self.list_frame.grid_slaves()):
            info = widget.grid_info()
            if int(info["row"]) > 0:
                widget.destroy()

        for idx, s in enumerate(self.servers, start=1):
            sid = s.get("server_id")
            name = s.get("server_id")  # backend doesn't provide friendly name by default; you can change
            running = s.get("running", False)

            name_lbl = tk.Label(self.list_frame, text=name, bg=self["bg"], fg="#222222",
                                font=self.table_font, anchor="w")
            name_lbl.grid(row=idx, column=0, sticky="w", padx=(12,0), pady=8)

            # status
            status_frame = tk.Frame(self.list_frame, bg=self["bg"])
            status_frame.grid(row=idx, column=1, padx=8, pady=8)
            color = "#4caf50" if running else "#e53935"
            circle = tk.Canvas(status_frame, width=14, height=14, highlightthickness=0, bg=self["bg"])
            circle.pack(side="left", padx=(0,6))
            circle.create_oval(2, 2, 12, 12, fill=color, outline=color)
            status_lbl = tk.Label(status_frame, text="Running" if running else "Stopped", fg=color,
                                  bg=self["bg"], font=self.status_font)
            status_lbl.pack(side="left")

            # actions column (select button)
            actions_frame = tk.Frame(self.list_frame, bg=self["bg"])
            actions_frame.grid(row=idx, column=2, padx=(8,12), pady=8)

            select_btn = RoundedButton(actions_frame, width=84, height=30, cornerradius=8, padding=3,
                                       bg="#1976d2", fg="#fff", hoverbg="#1565c0",
                                       text="Select", font=("Segoe UI", 9, "bold"),
                                       command=lambda sid=sid: self._select_server(sid))
            select_btn.pack(side="left")

        # ensure scrollregion updated
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _select_server(self, server_id):
        """Select a server and start streaming its console."""
        self.selected_server_id = server_id
        self.status_label.config(text=f"Selected: {server_id} — fetching status...")
        self._set_action_buttons_enabled(False)
        # update status and action buttons
        self._update_server_status()
        # clear console and start streaming
        self._clear_console()
        self._start_console_stream()

    def _update_server_status(self):
        if not self.selected_server_id:
            return
        try:
            r = requests.get(f"{API_URL}/servers/{self.selected_server_id}/status", timeout=4)
            r.raise_for_status()
            status = r.json()
            running = status.get("running", False)
            self.status_label.config(text=f"Selected: {self.selected_server_id}  —  running: {running}")
            self._set_action_buttons_enabled(True, running=running)
        except Exception as e:
            self.status_label.config(text=f"Selected: {self.selected_server_id}  —  status unavailable")
            self._set_action_buttons_enabled(False)

    def _set_action_buttons_enabled(self, enabled: bool, running: bool = False):
        # enable/disable action buttons and set appropriate commands
        if not enabled:
            self.btn_start.configure_state(False)
            self.btn_stop.configure_state(False)
            self.btn_restart.configure_state(False)
            self.btn_kill.configure_state(False)
            self.btn_delete.configure_state(False)
        else:
            # Start enabled if not running, Stop/Restart/Kill enabled if running
            self.btn_start.command = self._on_start if not running else None
            self.btn_stop.command = self._on_stop if running else None
            self.btn_restart.command = self._on_restart if running else self._on_restart
            self.btn_kill.command = self._on_kill if running else None
            self.btn_delete.command = self._on_delete
            self.btn_start.configure_state(not running)
            self.btn_stop.configure_state(running)
            self.btn_restart.configure_state(True)
            self.btn_kill.configure_state(running)
            self.btn_delete.configure_state(True)

    # ---------- console streaming ----------
    def _start_console_stream(self):
        # stop old thread if running
        self._stop_console_stream()
        if not self.selected_server_id:
            return
        self.stop_console_flag.clear()
        self.console_thread = threading.Thread(target=self._console_stream_worker, daemon=True)
        self.console_thread.start()

    def _stop_console_stream(self):
        if self.console_thread and self.console_thread.is_alive():
            self.stop_console_flag.set()
            # No blocking join here; thread will end soon
            self.console_thread = None

    def _console_stream_worker(self):
        url = f"{API_URL}/servers/{self.selected_server_id}/console/stream"
        try:
            with requests.get(url, stream=True, timeout=10) as r:
                r.raise_for_status()
                buffer = ""
                for chunk in r.iter_content(chunk_size=1024, decode_unicode=True):
                    if self.stop_console_flag.is_set():
                        break
                    if not chunk:
                        continue
                    buffer += chunk
                    # SSE-style messages separated by double newline
                    while "\n\n" in buffer:
                        part, buffer = buffer.split("\n\n", 1)
                        part = part.strip()
                        if part.startswith("data:"):
                            try:
                                payload = part[5:].strip()
                                data = json.loads(payload)
                                line = data.get("line", "")
                                self._append_console_line(line)
                            except Exception:
                                # fallback: append raw part
                                self._append_console_line(part)
                # final flush
                if buffer.strip():
                    for line in buffer.splitlines():
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                                self._append_console_line(data.get("line", ""))
                            except Exception:
                                self._append_console_line(line)
        except requests.exceptions.RequestException as e:
            self._append_console_line(f"[Console stream error: {e}]")
        except Exception as e:
            self._append_console_line(f"[Console stream unexpected error: {e}]")

    def _append_console_line(self, text):
        # append must run on main thread
        def append():
            self.console_text.config(state=tk.NORMAL)
            self.console_text.insert(tk.END, text + "\n")
            self.console_text.see(tk.END)
            self.console_text.config(state=tk.DISABLED)
        self.after(0, append)

    def _clear_console(self):
        self.console_text.config(state=tk.NORMAL)
        self.console_text.delete("1.0", tk.END)
        self.console_text.config(state=tk.DISABLED)

    # ---------- control actions ----------
    def _request_action(self, method, path, json_data=None):
        """Helper to call backend; returns response json or raises."""
        url = f"{API_URL}{path}"
        try:
            if method == "POST":
                r = requests.post(url, json=json_data, timeout=10)
            elif method == "DELETE":
                r = requests.delete(url, timeout=10)
            elif method == "GET":
                r = requests.get(url, timeout=10)
            else:
                raise ValueError("Unsupported method")
            r.raise_for_status()
            try:
                return r.json()
            except Exception:
                return {}
        except Exception as e:
            raise

    def _on_start(self):
        if not self.selected_server_id:
            return
        try:
            self._request_action("POST", f"/servers/{self.selected_server_id}/start", json_data={"memory": "1G"})
            self._append_console_line(f"[INFO] Sent start to {self.selected_server_id}")
            time.sleep(0.2)
            self._update_server_status()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start server:\n{e}")

    def _on_stop(self):
        if not self.selected_server_id:
            return
        try:
            self._request_action("POST", f"/servers/{self.selected_server_id}/stop")
            self._append_console_line(f"[INFO] Sent stop to {self.selected_server_id}")
            time.sleep(0.2)
            self._update_server_status()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to stop server:\n{e}")

    def _on_restart(self):
        if not self.selected_server_id:
            return
        try:
            self._request_action("POST", f"/servers/{self.selected_server_id}/restart", json_data={"memory": "1G"})
            self._append_console_line(f"[INFO] Sent restart to {self.selected_server_id}")
            time.sleep(0.2)
            self._update_server_status()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to restart server:\n{e}")

    def _on_kill(self):
        if not self.selected_server_id:
            return
        if not messagebox.askyesno("Confirm Kill", f"Kill process for {self.selected_server_id}?"):
            return
        try:
            self._request_action("POST", f"/servers/{self.selected_server_id}/kill")
            self._append_console_line(f"[INFO] Sent kill to {self.selected_server_id}")
            time.sleep(0.2)
            self._update_server_status()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to kill server:\n{e}")

    def _on_delete(self):
        if not self.selected_server_id:
            return
        if not messagebox.askyesno("Confirm Delete", f"DELETE server folder for {self.selected_server_id}? This is irreversible."):
            return
        try:
            self._stop_console_stream()
            self._request_action("DELETE", f"/servers/{self.selected_server_id}/delete")
            self._append_console_line(f"[INFO] Deleted {self.selected_server_id}")
            # refresh list and UI
            self.selected_server_id = None
            self.load_servers()
            self._clear_console()
            self.status_label.config(text="No server selected.")
            self._set_action_buttons_enabled(False)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete server:\n{e}")

    def _on_send_command(self, event=None):
        cmd = self.command_var.get().strip()
        if not cmd or not self.selected_server_id:
            return
        try:
            self._request_action("POST", f"/servers/{self.selected_server_id}/command", json_data={"command": cmd})
            self._append_console_line(f"> {cmd}")
            self.command_var.set("")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to send command:\n{e}")

    # ---------- cleanup ----------
    def on_close(self):
        self._stop_console_stream()
        # small wait to let thread exit
        time.sleep(0.15)
        self.destroy()

if __name__ == "__main__":
    app = ServerManagerGUI()
    app.mainloop()