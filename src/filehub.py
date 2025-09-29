import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import http.server
import socketserver
import os
import threading
import urllib.parse
from email.parser import BytesParser
import html
import json
import shutil
import socket
import sys
import io
import requests
import time
from tkinterdnd2 import DND_FILES, TkinterDnD

# --- Global State ---
STATE_LOCK = threading.Lock()
ACTIVE_CLIENTS = {}
PENDING_FILES = {}
USER_FILES_DIR = 'user_files'
PUBLIC_FILES_DIR = 'public_files'

# --- Server Class (The Backend) ---
class FileHubRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Redirects server logs to our GUI console
        sys.stdout.write("%s - - [%s] %s\n" %
                         (self.address_string(),
                          self.log_date_time_string(),
                          format%args))

    def _get_client_id(self): return self.client_address[0]
    
    def _update_client_activity(self):
        client_ip = self._get_client_id()
        with STATE_LOCK:
            if client_ip not in ACTIVE_CLIENTS: ACTIVE_CLIENTS[client_ip] = {'name': None}

    def _send_html_response(self, html_content, code=200):
        try:
            self.send_response(code); self.send_header('Content-type', 'text/html'); self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
        except (socket.error, ConnectionResetError, BrokenPipeError):
            print(f"Client {self.client_address} disconnected abruptly.")

    def _serve_file(self, filepath):
        if not os.path.exists(filepath): self._send_html_response("<h2>404 Not Found</h2>", 404); return
        try:
            self.send_response(200); self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{os.path.basename(filepath)}"'); self.send_header('Content-Length', str(os.path.getsize(filepath))); self.end_headers()
            with open(filepath, 'rb') as f: shutil.copyfileobj(f, self.wfile)
        except Exception as e: print(f"Error serving file: {e}")
    
    def do_GET(self):
        self._update_client_activity()
        parsed_path, client_ip = urllib.parse.urlparse(self.path), self._get_client_id()
        with STATE_LOCK: client_name = ACTIVE_CLIENTS.get(client_ip, {}).get('name')
        if parsed_path.path == '/check_updates': self._handle_check_updates(); return
        if client_name is None and parsed_path.path != '/favicon.ico': self._serve_set_name_page(); return
        if parsed_path.path.startswith('/download/'): self._handle_download(parsed_path)
        else: self._serve_main_page()
    
    def do_POST(self):
        self._update_client_activity()
        if self.path == '/set_name': self._handle_set_name()
        elif self.path == '/leave': self._handle_leave()
        else: self._handle_file_upload()
    
    def _handle_check_updates(self):
        client_ip = self._get_client_id()
        has_updates = False
        with STATE_LOCK:
            if client_ip in PENDING_FILES and PENDING_FILES[client_ip]: has_updates = True
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps({"updates": has_updates}).encode('utf-8'))

    def _serve_set_name_page(self):
        self._send_html_response(f"""<!DOCTYPE html><html><head><title>Welcome!</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{{display:flex;justify-content:center;align-items:center;height:100vh;background-color:#f8f9fa;}}</style></head><body><div class="card text-center shadow-sm" style="width: 25rem;"><div class="card-body"><h2 class="card-title">Welcome to File Hub!</h2><p class="card-text">Please choose a name to identify yourself.</p><form action="/set_name" method="post"><div class="mb-3"><input type="text" name="username" class="form-control" placeholder="Enter your name..." autofocus required></div><button type="submit" class="btn btn-primary">Join Hub</button></form></div></div></body></html>""")

    def _handle_set_name(self):
        post_data = self.rfile.read(int(self.headers['Content-Length'])).decode('utf-8')
        sanitized_name = html.escape(urllib.parse.parse_qs(post_data).get('username', [''])[0].strip())
        if sanitized_name:
            with STATE_LOCK: ACTIVE_CLIENTS[self._get_client_id()]['name'] = sanitized_name
            self.send_response(303); self.send_header('Location', '/'); self.end_headers()
        else: self._send_html_response("<h2>Invalid Name</h2>", 400)
    
    # --- THIS IS THE HEAVILY MODIFIED METHOD WITH THE SYNTAX FIX ---
    def _serve_main_page(self):
        client_ip = self._get_client_id()
        with STATE_LOCK:
            my_name = ACTIVE_CLIENTS.get(client_ip, {}).get('name', client_ip)
            pending_files_html = '<li class="list-group-item">You have no new files.</li>'
            if client_ip in PENDING_FILES and PENDING_FILES[client_ip]:
                links = [f"<li class='list-group-item d-flex justify-content-between align-items-center'><a href='/download/{urllib.parse.quote(f['filepath'])}'>{html.escape(f['filename'])}</a> <span class='badge bg-secondary rounded-pill'>from {html.escape(f'{ACTIVE_CLIENTS.get(f["sender"], {}).get("name", f["sender"])} ({f["sender"]})')}</span></li>" for f in PENDING_FILES[client_ip]]
                pending_files_html = "".join(links)
            recipients_html = "<option value='Public Folder'>Public Folder (Shared)</option>" + "".join([f"<option value='{ip}'>{html.escape(data['name'])} ({ip})</option>" for ip, data in sorted(ACTIVE_CLIENTS.items()) if ip != client_ip and data.get('name')])
            public_files_html = '<li class="list-group-item text-muted">No public files.</li>'
            public_files = sorted(os.listdir(PUBLIC_FILES_DIR))
            if public_files:
                public_files_html = "".join([f"<li class='list-group-item'><a href='/download/{urllib.parse.quote(os.path.join(PUBLIC_FILES_DIR, f))}'>{html.escape(f)}</a></li>" for f in public_files])

        self._send_html_response(f"""
        <!DOCTYPE html><html lang="en"><head>
            <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>File Hub</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
            <style>
                #drop-zone.dragover {{ border-color: #0d6efd; background-color: #e9f2ff; }}
                .progress {{ height: 25px; font-size: 1rem; }}
            </style>
        </head><body class="bg-light">
        <div class="container mt-4 mb-5">
            <div class="d-flex justify-content-between align-items-center mb-3"><h1 class="h3">File Hub 🌐</h1><form action="/leave" method="post"><button type="submit" class="btn btn-sm btn-outline-danger">Leave Hub</button></form></div>
            <p class="lead">Your Name: <strong>{html.escape(my_name)} ({client_ip})</strong></p>
            <div class="row">
                <div class="col-md-8">
                    <div class="card mb-4 shadow-sm"><div class="card-header"><h5>📥 Your Incoming Files</h5></div><ul class="list-group list-group-flush">{pending_files_html}</ul></div>
                    <div class="card mb-4 shadow-sm"><div class="card-header"><h5>📤 Send Files</h5></div><div class="card-body"><div class="mb-3"><label for="recipient" class="form-label">1. Choose a recipient:</label><select id="recipient" name="recipient" class="form-select">{recipients_html}</select></div><div class="mb-3"><label class="form-label">2. Drag & Drop Files Below or Choose Manually:</label><div id="drop-zone" class="p-5 border rounded text-center text-muted">Drag & Drop files here</div><input type="file" id="file-input" multiple class="d-none"><div class="text-center mt-2"><button type="button" class="btn btn-secondary" onclick="document.getElementById('file-input').click();">Or Choose Files...</button></div></div><div class="mb-3"><h6>Selected Files:</h6><ul id="file-list" class="list-group"><li class="list-group-item text-muted">No files selected.</li></ul></div><div class="d-grid"><button id="upload-btn" onclick="uploadFiles();" class="btn btn-success">Send Files</button></div><div id="progress-container" class="mt-3" style="display:none;"><div class="progress"><div id="progress-bar" class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width: 0%;">0%</div></div><small id="progress-text" class="text-muted"></small></div></div></div>
                </div>
                <div class="col-md-4">
                    <div class="card shadow-sm"><div class="card-header"><h5>🌍 Public Files</h5></div><ul class="list-group list-group-flush">{public_files_html}</ul></div>
                </div>
            </div>
        </div>
        <footer class="d-flex flex-wrap justify-content-between align-items-center p-4 my-4 border-top">
        <div class="col-md-4 d-flex align-items-center"><span class="text-muted">Developed by Krishna Kumar</span></div>
        </footer>
        <script>
            const dropZone = document.getElementById('drop-zone');
            const fileInput = document.getElementById('file-input');
            const fileList = document.getElementById('file-list');
            const uploadBtn = document.getElementById('upload-btn');
            const recipientSelect = document.getElementById('recipient');
            const progressContainer = document.getElementById('progress-container');
            const progressBar = document.getElementById('progress-bar');
            const progressText = document.getElementById('progress-text');
            let filesToUpload = [];

            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {{
                document.body.addEventListener(eventName, e => {{ e.preventDefault(); e.stopPropagation(); }}, false);
            }});
            ['dragenter', 'dragover'].forEach(eventName => {{
                dropZone.addEventListener(eventName, () => dropZone.classList.add('dragover'), false);
            }});
            ['dragleave', 'drop'].forEach(eventName => {{
                dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'), false);
            }});

            dropZone.addEventListener('drop', e => {{
                filesToUpload = [...e.dataTransfer.files];
                updateFileList();
            }}, false);

            fileInput.addEventListener('change', e => {{
                filesToUpload = [...e.target.files];
                updateFileList();
            }});

            function updateFileList() {{
                fileList.innerHTML = '';
                if (filesToUpload.length === 0) {{
                    fileList.innerHTML = '<li class="list-group-item text-muted">No files selected.</li>';
                    return;
                }}
                filesToUpload.forEach(file => {{
                    const listItem = document.createElement('li');
                    listItem.className = 'list-group-item';
                    const fileSizeKB = (file.size / 1024).toFixed(2);
                    listItem.textContent = `${{file.name}} (${{fileSizeKB}} KB)`;
                    fileList.appendChild(listItem);
                }});
            }}

            function uploadFiles() {{
                if (filesToUpload.length === 0) {{
                    alert('Please select files to upload.');
                    return;
                }}
                const formData = new FormData();
                formData.append('recipient', recipientSelect.value);
                filesToUpload.forEach(file => formData.append('filetoupload', file));
                
                const xhr = new XMLHttpRequest();
                let lastLoaded = 0;
                let lastTime = Date.now();

                xhr.upload.addEventListener('progress', e => {{
                    if (e.lengthComputable) {{
                        const now = Date.now();
                        const timeDiff = (now - lastTime) / 1000 || 1; // Avoid division by zero
                        const bytesDiff = e.loaded - lastLoaded;
                        const speed = bytesDiff / timeDiff;
                        const eta = (e.total - e.loaded) / speed;
                        
                        const percentComplete = (e.loaded / e.total * 100);
                        const speedMBs = (speed / 1024 / 1024);
                        
                        progressBar.style.width = `${{percentComplete.toFixed(2)}}%`;
                        progressBar.textContent = `${{percentComplete.toFixed(2)}}%`;
                        progressText.textContent = `Speed: ${{speedMBs.toFixed(2)}} MB/s - ETA: ${{eta.toFixed(1)}}s`;
                        
                        lastLoaded = e.loaded;
                        lastTime = now;
                    }}
                }});

                xhr.addEventListener('load', () => {{
                    _updateProgressBar('bg-success', 'Upload successful! Refreshing...');
                    setTimeout(() => window.location.reload(), 1500);
                }});

                xhr.addEventListener('error', () => {{
                    _updateProgressBar('bg-danger', 'Upload failed. Please try again.');
                    uploadBtn.disabled = false;
                }});

                xhr.open('POST', '/');
                xhr.send(formData);
                progressContainer.style.display = 'block';
                uploadBtn.disabled = true;
            }}
            
            function _updateProgressBar(className, text) {{
                progressBar.classList.remove('progress-bar-animated');
                progressBar.className = 'progress-bar'; // Reset classes
                progressBar.classList.add(className);
                progressText.textContent = text;
            }}

            setInterval(async () => {{
                try {{
                    const response = await fetch('/check_updates');
                    const data = await response.json();
                    if (data.updates) {{
                        window.location.reload();
                    }}
                }} catch (e) {{
                    // Do nothing on error, just continue polling
                }}
            }}, 5000);
        </script>
        </body></html>
        """)
    def _handle_leave(self):
        with STATE_LOCK:
            if self._get_client_id() in ACTIVE_CLIENTS: del ACTIVE_CLIENTS[self._get_client_id()]
        self._send_html_response("<h2>You have left the hub.</h2>")
    def _handle_download(self, parsed_path):
        requested_path = os.path.abspath(urllib.parse.unquote(parsed_path.path.split('/', 2)[-1]))
        safe_user_dir, safe_public_dir = os.path.abspath(USER_FILES_DIR), os.path.abspath(PUBLIC_FILES_DIR)
        if requested_path.startswith((safe_user_dir, safe_public_dir)):
            self._serve_file(requested_path)
            if requested_path.startswith(safe_user_dir):
                client_ip = self._get_client_id()
                with STATE_LOCK:
                    if client_ip in PENDING_FILES: PENDING_FILES[client_ip] = [f for f in PENDING_FILES[client_ip] if f['filepath'] != requested_path]
                os.remove(requested_path)
        else: self._send_html_response("<h2>403 Forbidden</h2>", 403)
    def _handle_file_upload(self):
        client_ip = self._get_client_id()
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)
        message = BytesParser().parsebytes(f"Content-Type: {self.headers['Content-Type']}\n\n".encode() + body)
        recipient_ip = None; file_parts = []
        for part in message.get_payload():
            if part.get_param('name', header='Content-Disposition') == 'recipient': recipient_ip = part.get_payload(decode=True).decode()
            elif part.get_param('name', header='Content-Disposition') == 'filetoupload' and part.get_filename():
                file_parts.append(part)
        if not recipient_ip or not file_parts: self._send_html_response("<h2>400 Bad Request</h2>", 400); return
        for part in file_parts:
            filename = os.path.basename(part.get_filename()); file_data = part.get_payload(decode=True)
            if not filename or file_data is None: continue
            if recipient_ip == 'Public Folder':
                filepath = os.path.join(PUBLIC_FILES_DIR, filename)
                with open(filepath, 'wb') as f: f.write(file_data)
                print(f"[+] Public file '{filename}' uploaded by {client_ip}.")
            else:
                filepath = os.path.join(USER_FILES_DIR, recipient_ip, filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, 'wb') as f: f.write(file_data)
                with STATE_LOCK: PENDING_FILES.setdefault(recipient_ip, []).append({'filename': filename, 'filepath': filepath, 'sender': client_ip})
                print(f"[+] File '{filename}' from {client_ip} staged for {recipient_ip}.")
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
        self.wfile.write(json.dumps({'status': 'success'}).encode('utf-8'))
    # The _serve_host_page and _handle_delete methods are now obsolete and removed.

