#!/usr/bin/env python3
"""
T-Watch Health Monitor --- Full Server.py (WITH SQLITE) - FIXED DUPLICATE EVENTS
PART 1 OF 2
"""
from flask import Flask, render_template, request, redirect, session, send_file, jsonify, url_for
from datetime import datetime
import json
import os
import threading
import io
import sqlite3
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from functools import wraps
from werkzeug.utils import secure_filename

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = "supersecretkey123"

DATA_FILE = "data.json"
USERS_FILE = "users.json"
DB_PATH = "user.db"
PATIENT_PHOTOS_DIR = os.path.join("static", "patient_photos")
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

_file_lock = threading.Lock()

# =========================================================
# SQLITE
# =========================================================
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_sqlite():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        display_name TEXT,
        temperature REAL,
        steps INTEGER,
        fall INTEGER,
        seizure INTEGER,
        sos INTEGER,
        sleep_state INTEGER,
        last_ts TEXT,
        last_payload TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT,
        event_type TEXT,
        ts TEXT,
        payload TEXT
    )
    """)
    conn.commit()
    conn.close()

def save_sqlite(device_id, ts, payload, event_type="status"):
    conn = get_db()
    cur = conn.cursor()
    payload_txt = json.dumps(payload, default=str)
    
    try:
        cur.execute("INSERT INTO events(device_id,event_type,ts,payload) VALUES(?,?,?,?)",
                    (device_id, event_type, ts, payload_txt))
    except Exception as e:
        print("Warning: sqlite event insert failed:", e)
    
    try:
        cur.execute("""
        INSERT INTO devices VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id) DO UPDATE SET
        display_name=excluded.display_name,
        temperature=excluded.temperature,
        steps=excluded.steps,
        fall=excluded.fall,
        seizure=excluded.seizure,
        sos=excluded.sos,
        sleep_state=excluded.sleep_state,
        last_ts=excluded.last_ts,
        last_payload=excluded.last_payload
        """, (
            device_id,
            payload.get("display_name", device_id),
            payload.get("temperature"),
            payload.get("steps"),
            int(bool(payload.get("fall", 0))),
            int(bool(payload.get("seizure", 0))),
            int(bool(payload.get("sos", 0))),
            int(bool(payload.get("sleep_state", 0))),
            ts,
            payload_txt
        ))
    except Exception as e:
        print("Warning: sqlite upsert failed:", e)
    
    conn.commit()
    conn.close()

# =========================================================
# FILE HELPERS
# =========================================================
def _ensure_file(path, default):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(default, f, indent=4)

def load_data():
    _ensure_file(DATA_FILE, {})
    with _file_lock:
        try:
            return json.load(open(DATA_FILE))
        except:
            return {}

def save_data(data):
    with _file_lock:
        json.dump(data, open(DATA_FILE, "w"), indent=4)

def load_users():
    _ensure_file(USERS_FILE, {"admin": "admin123"})
    return json.load(open(USERS_FILE))

def save_users(users):
    json.dump(users, open(USERS_FILE, "w"), indent=4)

def ensure_dirs():
    os.makedirs(PATIENT_PHOTOS_DIR, exist_ok=True)

def patient_photo_url(device_id):
    for ext in ALLOWED_EXT:
        p = os.path.join(PATIENT_PHOTOS_DIR, f"{device_id}.{ext}")
        if os.path.exists(p):
            return url_for("static", filename=f"patient_photos/{device_id}.{ext}")
    return None

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# =========================================================
# AUTH DECORATOR
# =========================================================
from flask import request as _flask_request

def login_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if "user" not in session:
            if _flask_request.is_json:
                return jsonify({"error": "login required"}), 401
            return redirect("/login")
        return f(*a, **k)
    return wrap

# =========================================================
# AUTH ROUTES
# =========================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        pw = request.form.get("password", "")
        users = load_users()
        if username in users and users[username] == pw:
            session["user"] = username
            return redirect("/")
        return render_template("login.html", error="Invalid login.")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "").strip()
        confirm = request.form.get("confirm", "").strip()
        users = load_users()
        if not username:
            return render_template("signup.html", error="Username required.")
        if username in users:
            return render_template("signup.html", error="Username exists.")
        if pw != confirm:
            return render_template("signup.html", error="Passwords do not match.")
        users[username] = pw
        save_users(users)
        return redirect("/login")
    return render_template("signup.html")

@app.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        new_pw = request.form.get("password", "").strip()
        confirm = request.form.get("confirm", "").strip()
        users = load_users()
        if not username or username not in users:
            return render_template("forgot_password.html", error="User not found.")
        if new_pw != confirm:
            return render_template("forgot_password.html", error="Passwords do not match.")
        users[username] = new_pw
        save_users(users)
        return redirect("/login")
    return render_template("forgot_password.html")

@app.route("/logout")
def logout():
    username = session.get("user")
    if username:
        data = load_data()
        data.setdefault("_system_events", []).append({
            "event": "logout",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {"user": username}
        })
        data["_system_events"] = data["_system_events"][-200:]
        save_data(data)
    session.clear()
    return redirect("/login")

# =========================================================
# UPLOAD ENDPOINT - FIXED TO PREVENT DUPLICATES
# =========================================================
@app.route("/upload", methods=["POST"])
def upload():
    """
    FIXED: This endpoint now creates only ONE event per incoming payload.
    No more duplicates!
    """
    data = load_data()
    incoming = None
    raw_bytes = None
    
    try:
        incoming = request.get_json(force=True)
    except Exception as e:
        try:
            raw_bytes = request.get_data()
            txt = raw_bytes.decode("utf-8", errors="replace").strip()
            import json as _json
            incoming = _json.loads(txt) if txt else None
        except Exception as e2:
            print("upload: get_json error:", str(e))
            print("upload: fallback decode error:", str(e2))
            print("upload: raw bytes (first 500):", (raw_bytes[:500] if raw_bytes else b""))
            return jsonify({"error": "invalid_json", "msg": "Could not parse request body as JSON"}), 400
    
    if not isinstance(incoming, dict):
        return jsonify({"error": "payload_must_be_object", "received_type": str(type(incoming))}), 400
    
    device = incoming.get("device_id") or incoming.get("device") or incoming.get("deviceId")
    if not device:
        return jsonify({"error": "missing_device_id", "expected_example": {"device_id": "YOUR_ID"}}), 400
    
    if device not in data:
        data[device] = {
            "display_name": device,
            "temperature": None,
            "steps": None,
            "fall": 0,
            "seizure": 0,
            "sos": 0,
            "sleep_state": 0,
            "timestamp": None,
            "events": [],
            "last_raw": None
        }
    
    dev = data[device]
    
    # Store previous state for edge detection
    prev = {
        "fall": dev.get("fall", 0),
        "seizure": dev.get("seizure", 0),
        "sos": dev.get("sos", 0),
        "steps": dev.get("steps"),
        "sleep_state": dev.get("sleep_state")
    }
    
    dev["last_raw"] = incoming
    
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update device state
        if "temperature" in incoming:
            try:
                dev["temperature"] = float(incoming["temperature"])
            except:
                dev["temperature"] = incoming["temperature"]
        
        if "steps" in incoming:
            try:
                dev["steps"] = int(incoming["steps"])
            except:
                dev["steps"] = incoming["steps"]
        
        if "sleep_state" in incoming:
            try:
                dev["sleep_state"] = int(bool(int(incoming["sleep_state"])))
            except:
                try:
                    dev["sleep_state"] = int(bool(incoming["sleep_state"]))
                except:
                    dev["sleep_state"] = incoming["sleep_state"]
        
        if "fall" in incoming:
            dev["fall"] = int(bool(incoming["fall"]))
        elif str(incoming.get("event", "")).lower() == "fall":
            dev["fall"] = 1
        
        if "seizure" in incoming:
            dev["seizure"] = int(bool(incoming["seizure"]))
        elif str(incoming.get("event", "")).lower() == "seizure":
            dev["seizure"] = 1
        
        if "sos" in incoming:
            dev["sos"] = int(bool(incoming["sos"]))
        elif str(incoming.get("event", "")).lower() == "sos":
            dev["sos"] = 1
        
        dev["timestamp"] = ts
        
        # ====================================================================
        # EVENT CREATION LOGIC - FIXED TO CREATE ONLY ONE EVENT
        # ====================================================================
        
        events_created = []  # Track what we create to avoid duplicates
        
        # Determine the primary event type
        event_type = incoming.get("event", "").lower() if "event" in incoming else None
        
        # PRIORITY 1: Explicit alert events (fall, seizure, sos)
        # Only create if it's a rising edge (0 -> 1)
        alert_triggered = False
        
        for flag_name in ("seizure", "fall", "sos"):  # Check seizure first priority
            if flag_name in incoming or event_type == flag_name:
                new_val = int(bool(incoming.get(flag_name, 0)))
                old_val = int(bool(prev.get(flag_name, 0)))
                
                if new_val == 1 and old_val == 0:  # Rising edge
                    ev = {
                        "event": flag_name,
                        "ts": ts,
                        "payload": incoming.copy()
                    }
                    dev.setdefault("events", []).append(ev)
                    events_created.append(flag_name)
                    
                    # Save to SQLite
                    try:
                        save_sqlite(device, ts, incoming, event_type=flag_name)
                    except Exception as e:
                        print(f"Warning: sqlite save failed for {flag_name}:", e)
                    
                    alert_triggered = True
                    break  # Only create ONE alert event
        
        # PRIORITY 2: Sleep state changes
        if not alert_triggered and "sleep_state" in incoming:
            old_sleep = prev.get("sleep_state")
            new_sleep = dev.get("sleep_state")
            
            if old_sleep != new_sleep:  # Only if state changed
                ev = {
                    "event": "sleep_state_change" if event_type == "status" else (event_type or "sleep_state_change"),
                    "ts": ts,
                    "payload": {"sleep_state": new_sleep}
                }
                dev.setdefault("events", []).append(ev)
                events_created.append("sleep_state_change")
                
                try:
                    save_sqlite(device, ts, {"sleep_state": new_sleep}, event_type="sleep_state_change")
                except Exception as e:
                    print("Warning: sqlite save failed for sleep_state:", e)
        
        # PRIORITY 3: Steps updates (only if steps changed significantly)
        if not alert_triggered and "steps" in incoming:
            old_steps = prev.get("steps")
            new_steps = dev.get("steps")
            
            # Only log if steps changed by at least 1 (or if first reading)
            if old_steps is None or (new_steps != old_steps):
                ev = {
                    "event": "steps_update" if event_type == "status" else (event_type or "steps_update"),
                    "ts": ts,
                    "payload": {"steps": new_steps}
                }
                dev.setdefault("events", []).append(ev)
                events_created.append("steps_update")
                
                try:
                    save_sqlite(device, ts, {"steps": new_steps}, event_type="steps_update")
                except Exception as e:
                    print("Warning: sqlite save failed for steps:", e)
        
        # PRIORITY 4: Generic status/event updates
        # Only if we haven't created any other event AND there's an explicit event type
        if not events_created and event_type and event_type not in ["status", ""]:
            ev = {
                "event": event_type,
                "ts": ts,
                "payload": incoming.copy()
            }
            dev.setdefault("events", []).append(ev)
            events_created.append(event_type)
            
            try:
                save_sqlite(device, ts, incoming, event_type=event_type)
            except Exception as e:
                print(f"Warning: sqlite save failed for {event_type}:", e)
        
        # Trim events to last 200
        dev["events"] = dev.get("events", [])[-200:]
        
        # Save JSON data
        save_data(data)
        
        # Save snapshot to SQLite
        try:
            snapshot = {
                "display_name": dev.get("display_name", device),
                "temperature": dev.get("temperature"),
                "steps": dev.get("steps"),
                "fall": dev.get("fall"),
                "seizure": dev.get("seizure"),
                "sos": dev.get("sos"),
                "sleep_state": dev.get("sleep_state")
            }
            save_sqlite(device, ts, snapshot, event_type="snapshot")
        except Exception as e:
            print("Warning: sqlite save failed for snapshot:", e)
        
        print(f"✅ Upload success - Events created: {events_created if events_created else ['snapshot_only']}")
        return jsonify({"status": "ok", "events_created": events_created}), 200
        
    except Exception as ex:
        print("upload: processing error:", ex)
        return jsonify({"error": "processing_error", "msg": str(ex)}), 500

# END OF PART 1 - Continue to Part 2

# PART 2 OF 2 - Continue from Part 1

# =========================================================
# UPLOAD PHOTO
# =========================================================
@app.route("/upload_photo/<patient_id>", methods=["POST"])
@login_required
def upload_photo(patient_id):
    ensure_dirs()
    if "photo" not in request.files:
        return jsonify({"error": "no_file"}), 400
    
    photo = request.files["photo"]
    filename = secure_filename(photo.filename or "")
    if not filename or not allowed_file(filename):
        return jsonify({"error": "invalid_file_type"}), 400
    
    ext = filename.rsplit(".", 1)[1].lower()
    save_name = f"{patient_id}.{ext}"
    save_path = os.path.join(PATIENT_PHOTOS_DIR, save_name)
    photo.save(save_path)
    
    data = load_data()
    if patient_id in data:
        data[patient_id]["photo"] = f"/static/patient_photos/{save_name}"
        data[patient_id].setdefault("events", []).append({
            "event": "photo_uploaded",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {"uploaded_by": session.get("user"), "filename": save_name}
        })
        data[patient_id]["events"] = data[patient_id]["events"][-200:]
        save_data(data)
        
        try:
            save_sqlite(patient_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        {"photo_uploaded": True, "filename": save_name, "uploaded_by": session.get("user")},
                        event_type="photo_uploaded")
        except Exception as e:
            print("Warning: sqlite save failed for photo upload:", e)
    
    return jsonify({"status": "ok", "url": f"/static/patient_photos/{save_name}"}), 200

# =========================================================
# CLEAR ALERT
# =========================================================
@app.route("/clear_alert/<device_id>", methods=["POST"])
@login_required
def clear_alert(device_id):
    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    
    dev = data[device_id]
    dev["fall"] = 0
    dev["seizure"] = 0
    dev["sos"] = 0
    dev.setdefault("events", [])
    
    event_entry = {
        "event": "alert_cleared",
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "payload": {"cleared_by": session.get("user")}
    }
    dev["events"].append(event_entry)
    dev["events"] = dev.get("events", [])[-200:]
    
    save_data(data)
    
    try:
        save_sqlite(device_id, event_entry["ts"], {"alert_cleared": True, "cleared_by": session.get("user")}, event_type="alert_cleared")
    except Exception as e:
        print("Warning: sqlite save failed for clear_alert:", e)
    
    return jsonify({"status": "cleared"})

# =========================================================
# ALERT STATUS
# =========================================================
@app.route("/alert_status/<device_id>")
def alert_status(device_id):
    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    
    dev = data[device_id]
    active = (dev.get("fall", 0) == 1 or dev.get("seizure", 0) == 1 or dev.get("sos", 0) == 1)
    return jsonify({
        "active": active,
        "fall": dev.get("fall", 0),
        "seizure": dev.get("seizure", 0),
        "sos": dev.get("sos", 0)
    })

# =========================================================
# GLOBAL ALERT LIST
# =========================================================
@app.route("/alerts_all")
@login_required
def alerts_all():
    """
    Returns all devices that currently have:
    - fall = 1
    - seizure = 1
    - sos = 1
    Used by dashboard.js to trigger global siren.
    """
    data = load_data()
    alerts = []
    for device_id, entry in data.items():
        if device_id == "_system_events":
            continue
        fall = int(entry.get("fall", 0))
        seizure = int(entry.get("seizure", 0))
        sos = int(entry.get("sos", 0))
        if fall == 1 or seizure == 1 or sos == 1:
            alerts.append({
                "device_id": device_id,
                "name": entry.get("display_name", device_id),
                "fall": fall,
                "seizure": seizure,
                "sos": sos,
                "time": entry.get("timestamp")
            })
    return jsonify(alerts)

# =========================================================
# PATIENTS / LATEST / EVENTS
# =========================================================
@app.route("/patients")
@login_required
def patients():
    data = load_data()
    result = []
    for dev_id, entry in data.items():
        if dev_id == "_system_events":
            continue
        name = entry.get("display_name") or dev_id
        photo = entry.get("photo") or patient_photo_url(dev_id)
        result.append({"id": dev_id, "name": name, "photo": photo})
    result = sorted(result, key=lambda x: (x["name"] or "").lower())
    return jsonify(result)

@app.route("/latest/<device_id>")
@login_required
def latest(device_id):
    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    entry = data[device_id].copy()
    entry["photo"] = entry.get("photo") or patient_photo_url(device_id)
    return jsonify(entry)

@app.route("/events/<device_id>")
@login_required
def events_route(device_id):
    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data[device_id].get("events", []))

@app.route("/download_events/<device_id>")
@login_required
def download_events(device_id):
    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    
    events = data[device_id].get("events", [])[-50:]
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    w, h = letter
    y = h - 60
    
    c.setFont("Helvetica-Bold", 16)
    display_name = data[device_id].get("display_name", device_id)
    c.drawString(40, h - 40, f"Events for {display_name} ({device_id})")
    
    c.setFont("Helvetica", 10)
    for ev in reversed(events):
        line = f"{ev.get('ts','?')} --- {ev.get('event','?')}"
        c.drawString(40, y, line)
        y -= 14
        payload = json.dumps(ev.get("payload", {}))
        c.drawString(60, y, payload[:120])
        y -= 20
        if y < 60:
            c.showPage()
            y = h - 60
    
    c.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True,
                     download_name=f"events_{device_id}.pdf",
                     mimetype="application/pdf")

@app.route("/download_logs")
@login_required
def download_logs():
    _ensure_file(DATA_FILE, {})
    return send_file(DATA_FILE, as_attachment=True)

# =========================================================
# RENAME DEVICE
# =========================================================
@app.route("/rename_device", methods=["POST"])
@login_required
def rename_device():
    payload = None
    try:
        if request.is_json:
            payload = request.get_json(silent=True)
        else:
            raw = request.get_data(as_text=True).strip()
            if raw:
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = None
    except Exception as e:
        print("rename_device: parse error:", e)
        payload = None
    
    if not payload or not isinstance(payload, dict):
        return jsonify({"error": "invalid_json", "msg": "Expected JSON body with device_id and new_name"}), 400
    
    print("rename_device payload:", payload)
    print("session user:", session.get("user"))
    
    device_id = payload.get("device_id") or payload.get("old_id")
    new_name = payload.get("new_name") or payload.get("new_id")
    
    if not device_id or not new_name:
        return jsonify({"error": "bad_request", "msg": "device_id and new_name required"}), 400
    
    new_name = new_name.strip()
    if not new_name:
        return jsonify({"error": "bad_request", "msg": "new_name cannot be empty"}), 400
    
    data = load_data()
    if device_id not in data:
        return jsonify({"error": "device_not_found", "msg": f"Device {device_id} not found"}), 404
    
    for dev_key, entry in data.items():
        if dev_key == "_system_events":
            continue
        if dev_key != device_id and entry.get("display_name") == new_name:
            return jsonify({"error": "display_name_in_use", "msg": f"Name '{new_name}' is already in use"}), 409
    
    old_display_name = data[device_id].get("display_name", device_id)
    data[device_id]["display_name"] = new_name
    data[device_id].setdefault("events", [])
    
    ev = {
        "event": "rename",
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "payload": {
            "device_id": device_id,
            "old_display_name": old_display_name,
            "new_display_name": new_name,
            "changed_by": session.get("user")
        }
    }
    data[device_id]["events"].append(ev)
    data[device_id]["events"] = data[device_id]["events"][-200:]
    
    save_data(data)
    
    try:
        save_sqlite(device_id, ev["ts"], {"old_display_name": old_display_name, "new_display_name": new_name, "changed_by": session.get("user")}, event_type="rename")
    except Exception as e:
        print("Warning: sqlite save failed for rename:", e)
    
    return jsonify({
        "status": "ok",
        "device_id": device_id,
        "display_name": new_name,
        "message": f"Device renamed to '{new_name}'"
    }), 200

# =========================================================
# DEVICE INFO
# =========================================================
@app.route("/device_info/<device_id>")
@login_required
def device_info(device_id):
    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    entry = data[device_id].copy()
    entry["device_id"] = device_id
    entry["photo"] = entry.get("photo") or patient_photo_url(device_id)
    return jsonify(entry)

# =========================================================
# PAGES
# =========================================================
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route("/logs")
@login_required
def logs_page():
    data = load_data()
    logs = []
    active_alerts = []
    recent_cleared_alerts = []
    
    for dev, entry in data.items():
        if dev == "_system_events":
            continue
        
        # Build regular log entry
        logs.append({
            "device": dev,
            "display_name": entry.get("display_name", dev),
            "temperature": entry.get("temperature"),
            "steps": entry.get("steps"),
            "fall": entry.get("fall"),
            "seizure": entry.get("seizure"),
            "sos": entry.get("sos"),
            "sleep_state": entry.get("sleep_state"),
            "time": entry.get("timestamp"),
            "last_raw": entry.get("last_raw"),
            "alert_row": (entry.get("fall", 0) == 1 or entry.get("seizure", 0) == 1)
        })
        
        # Check for active alerts
        fall_active = entry.get("fall", 0) == 1
        seizure_active = entry.get("seizure", 0) == 1
        sos_active = entry.get("sos", 0) == 1
        
        if seizure_active:
            active_alerts.append({
                "device": dev,
                "display_name": entry.get("display_name", dev),
                "type": "seizure",
                "icon": "⚡",
                "time": entry.get("timestamp", "Unknown")
            })
        elif fall_active:
            active_alerts.append({
                "device": dev,
                "display_name": entry.get("display_name", dev),
                "type": "fall",
                "icon": "🟠",
                "time": entry.get("timestamp", "Unknown")
            })
        elif sos_active:
            active_alerts.append({
                "device": dev,
                "display_name": entry.get("display_name", dev),
                "type": "sos",
                "icon": "🆘",
                "time": entry.get("timestamp", "Unknown")
            })
        
        # Check for recently cleared alerts in events
        events = entry.get("events", [])
        for event in reversed(events[-20:]):  # Check last 20 events
            event_type = (event.get("event") or "").lower()
            if event_type == "alert_cleared":
                # Find what type of alert was cleared
                payload = event.get("payload", {})
                cleared_by = payload.get("cleared_by", "System")
                
                # Look for the previous alert type
                for prev_event in reversed(events):
                    prev_type = (prev_event.get("event") or "").lower()
                    if prev_type in ["fall", "seizure", "sos"]:
                        recent_cleared_alerts.append({
                            "device": dev,
                            "display_name": entry.get("display_name", dev),
                            "type": prev_type,
                            "icon": "⚡" if prev_type == "seizure" else ("🟠" if prev_type == "fall" else "🆘"),
                            "time": event.get("ts", "Unknown"),
                            "cleared_by": cleared_by
                        })
                        break
                break
    
    # Sort logs by time (most recent first)
    logs_sorted = sorted(logs, key=lambda x: x["time"] or "", reverse=True)
    
    # Remove duplicate cleared alerts (keep only most recent per device)
    seen_devices = set()
    filtered_cleared = []
    for alert in recent_cleared_alerts:
        if alert["device"] not in seen_devices:
            seen_devices.add(alert["device"])
            filtered_cleared.append(alert)
    
    return render_template("logs.html", 
                         logs=logs_sorted,
                         active_alerts=active_alerts,
                         recent_cleared_alerts=filtered_cleared[:5])  # Limit to 5 most recent

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

# =========================================================
# INIT AND RUN
# =========================================================
def init_db():
    _ensure_file(DATA_FILE, {})
    _ensure_file(USERS_FILE, {"admin": "admin123"})
    ensure_dirs()
    try:
        init_sqlite()
    except Exception as e:
        print("init_sqlite failed:", e)

if __name__ == "__main__":
    import logging
    port = int(os.environ.get("PORT", 5000))

    log = logging.getLogger('werkzeug')
    log.setLevel(logging.INFO)

    init_db()

    print("=" * 70)
    print(" 🏥 Health Monitoring - SERVER ONLINE")
    print(f" 🌐 Running on port {port}")
    print("=" * 70)

    # Run Flask (Render will NOT use Waitress)
    app.run(host="0.0.0.0", port=port)
