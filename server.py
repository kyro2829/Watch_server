#!/usr/bin/env python3
"""
T-Watch Health Monitor --- Supabase-backed server (Corrected)
Part 1 of 2
- Supabase integration + local JSON/SQLite fallback
- Fixed indentation, missing returns, signup/forgot issues, photo upload helpers
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

# Supabase client
try:
    from supabase import create_client, Client
except Exception:
    # If supabase isn't installed, we'll still keep the code runnable (fallback-only)
    create_client = None
    Client = None

# ---------------------------
# App & configuration
# ---------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("FLASK_SECRET", "supersecretkey123")

# Supabase envs (must be set in Render if you want cloud backing)
SUPABASE_URL = os.environ.get("https://mscxzpgcoispmxzwyuof.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1zY3h6cGdjb2lzcG14end5dW9mIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NTE5MzQzNiwiZXhwIjoyMDgwNzY5NDM2fQ.AboGeQlIOoN0hnwP-UPNJMoVofJOztpqnLnTezgY6eI")
SUPABASE_ANON_KEY = os.environ.get("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1zY3h6cGdjb2lzcG14end5dW9mIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjUxOTM0MzYsImV4cCI6MjA4MDc2OTQzNn0.7OMREJe6tWc6D5b57FVL245Tx7GQid3xgooqy_EKqqQ")  # optional

# Create Supabase client if credentials present and library available
supabase = None
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and create_client:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as e:
        print("Warning: failed to create Supabase client:", e)
        supabase = None
else:
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        print("Supabase credentials not set — running with local fallback only.")
    else:
        print("Supabase client library not installed — running with local fallback only.")

# Local constants (kept for compatibility; JSON fallback is optional)
DATA_FILE = os.path.join(BASE_DIR, "data.json")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
DB_PATH = os.path.join(BASE_DIR, "user.db")  # kept for sqlite events fallback (optional)

PATIENT_PHOTOS_DIR = os.path.join(BASE_DIR, "static", "patient_photos")
ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

_file_lock = threading.Lock()

# =========================================================
# SQLITE (kept for events snapshot compatibility)
# =========================================================
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_sqlite():
    try:
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
    except Exception as e:
        print("init_sqlite failed:", e)

def save_sqlite(device_id, ts, payload, event_type="status"):
    """
    Keep saving a local sqlite snapshot for compatibility and local debugging.
    The app's canonical storage is Supabase when available.
    """
    try:
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
    except Exception as e:
        print("sqlite save error:", e)

# =========================================================
# SUPABASE helpers (DB + Storage)
# =========================================================
def supa_get_user(username: str):
    """Return user row by username (or None)."""
    if not supabase:
        return None
    try:
        resp = supabase.table("users").select("*").eq("username", username).limit(1).execute()
        if getattr(resp, "error", None):
            print("supa_get_user error:", resp.error)
            return None
        data = resp.data or []
        return data[0] if len(data) else None
    except Exception as e:
        print("supa_get_user exception:", e)
        return None

def supa_create_user(username: str, password: str):
    """Create a user record in Supabase users table."""
    if not supabase:
        return None
    try:
        payload = {"username": username, "password": password}
        resp = supabase.table("users").insert(payload).execute()
        if getattr(resp, "error", None):
            print("supa_create_user error:", resp.error)
            return None
        return resp.data[0]
    except Exception as e:
        print("supa_create_user exception:", e)
        return None

def supa_upsert_device(device_id: str, snapshot: dict):
    """Upsert device snapshot into Supabase devices table."""
    if not supabase:
        return None
    try:
        snapshot_clean = {
            "device_id": device_id,
            "display_name": snapshot.get("display_name"),
            "temperature": snapshot.get("temperature"),
            "steps": snapshot.get("steps"),
            "fall": bool(snapshot.get("fall")),
            "seizure": bool(snapshot.get("seizure")),
            "sos": bool(snapshot.get("sos")),
            "sleep_state": bool(snapshot.get("sleep_state")),
            "last_ts": snapshot.get("last_ts"),
            "last_payload": snapshot.get("last_payload")
        }
        resp = supabase.table("devices").upsert(snapshot_clean, on_conflict="device_id").execute()
        if getattr(resp, "error", None):
            print("supa_upsert_device error:", resp.error)
        return resp
    except Exception as e:
        print("supa_upsert_device exception:", e)
        return None

def supa_insert_event(device_id: str, event_type: str, payload: dict, ts: str = None):
    """Insert event into Supabase events table."""
    if not supabase:
        return None
    try:
        row = {
            "device_id": device_id,
            "event_type": event_type,
            "payload": payload,
        }
        if ts:
            row["ts"] = ts
        resp = supabase.table("events").insert(row).execute()
        if getattr(resp, "error", None):
            print("supa_insert_event error:", resp.error)
        return resp
    except Exception as e:
        print("supa_insert_event exception:", e)
        return None

def supa_upload_photo_from_file(patient_id: str, file_stream, filename: str):
    """
    Upload file to Supabase storage bucket 'patient_photos' and return public URL.
    file_stream must be bytes or a file-like object.
    """
    if not supabase:
        return None
    bucket = "patient_photos"
    path = f"{patient_id}/{filename}"
    try:
        content = file_stream.read() if hasattr(file_stream, "read") else file_stream
        resp = supabase.storage.from_(bucket).upload(path, content, upsert=True)
        public = supabase.storage.from_(bucket).get_public_url(path)
        if isinstance(public, dict):
            public_url = public.get("publicURL") or public.get("publicUrl") or public.get("public_url")
            return public_url
        # supabase-py variations
        return getattr(public, "publicUrl", None) or getattr(public, "publicURL", None)
    except Exception as e:
        print("supa_upload_photo error:", e)
        return None

# =========================================================
# FILE HELPERS (fallback & compatibility)
# =========================================================
def _ensure_file(path, default):
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(default, f, indent=4)

def load_data():
    """
    Read cached JSON data (fallback). The canonical data is in Supabase,
    but JSON is kept for backward compatibility and fast reads.
    """
    _ensure_file(DATA_FILE, {})
    with _file_lock:
        try:
            return json.load(open(DATA_FILE))
        except:
            return {}

def save_data(data):
    """
    Save JSON cache (not canonical). Also attempt to sync key snapshots to Supabase devices table.
    """
    with _file_lock:
        json.dump(data, open(DATA_FILE, "w"), indent=4)

    # Try to upsert snapshots into Supabase for each device
    try:
        if not supabase:
            # nothing to sync
            return data
        for device_id, entry in data.items():
            if device_id == "_system_events":
                continue
            snapshot = {
                "display_name": entry.get("display_name", device_id),
                "temperature": entry.get("temperature"),
                "steps": entry.get("steps"),
                "fall": entry.get("fall"),
                "seizure": entry.get("seizure"),
                "sos": entry.get("sos"),
                "sleep_state": entry.get("sleep_state"),
                "last_ts": entry.get("timestamp"),
                "last_payload": entry.get("last_raw")
            }
            try:
                supa_upsert_device(device_id, snapshot)
            except Exception as e:
                print("Warning: supa_upsert_device failed:", e)
    except Exception as e:
        print("save_data supa sync error:", e)

    return data
# ------------------- continue server.py (Part 2 of 2) -------------------

# =========================================================
# UTILITY: patient photo URL helper (use Supabase public URL)
# =========================================================
def patient_photo_url(device_id):
    """
    Try Supabase public URL first. Fallback to static folder if exists.
    """
    try:
        if supabase:
            bucket = "patient_photos"
            for ext in ALLOWED_EXT:
                path = f"{device_id}/{device_id}.{ext}"
                public = supabase.storage.from_(bucket).get_public_url(path)
                if isinstance(public, dict):
                    url = public.get("publicUrl") or public.get("publicURL") or public.get("public_url")
                else:
                    url = getattr(public, "publicUrl", None) or getattr(public, "publicURL", None)
                if url:
                    return url
    except Exception as e:
        print("patient_photo_url supa error:", e)

    # fallback: local static
    for ext in ALLOWED_EXT:
        p = os.path.join(PATIENT_PHOTOS_DIR, f"{device_id}.{ext}")
        if os.path.exists(p):
            return url_for("static", filename=f"patient_photos/{device_id}.{ext}", _external=False)
    return None

# =========================================================
# Basic helpers
# =========================================================
def ensure_dirs():
    os.makedirs(PATIENT_PHOTOS_DIR, exist_ok=True)

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# =========================================================
# AUTH DECORATOR & ROUTES
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

# LOGIN
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

# SIGNUP (create user in Supabase)
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
        # create in supabase if available
        created = None
        try:
            created = supa_create_user(username, pw)
        except Exception as e:
            print("supa_create_user exception:", e)
            created = None
        if created is None:
            # fallback to local file
            users[username] = pw
            save_users(users)
        else:
            users[username] = pw
            save_users(users)
        return redirect("/login")
    return render_template("signup.html")

# FORGOT PASSWORD (update user in Supabase)
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
        # update in supabase if possible
        try:
            if supabase:
                resp = supabase.table("users").update({"password": new_pw}).eq("username", username).execute()
                if getattr(resp, "error", None):
                    print("forgot_password supa update error:", resp.error)
                    users[username] = new_pw
                    save_users(users)
                else:
                    users[username] = new_pw
                    save_users(users)
            else:
                users[username] = new_pw
                save_users(users)
        except Exception as e:
            print("forgot_password exception:", e)
            users[username] = new_pw
            save_users(users)
        return redirect("/login")
    return render_template("forgot_password.html")

# Useful aliases to match alternate template routes
@app.route("/register", methods=["GET", "POST"])
def register_alias():
    return signup()

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password_alias():
    return forgot_password()

# LOGOUT
@app.route("/logout")
def logout():
    username = session.get("user")
    if username:
        try:
            if supabase:
                supa_insert_event("system", "logout", {"user": username}, ts=datetime.now().isoformat())
        except Exception as e:
            print("logout supa_insert_event error:", e)
        # also keep local system event
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
# UPLOAD endpoint (watch -> server -> supabase/local)
# =========================================================
@app.route("/upload", methods=["POST"])
def upload():
    """
    Accepts JSON from the watch. Validates and writes to Supabase (if available) and local cache.
    """
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
            print("upload: get_json error:", e)
            print("upload: fallback decode error:", e2)
            return jsonify({"error": "invalid_json", "msg": "Could not parse request body as JSON"}), 400

    if not isinstance(incoming, dict):
        return jsonify({"error": "payload_must_be_object", "received_type": str(type(incoming))}), 400

    device = incoming.get("device_id") or incoming.get("device") or incoming.get("deviceId")
    if not device:
        return jsonify({"error": "missing_device_id", "expected_example": {"device_id": "YOUR_ID"}}), 400

    # Use load_data() to keep local cache and compatibility
    data = load_data()
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

    # previous state
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
        # update fields
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

        # event creation logic
        events_created = []
        event_type = incoming.get("event", "").lower() if "event" in incoming else None
        alert_triggered = False
        for flag_name in ("seizure", "fall", "sos"):
            if flag_name in incoming or event_type == flag_name:
                new_val = int(bool(incoming.get(flag_name, 0)))
                old_val = int(bool(prev.get(flag_name, 0)))
                if new_val == 1 and old_val == 0:
                    ev = {"event": flag_name, "ts": ts, "payload": incoming.copy()}
                    dev.setdefault("events", []).append(ev)
                    events_created.append(flag_name)
                    # save to supabase events table
                    try:
                        supa_insert_event(device, flag_name, incoming, ts=ts)
                    except Exception as e:
                        print(f"Warning: supa_insert_event failed for {flag_name}:", e)
                    alert_triggered = True
                    break

        if not alert_triggered and "sleep_state" in incoming:
            old_sleep = prev.get("sleep_state")
            new_sleep = dev.get("sleep_state")
            if old_sleep != new_sleep:
                ev = {"event": "sleep_state_change" if event_type == "status" else (event_type or "sleep_state_change"),
                      "ts": ts, "payload": {"sleep_state": new_sleep}}
                dev.setdefault("events", []).append(ev)
                events_created.append("sleep_state_change")
                try:
                    supa_insert_event(device, "sleep_state_change", {"sleep_state": new_sleep}, ts=ts)
                except Exception as e:
                    print("Warning: supa_insert_event failed for sleep_state:", e)

        if not alert_triggered and "steps" in incoming:
            old_steps = prev.get("steps")
            new_steps = dev.get("steps")
            if old_steps is None or (new_steps != old_steps):
                ev = {"event": "steps_update" if event_type == "status" else (event_type or "steps_update"),
                      "ts": ts, "payload": {"steps": new_steps}}
                dev.setdefault("events", []).append(ev)
                events_created.append("steps_update")
                try:
                    supa_insert_event(device, "steps_update", {"steps": new_steps}, ts=ts)
                except Exception as e:
                    print("Warning: supa_insert_event failed for steps:", e)

        if not events_created and event_type and event_type not in ["status", ""]:
            ev = {"event": event_type, "ts": ts, "payload": incoming.copy()}
            dev.setdefault("events", []).append(ev)
            events_created.append(event_type)
            try:
                supa_insert_event(device, event_type, incoming, ts=ts)
            except Exception as e:
                print(f"Warning: supa_insert_event failed for {event_type}:", e)

        # Trim events locally
        dev["events"] = dev.get("events", [])[-200:]

        # Save local cache
        save_data(data)

        # Upsert device snapshot to Supabase devices table
        try:
            snapshot = {
                "display_name": dev.get("display_name", device),
                "temperature": dev.get("temperature"),
                "steps": dev.get("steps"),
                "fall": bool(dev.get("fall")),
                "seizure": bool(dev.get("seizure")),
                "sos": bool(dev.get("sos")),
                "sleep_state": bool(dev.get("sleep_state")),
                "last_ts": dev.get("timestamp"),
                "last_payload": dev.get("last_raw")
            }
            if supabase:
                supa_upsert_device(device, snapshot)
            save_sqlite(device, ts, snapshot, event_type="snapshot")
        except Exception as e:
            print("Warning: supa_upsert_device failed for snapshot:", e)

        print(f"✅ Upload success - Events created: {events_created if events_created else ['snapshot_only']}")
        return jsonify({"status": "ok", "events_created": events_created}), 200

    except Exception as ex:
        print("upload: processing error:", ex)
        return jsonify({"error": "processing_error", "msg": str(ex)}), 500

# =========================================================
# UPLOAD PHOTO via Supabase Storage or local fallback
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

    try:
        file_bytes = photo.read()
        public_url = None
        if supabase:
            public_url = supa_upload_photo_from_file(patient_id, io.BytesIO(file_bytes), filename)
        if not public_url:
            ext = filename.rsplit(".", 1)[1].lower()
            save_name = f"{patient_id}.{ext}"
            save_path = os.path.join(PATIENT_PHOTOS_DIR, save_name)
            with open(save_path, "wb") as f:
                f.write(file_bytes)
            public_url = url_for("static", filename=f"patient_photos/{save_name}", _external=True)

        # Update cache + events
        data = load_data()
        if patient_id not in data:
            data[patient_id] = {"display_name": patient_id, "events": []}
        data[patient_id]["photo"] = public_url
        data[patient_id].setdefault("events", []).append({
            "event": "photo_uploaded",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {"uploaded_by": session.get("user"), "filename": filename}
        })
        data[patient_id]["events"] = data[patient_id]["events"][-200:]
        save_data(data)

        # Persist photo info to Supabase device snapshot if available
        try:
            if supabase:
                supa_upsert_device(patient_id, {"display_name": data[patient_id].get("display_name"), "last_payload": {"photo": public_url}})
        except Exception as e:
            print("Warning: supa_upsert_device failed when saving photo:", e)

        return jsonify({"status": "ok", "url": public_url}), 200
    except Exception as e:
        print("upload_photo exception:", e)
        return jsonify({"error": "upload_failed", "msg": str(e)}), 500

# =========================================================
# ALERT / DEVICE / EVENTS routes (read from Supabase when possible)
# =========================================================
@app.route("/alerts_all")
@login_required
def alerts_all():
    try:
        if supabase:
            resp = supabase.table("devices").select("*").execute()
            if getattr(resp, "error", None):
                raise Exception(getattr(resp, "error", "supabase error"))
            alerts = []
            for row in resp.data or []:
                if row.get("fall") or row.get("seizure") or row.get("sos"):
                    alerts.append({
                        "device_id": row.get("device_id"),
                        "name": row.get("display_name") or row.get("device_id"),
                        "fall": int(bool(row.get("fall"))),
                        "seizure": int(bool(row.get("seizure"))),
                        "sos": int(bool(row.get("sos"))),
                        "time": row.get("last_ts")
                    })
            return jsonify(alerts)
    except Exception as e:
        print("alerts_all supa error:", e)

    # fallback local
    data = load_data()
    alerts = []
    for device_id, entry in data.items():
        if device_id == "_system_events":
            continue
        if entry.get("fall") == 1 or entry.get("seizure") == 1 or entry.get("sos") == 1:
            alerts.append({
                "device_id": device_id,
                "name": entry.get("display_name", device_id),
                "fall": int(entry.get("fall", 0)),
                "seizure": int(entry.get("seizure", 0)),
                "sos": int(entry.get("sos", 0)),
                "time": entry.get("timestamp")
            })
    return jsonify(alerts)

@app.route("/patients")
@login_required
def patients():
    try:
        if supabase:
            resp = supabase.table("devices").select("device_id, display_name").execute()
            if getattr(resp, "error", None):
                raise Exception(getattr(resp, "error"))
            result = []
            for row in resp.data or []:
                dev_id = row.get("device_id")
                name = row.get("display_name") or dev_id
                photo = patient_photo_url(dev_id)
                result.append({"id": dev_id, "name": name, "photo": photo})
            result = sorted(result, key=lambda x: (x["name"] or "").lower())
            return jsonify(result)
    except Exception as e:
        print("patients supa error:", e)

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
    try:
        if supabase:
            resp = supabase.table("devices").select("*").eq("device_id", device_id).limit(1).execute()
            if getattr(resp, "error", None) or not resp.data:
                raise Exception("supabase no data")
            row = resp.data[0]
            entry = dict(row)
            entry["photo"] = patient_photo_url(device_id)
            return jsonify(entry)
    except Exception as e:
        print("latest supa error:", e)

    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    entry = data[device_id].copy()
    entry["photo"] = entry.get("photo") or patient_photo_url(device_id)
    return jsonify(entry)

@app.route("/events/<device_id>")
@login_required
def events_route(device_id):
    try:
        if supabase:
            resp = supabase.table("events").select("*").eq("device_id", device_id).order("ts", {"ascending": False}).limit(200).execute()
            if getattr(resp, "error", None):
                raise Exception(getattr(resp, "error"))
            events = resp.data or []
            normalized = []
            for ev in events:
                normalized.append({
                    "event": ev.get("event_type") or ev.get("event"),
                    "ts": ev.get("ts"),
                    "payload": ev.get("payload")
                })
            return jsonify(normalized)
    except Exception as e:
        print("events_route supa error:", e)

    data = load_data()
    if device_id not in data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data[device_id].get("events", []))

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

    # Check for name collision in Supabase devices
    try:
        if supabase:
            resp = supabase.table("devices").select("device_id, display_name").eq("display_name", new_name).limit(1).execute()
            if getattr(resp, "data", None):
                for r in resp.data:
                    if r.get("device_id") != device_id:
                        return jsonify({"error": "display_name_in_use", "msg": f"Name '{new_name}' is already in use"}), 409
    except Exception as e:
        print("rename_device supa select error:", e)

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

    # update supabase devices table
    try:
        if supabase:
            supabase.table("devices").update({"display_name": new_name}).eq("device_id", device_id).execute()
            supa_insert_event(device_id, "rename", ev["payload"], ts=ev["ts"])
    except Exception as e:
        print("Warning: supabase update failed for rename:", e)

    return jsonify({
        "status": "ok",
        "device_id": device_id,
        "display_name": new_name,
        "message": f"Device renamed to '{new_name}'"
    }), 200

# =========================================================
# PAGES + LOGS + DOWNLOAD (PDF) — use Supabase where possible
# =========================================================
@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route("/logs")
@login_required
def logs_page():
    logs = []
    active_alerts = []
    recent_cleared_alerts = []

    # Try supabase devices
    try:
        if supabase:
            dev_resp = supabase.table("devices").select("*").execute()
            if getattr(dev_resp, "error", None):
                raise Exception(getattr(dev_resp, "error"))
            devices = dev_resp.data or []
        else:
            devices = []
    except Exception as e:
        print("logs_page supa devices error:", e)
        devices = []

    # Build logs list from supabase rows if present
    if devices:
        for entry in devices:
            dev_id = entry.get("device_id")
            logs.append({
                "device": dev_id,
                "display_name": entry.get("display_name", dev_id),
                "temperature": entry.get("temperature"),
                "steps": entry.get("steps"),
                "fall": entry.get("fall"),
                "seizure": entry.get("seizure"),
                "sos": entry.get("sos"),
                "sleep_state": entry.get("sleep_state"),
                "time": entry.get("last_ts"),
                "last_raw": entry.get("last_payload"),
                "alert_row": (entry.get("fall") == True or entry.get("seizure") == True)
            })
    else:
        data = load_data()
        for dev, entry in data.items():
            if dev == "_system_events":
                continue
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

    # active alerts
    for row in logs:
        if row.get("seizure"):
            active_alerts.append({"device": row["device"], "display_name": row["display_name"], "type": "seizure", "icon": "⚡", "time": row.get("time", "Unknown")})
        elif row.get("fall"):
            active_alerts.append({"device": row["device"], "display_name": row["display_name"], "type": "fall", "icon": "🟠", "time": row.get("time", "Unknown")})
        elif row.get("sos"):
            active_alerts.append({"device": row["device"], "display_name": row["display_name"], "type": "sos", "icon": "🆘", "time": row.get("time", "Unknown")})

    # recent cleared alerts: try events query from supabase
    events_all = []
    try:
        if supabase:
            ev_resp = supabase.table("events").select("*").order("ts", {"ascending": False}).limit(200).execute()
            if getattr(ev_resp, "error", None):
                raise Exception(getattr(ev_resp, "error"))
            events_all = ev_resp.data or []
    except Exception as e:
        print("logs_page events supa error:", e)
        events_all = []

    seen_devices = set()
    filtered_cleared = []
    for event in events_all:
        event_type = (event.get("event_type") or "").lower()
        if event_type == "alert_cleared":
            device = event.get("device_id")
            if device in seen_devices:
                continue
            # attempt to find previous alert type
            prev_type = None
            for prev in events_all:
                if prev.get("device_id") == device:
                    pt = (prev.get("event_type") or "").lower()
                    if pt in ["fall", "seizure", "sos"]:
                        prev_type = pt
                        break
            filtered_cleared.append({"device": device, "display_name": device, "type": prev_type or "alert_cleared", "time": event.get("ts"), "cleared_by": (event.get("payload") or {}).get("cleared_by")})
            seen_devices.add(device)

    # sort logs
    logs_sorted = sorted(logs, key=lambda x: x["time"] or "", reverse=True)

    return render_template("logs.html", logs=logs_sorted, active_alerts=active_alerts, recent_cleared_alerts=filtered_cleared[:5])

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

# =========================================================
# INIT & RUN
# =========================================================
def init_db():
    _ensure_file(DATA_FILE, {})
    _ensure_file(USERS_FILE, {"admin": "admin123"})
    ensure_dirs()
    try:
        init_sqlite()
    except Exception as e:
        print("init_sqlite failed:", e)

# Initialize on import (works with Gunicorn)
init_db()

if __name__ == "__main__":
    import logging
    port = int(os.environ.get("PORT", 5000))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.INFO)
    print("=" * 70)
    print(" 🏥 Health Monitoring (Supabase-backed) - SERVER ONLINE")
    print(f" 🌐 Running on port {port}")
    print("=" * 70)
    # Use Flask dev server for local testing. On Render, use Gunicorn and set the Start Command to: gunicorn server:app
    app.run(host="0.0.0.0", port=port, debug=False)