# --- GUI Application Class ---
class ConsoleRedirector(io.StringIO):
    def __init__(self, widget): self.widget = widget
    def write(self, s):
        self.widget.configure(state='normal'); self.widget.insert(tk.END, s); self.widget.see(tk.END); self.widget.configure(state='disabled')
    def flush(self): pass

class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("File Hub Control Panel"); self.geometry("1000x750")
        self.server_thread = None; self.httpd = None
        self.host_ip = None
        self.port_var = tk.StringVar(value="2604"); self.file_to_send = tk.StringVar(value="No file selected.")
        self.host_name_var = tk.StringVar(value="Host")
        self._create_widgets()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _create_widgets(self):
        main_frame = ttk.Frame(self, padding="10"); main_frame.pack(fill="both", expand=True)
        main_frame.rowconfigure(1, weight=1); main_frame.rowconfigure(2, weight=0); main_frame.columnconfigure(0, weight=1); main_frame.columnconfigure(1, weight=1); main_frame.columnconfigure(2, weight=1)
        top_frame = ttk.Frame(main_frame); top_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(top_frame, text="Your Name:").pack(side="left", padx=(0,5))
        self.name_entry = ttk.Entry(top_frame, textvariable=self.host_name_var, width=15); self.name_entry.pack(side="left", padx=(0,10))
        ttk.Label(top_frame, text="Port:").pack(side="left", padx=(0,5))
        self.port_entry = ttk.Entry(top_frame, textvariable=self.port_var, width=8); self.port_entry.pack(side="left", padx=(0,10))
        self.start_button = ttk.Button(top_frame, text="Start Server", command=self.start_server); self.start_button.pack(side="left")
        self.stop_button = ttk.Button(top_frame, text="Stop Server", state=tk.DISABLED, command=self.stop_server); self.stop_button.pack(side="left", padx=(5,0))
        self.status_label = ttk.Label(top_frame, text="Server Offline", font=("", 10, "bold"), foreground="red"); self.status_label.pack(side="right")
        self.panels_frame = ttk.Frame(main_frame); self.panels_frame.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.panels_frame.columnconfigure(0, weight=1); self.panels_frame.columnconfigure(1, weight=1); self.panels_frame.columnconfigure(2, weight=1); self.panels_frame.rowconfigure(0, weight=1)
        incoming_frame = ttk.LabelFrame(self.panels_frame, text="📥 My Incoming Files"); incoming_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        incoming_frame.rowconfigure(0, weight=1); incoming_frame.columnconfigure(0, weight=1)
        self.incoming_files_list = tk.Listbox(incoming_frame); self.incoming_files_list.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=5)
        ttk.Button(incoming_frame, text="Open", command=self._open_incoming_file).grid(row=1, column=0, sticky="ew")
        ttk.Button(incoming_frame, text="Delete", command=self._delete_incoming_file).grid(row=1, column=1, sticky="ew")
        public_frame = ttk.LabelFrame(self.panels_frame, text="🌍 Public Files"); public_frame.grid(row=0, column=1, sticky="nsew", padx=5)
        public_frame.rowconfigure(0, weight=1); public_frame.columnconfigure(0, weight=1)
        self.public_files_list = tk.Listbox(public_frame); self.public_files_list.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=5)
        ttk.Button(public_frame, text="Open", command=self._open_public_file).grid(row=1, column=0, sticky="ew")
        ttk.Button(public_frame, text="Delete", command=self._delete_public_file).grid(row=1, column=1, sticky="ew")
        send_frame = ttk.LabelFrame(self.panels_frame, text="📤 Send a File"); send_frame.grid(row=0, column=2, sticky="nsew", padx=(5, 0))
        send_frame.columnconfigure(0, weight=1); send_frame.rowconfigure(1, weight=1)
        send_frame.drop_target_register(DND_FILES); send_frame.dnd_bind('<<Drop>>', self._handle_drop)
        ttk.Label(send_frame, text="Recipients:").grid(row=0, column=0, sticky="w")
        self.clients_list = tk.Listbox(send_frame); self.clients_list.grid(row=1, column=0, sticky="nsew", pady=5)
        ttk.Button(send_frame, text="Select File(s)...", command=self._select_files_to_send).grid(row=2, column=0, sticky="ew", pady=(10,0))
        ttk.Label(send_frame, text="Or drag & drop files onto this panel.").grid(row=3, column=0, sticky="w")
        ttk.Label(send_frame, textvariable=self.file_to_send, wraplength=250).grid(row=4, column=0, sticky="w", pady=5)
        ttk.Button(send_frame, text="Send File(s)", command=self._send_files).grid(row=5, column=0, sticky="ew")
        for child in self.panels_frame.winfo_children():
            for widget in child.winfo_children(): widget.configure(state=tk.DISABLED)
        console_frame = ttk.LabelFrame(main_frame, text="Console Log"); console_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10,0))
        self.console = scrolledtext.ScrolledText(console_frame, state='disabled', height=8, wrap=tk.WORD, bg="#2B2B2B", fg="#A9B7C6"); self.console.pack(expand=True, fill="both")
        sys.stdout = ConsoleRedirector(self.console)

    def _set_panels_state(self, state):
        for child in self.panels_frame.winfo_children():
            for widget in child.winfo_children():
                if isinstance(widget, tk.Listbox): widget.configure(state=tk.NORMAL if state == "normal" else tk.DISABLED)
                else: widget.configure(state=state)

    def _handle_drop(self, event):
        filepaths = self.tk.splitlist(event.data);
        if filepaths: self._filepaths_to_send = filepaths; self.file_to_send.set(f"{len(filepaths)} file(s) dropped"); print(f"Dropped {len(filepaths)} file(s).")
    
    def start_server(self):
        try:
            port = int(self.port_var.get()); host_name = self.host_name_var.get().strip()
            if not host_name: messagebox.showerror("Error", "Host name cannot be empty."); return
        except ValueError: messagebox.showerror("Error", "Invalid port."); return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); self.host_ip = s.getsockname()[0]; s.close()
            with STATE_LOCK: ACTIVE_CLIENTS[self.host_ip] = {'name': html.escape(host_name)}
        except Exception as e: messagebox.showwarning("Warning", f"Could not determine local IP: {e}"); self.host_ip = "127.0.0.1"
        socketserver.TCPServer.allow_reuse_address = True
        try: self.httpd = socketserver.ThreadingTCPServer(("", port), FileHubRequestHandler)
        except OSError as e: print(f"ERROR: Could not start server on port {port}. {e}"); return
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True); self.server_thread.start()
        print(f"--- Server started on port {port} with host name '{host_name}' ---")
        self.status_label.config(text=f"Server Running on port {port}", foreground="green")
        self.start_button.config(state=tk.DISABLED); self.port_entry.config(state=tk.DISABLED); self.name_entry.config(state=tk.DISABLED); self.stop_button.config(state=tk.NORMAL)
        self._set_panels_state("normal")
        self.update_gui()

    def stop_server(self):
        if self.httpd:
            print("--- Shutting down server... ---")
            self.httpd.shutdown(); self.httpd.server_close(); self.server_thread.join()
            self.httpd = None; self.host_ip = None; print("Server stopped.")
        self.status_label.config(text="Server Offline", foreground="red")
        self.start_button.config(state=tk.NORMAL); self.port_entry.config(state=tk.NORMAL); self.name_entry.config(state=tk.NORMAL); self.stop_button.config(state=tk.DISABLED)
        self._set_panels_state("disabled")
        self.incoming_files_list.delete(0, tk.END); self.public_files_list.delete(0, tk.END); self.clients_list.delete(0, tk.END)

    def update_gui(self):
        if not self.httpd: return
        self.update_incoming_files(); self.update_public_files(); self.update_clients_list()
        self.after(2000, self.update_gui)

    def update_incoming_files(self):
        try:
            with STATE_LOCK: my_pending_files = PENDING_FILES.get(self.host_ip, [])
            actual_files = {f['filename'] for f in my_pending_files}
            if set(self.incoming_files_list.get(0, tk.END)) != actual_files:
                self.incoming_files_list.delete(0, tk.END)
                for f_info in sorted(my_pending_files, key=lambda x: x['filename']):
                    self.incoming_files_list.insert(tk.END, f_info['filename'])
        except Exception as e: print(f"GUI Error updating incoming files: {e}")
    
    def update_public_files(self):
        try:
            with STATE_LOCK: actual_public_files = set(os.listdir(PUBLIC_FILES_DIR))
            if set(self.public_files_list.get(0, tk.END)) != actual_public_files:
                self.public_files_list.delete(0, tk.END)
                for f in sorted(actual_public_files): self.public_files_list.insert(tk.END, f)
        except Exception as e: print(f"GUI Error updating public files: {e}")

    def update_clients_list(self):
        try:
            selected_value = None
            if self.clients_list.curselection(): selected_value = self.clients_list.get(self.clients_list.curselection())
            with STATE_LOCK:
                my_name = ACTIVE_CLIENTS.get(self.host_ip, {}).get('name', 'Host')
                true_client_list = ["Public Folder (Shared)", f"{my_name} (Host/You)"]
                for ip, d in sorted(ACTIVE_CLIENTS.items()):
                    if d.get('name') and ip != self.host_ip: true_client_list.append(f"{d['name']} ({ip})")
            if self.clients_list.get(0, tk.END) != tuple(true_client_list):
                self.clients_list.delete(0, tk.END)
                for item in true_client_list: self.clients_list.insert(tk.END, item)
                if selected_value in true_client_list:
                    new_index = true_client_list.index(selected_value)
                    self.clients_list.selection_set(new_index); self.clients_list.activate(new_index)
        except Exception as e: print(f"GUI Error updating clients list: {e}")

    def _open_incoming_file(self):
        if not self.incoming_files_list.curselection(): return
        filename = self.incoming_files_list.get(self.incoming_files_list.curselection())
        filepath = os.path.join(USER_FILES_DIR, self.host_ip, filename)
        try: os.startfile(os.path.abspath(filepath))
        except Exception as e: messagebox.showerror("Error", f"Could not open file: {e}")

    def _delete_incoming_file(self):
        if not self.incoming_files_list.curselection(): return
        filename = self.incoming_files_list.get(self.incoming_files_list.curselection())
        filepath = os.path.join(USER_FILES_DIR, self.host_ip, filename)
        if messagebox.askyesno("Confirm", f"Delete {filename}?"):
            os.remove(filepath)
            with STATE_LOCK: PENDING_FILES[self.host_ip] = [f for f in PENDING_FILES[self.host_ip] if f['filename'] != filename]

    def _open_public_file(self):
        if not self.public_files_list.curselection(): return
        filepath = os.path.join(PUBLIC_FILES_DIR, self.public_files_list.get(self.public_files_list.curselection()))
        try: os.startfile(os.path.abspath(filepath))
        except Exception as e: messagebox.showerror("Error", f"Could not open file: {e}")

    def _delete_public_file(self):
        if not self.public_files_list.curselection(): return
        filename = self.public_files_list.get(self.public_files_list.curselection())
        if messagebox.askyesno("Confirm", f"Delete {filename}?"): os.remove(os.path.join(PUBLIC_FILES_DIR, filename))

    def _select_files_to_send(self):
        filepaths = filedialog.askopenfilenames() 
        if filepaths: self._filepaths_to_send = filepaths; self.file_to_send.set(f"{len(filepaths)} file(s) selected")
    
    def _send_files(self):
        if not hasattr(self, '_filepaths_to_send') or not self.clients_list.curselection(): messagebox.showwarning("Warning", "Please select file(s) and a recipient."); return
        recipient_str = self.clients_list.get(self.clients_list.curselection())
        if "(Host/You)" in recipient_str:
            print(f"Copying {len(self._filepaths_to_send)} file(s) to your incoming folder...")
            for path in self._filepaths_to_send:
                filename = os.path.basename(path); dest_path = os.path.join(USER_FILES_DIR, self.host_ip, filename)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True); shutil.copy(path, dest_path)
                with STATE_LOCK: PENDING_FILES.setdefault(self.host_ip, []).append({'filename': filename, 'filepath': dest_path, 'sender': self.host_ip})
            print("Local copy complete."); messagebox.showinfo("Success", f"{len(self._filepaths_to_send)} file(s) moved.")
            self.file_to_send.set("No file selected."); del self._filepaths_to_send
            return
        if recipient_str == "Public Folder (Shared)": recipient_target = "Public Folder"
        else: recipient_target = recipient_str.split('(')[-1].strip(')')
        def send_in_thread():
            file_count = len(self._filepaths_to_send); print(f"Sending {file_count} file(s) to {recipient_target}...")
            total_size = sum(os.path.getsize(p) for p in self._filepaths_to_send)
            print(f"Uploading {file_count} files ({total_size/1024/1024:.2f} MB)...")
            try:
                files_list = [('filetoupload', (os.path.basename(p), open(p, 'rb'))) for p in self._filepaths_to_send]
                response = requests.post(f"http://127.0.0.1:{self.port_var.get()}/", files=files_list, data={'recipient': recipient_target}, timeout=300)
                for _, (_, handle) in files_list: handle.close()
                if response.ok: print("Upload complete."); messagebox.showinfo("Success", f"{file_count} file(s) sent.")
                else: print(f"Error sending files: {response.status_code}"); messagebox.showerror("Error", f"Failed. Status: {response.status_code}")
            except Exception as e: print(f"ERROR sending files: {e}"); messagebox.showerror("Error", f"An error occurred: {e}")
            self.file_to_send.set("No file selected."); del self._filepaths_to_send
        threading.Thread(target=send_in_thread, daemon=True).start()

    def on_closing(self):
        if self.httpd: self.stop_server()
        self.destroy()

# --- Application Startup ---
if __name__ == "__main__":
    os.makedirs(USER_FILES_DIR, exist_ok=True); os.makedirs(PUBLIC_FILES_DIR, exist_ok=True)
    app = App(); app.mainloop()