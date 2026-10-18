import base64, os, re, secrets, sqlite3
from functools import wraps
from flask import Flask, Response, request, jsonify, session, render_template, g, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # reject request bodies over 4 MB

try:  # gzip responses (smaller + faster); optional
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
SQLITE_FILE = os.path.join(BASE, "found.db")

# ---------------------------------------------------------------------------
# Storage: if DATABASE_URL is set (Postgres, e.g. Neon) data is permanent.
# Otherwise a local SQLite file is used (great for running on your laptop).
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")
PG = DATABASE_URL.startswith("postgres")
if PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.pool import ThreadedConnectionPool
    POOL = ThreadedConnectionPool(1, 5, DATABASE_URL)
INTEGRITY = (sqlite3.IntegrityError,) + ((psycopg2.IntegrityError,) if PG else ())
LIKE = "ILIKE" if PG else "LIKE"

# every column except the (heavy) photo; has_photo tells us if one exists
COLS = ("id, kind, name, category, stop, spot, description, date, contact, vq, va, status, "
        "user_id, claimed_by, CASE WHEN photo IS NULL THEN 0 ELSE 1 END AS has_photo")


class Conn:
    """Tiny wrapper so the same SQL (written with ?) runs on SQLite and Postgres."""

    def __init__(self):
        if PG:
            self.c = POOL.getconn()
        else:
            self.c = sqlite3.connect(SQLITE_FILE)
            self.c.row_factory = sqlite3.Row

    def execute(self, sql, args=()):
        if not PG:
            return self.c.execute(sql, args)
        sql = sql.replace("?", "%s")
        is_insert = sql.lstrip().upper().startswith("INSERT INTO")
        if is_insert:
            sql += " RETURNING id"
        for attempt in (1, 2):
            try:
                cur = self.c.cursor(cursor_factory=RealDictCursor)
                cur.execute(sql, args)
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                if attempt == 2:
                    raise
                POOL.putconn(self.c, close=True)  # connection went stale (host slept): reconnect once
                self.c = POOL.getconn()
        if is_insert:
            return type("R", (), {"lastrowid": cur.fetchone()["id"]})()
        return cur

    def commit(self):
        self.c.commit()

    def rollback(self):
        self.c.rollback()

    def close(self):
        POOL.putconn(self.c) if PG else self.c.close()


def init_db():
    c = Conn()
    pk = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    c.execute(f"CREATE TABLE IF NOT EXISTS users(id {pk}, username TEXT UNIQUE NOT NULL, "
              "password TEXT NOT NULL, is_admin INTEGER DEFAULT 0, is_guest INTEGER DEFAULT 0)")
    c.execute(f"CREATE TABLE IF NOT EXISTS items(id {pk}, kind TEXT NOT NULL, name TEXT NOT NULL, "
              "category TEXT, stop TEXT NOT NULL, spot TEXT, description TEXT, date TEXT, contact TEXT, "
              "vq TEXT, va TEXT, status TEXT DEFAULT 'open', user_id INTEGER, claimed_by INTEGER, "
              "created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, photo TEXT)")
    # older databases: add the photo column
    if PG:
        c.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS photo TEXT")
    elif "photo" not in [r[1] for r in c.execute("PRAGMA table_info(items)").fetchall()]:
        c.execute("ALTER TABLE items ADD COLUMN photo TEXT")
    # indexes keep search / filters fast as reports grow
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_kind_status ON items(kind, status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_user ON items(user_id)")
    # admin account: ADMIN_PASSWORD (env) is applied on every start
    pw = os.environ.get("ADMIN_PASSWORD")
    if not c.execute("SELECT id FROM users WHERE is_admin=1").fetchone():
        c.execute("INSERT INTO users(username,password,is_admin) VALUES(?,?,1)",
                  ("admin", generate_password_hash(pw or "admin123")))
    elif pw:
        c.execute("UPDATE users SET password=? WHERE is_admin=1", (generate_password_hash(pw),))
    c.commit()
    c.close()


def db():
    if "db" not in g:
        g.db = Conn()
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def err(msg, code=400):
    return jsonify(error=msg), code


def user():
    uid = session.get("uid")
    if not uid:
        return None
    return db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not user():
            return err("Please log in first", 401)
        return f(*a, **k)
    return w


def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = user()
        if not u or not u["is_admin"]:
            return err("Admin only", 403)
        return f(*a, **k)
    return w


def clean_photo(data):
    """Validate a JPEG data-URL from the browser; return its base64 part."""
    if not data:
        return None
    m = re.fullmatch(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", data)
    if not m:
        raise ValueError("Photo must be a JPEG image")
    raw = base64.b64decode(m.group(1))
    if len(raw) > 2 * 1024 * 1024 or raw[:2] != b"\xff\xd8":
        raise ValueError("Invalid or too large photo")
    return m.group(1)


@app.errorhandler(413)
def too_big(_):
    return err("Photo is too large", 413)


def user_json(u):
    return dict(id=u["id"], username=u["username"], is_admin=bool(u["is_admin"]), is_guest=bool(u["is_guest"]))


def item_json(r, u):
    mine = bool(u and r["user_id"] == u["id"])
    admin = bool(u and u["is_admin"])
    d = {k: r[k] for k in ("id", "kind", "name", "category", "stop", "spot", "description", "date", "status")}
    d["mine"] = mine
    d["image"] = f"/api/items/{r['id']}/photo" if r["has_photo"] else None
    if r["kind"] == "found":
        d["vq"] = r["vq"]
    if mine or admin or (u and r["claimed_by"] == u["id"]):
        d["contact"] = r["contact"]
    return d


# ---------- pages ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(BASE, "static"), "favicon.ico", mimetype="image/x-icon")


@app.route("/sw.js")
def service_worker():
    # served from the site root so it can control the whole app
    return send_from_directory(os.path.join(BASE, "static"), "sw.js", mimetype="application/javascript")


# ---------- auth ----------
@app.post("/api/signup")
def signup():
    d = request.get_json(force=True)
    name, pw = (d.get("username") or "").strip(), d.get("password") or ""
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
        return err("Username: 3-20 letters, numbers or _")
    if len(pw) < 4:
        return err("Password must be at least 4 characters")
    try:
        cur = db().execute("INSERT INTO users(username,password) VALUES(?,?)",
                           (name, generate_password_hash(pw)))
        db().commit()
    except INTEGRITY:
        db().rollback()
        return err("Username already taken")
    session["uid"] = cur.lastrowid
    return jsonify(user_json(user()))


@app.post("/api/login")
def login():
    d = request.get_json(force=True)
    u = db().execute("SELECT * FROM users WHERE username=?", ((d.get("username") or "").strip(),)).fetchone()
    if not u or not check_password_hash(u["password"], d.get("password") or ""):
        return err("Wrong username or password", 401)
    session["uid"] = u["id"]
    return jsonify(user_json(u))


@app.post("/api/guest")
def guest():
    name = "guest_" + secrets.token_hex(3)
    cur = db().execute("INSERT INTO users(username,password,is_guest) VALUES(?,?,1)",
                       (name, generate_password_hash(secrets.token_hex(8))))
    db().commit()
    session["uid"] = cur.lastrowid
    return jsonify(user_json(user()))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    u = user()
    return jsonify(user_json(u) if u else None)


# ---------- items ----------
@app.get("/api/items")
def list_items():
    q, kind, status = request.args.get("q", "").strip(), request.args.get("kind", ""), request.args.get("status", "")
    try:
        limit = max(1, min(int(request.args.get("limit", 60)), 200))
    except ValueError:
        limit = 60
    sql, args = f"SELECT {COLS} FROM items WHERE 1=1", []
    if kind in ("lost", "found"):
        sql += " AND kind=?"; args.append(kind)
    if status:
        sql += " AND status=?"; args.append(status)
    if q:
        sql += f" AND (name {LIKE} ? OR stop {LIKE} ? OR description {LIKE} ? OR category {LIKE} ?)"
        args += [f"%{q}%"] * 4
    rows = db().execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
    u = user()
    return jsonify([item_json(r, u) for r in rows])


@app.get("/api/summary")
def summary():
    c = db()
    n = lambda where: c.execute(f"SELECT COUNT(*) AS n FROM items WHERE {where}").fetchone()["n"]
    return jsonify(open_lost=n("kind='lost' AND status='open'"),
                   open_found=n("kind='found' AND status='open'"),
                   resolved=n("status='returned'"))


@app.get("/api/items/<int:iid>/photo")
def photo(iid):
    r = db().execute("SELECT photo FROM items WHERE id=?", (iid,)).fetchone()
    if not r or not r["photo"]:
        return err("No photo", 404)
    resp = Response(base64.b64decode(r["photo"]), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=86400"  # photos never change, so browsers keep them
    return resp


@app.post("/api/items")
@login_required
def create_item():
    d, u = request.get_json(force=True), user()
    kind = d.get("kind")
    if kind not in ("lost", "found"):
        return err("Invalid report type")
    f = {k: (d.get(k) or "").strip() for k in
         ("name", "category", "stop", "spot", "description", "date", "contact", "vq", "va")}
    if not (f["name"] and f["stop"] and f["contact"]):
        return err("Item name, bus stop and contact are required")
    if kind == "found" and not (f["vq"] and f["va"]):
        return err("Set a verification question and its answer")
    if kind == "lost":
        f["vq"] = f["va"] = ""
    try:
        photo_b64 = clean_photo(d.get("image"))
    except ValueError as e:
        return err(str(e))
    cur = db().execute(
        "INSERT INTO items(kind,name,category,stop,spot,description,date,contact,vq,va,user_id,photo) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, f["name"], f["category"], f["stop"], f["spot"], f["description"], f["date"],
         f["contact"], f["vq"], f["va"], u["id"], photo_b64))
    db().commit()
    return jsonify(id=cur.lastrowid)


def get_item(iid):
    return db().execute(f"SELECT {COLS} FROM items WHERE id=?", (iid,)).fetchone()


@app.post("/api/items/<int:iid>/claim")
@login_required
def claim(iid):
    r, u = get_item(iid), user()
    if not r or r["kind"] != "found":
        return err("Item not found", 404)
    if r["status"] != "open":
        return err("This item is no longer open")
    if r["user_id"] == u["id"]:
        return err("You reported this item")
    ans = (request.get_json(force=True).get("answer") or "").strip().lower()
    if ans != (r["va"] or "").strip().lower():
        return err("Incorrect answer. Claim not verified.", 403)
    db().execute("UPDATE items SET status='claimed', claimed_by=? WHERE id=?", (u["id"], iid))
    db().commit()
    return jsonify(contact=r["contact"])


@app.patch("/api/items/<int:iid>/status")
@login_required
def set_status(iid):
    r, u = get_item(iid), user()
    if not r:
        return err("Item not found", 404)
    if r["user_id"] != u["id"] and not u["is_admin"]:
        return err("Not allowed", 403)
    s = request.get_json(force=True).get("status")
    if s not in ("open", "claimed", "returned"):
        return err("Invalid status")
    db().execute("UPDATE items SET status=? WHERE id=?", (s, iid))
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/items/<int:iid>")
@login_required
def delete_item(iid):
    r, u = get_item(iid), user()
    if not r:
        return err("Item not found", 404)
    if r["user_id"] != u["id"] and not u["is_admin"]:
        return err("Not allowed", 403)
    db().execute("DELETE FROM items WHERE id=?", (iid,))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/mine")
@login_required
def mine():
    u = user()
    rows = db().execute(f"SELECT {COLS} FROM items WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    return jsonify([item_json(r, u) for r in rows])


@app.get("/api/items/<int:iid>/matches")
@login_required
def matches(iid):
    """Smart matching: open found items in the same category or sharing a word in the name."""
    r, u = get_item(iid), user()
    if not r or r["user_id"] != u["id"] or r["kind"] != "lost":
        return err("Not allowed", 403)
    words = set(re.findall(r"\w{3,}", r["name"].lower()))
    out = []
    for f in db().execute(f"SELECT {COLS} FROM items WHERE kind='found' AND status='open' "
                          "ORDER BY id DESC LIMIT 200").fetchall():
        fw = set(re.findall(r"\w{3,}", f["name"].lower()))
        if (r["category"] and f["category"] == r["category"]) or words & fw:
            out.append(item_json(f, u))
    return jsonify(out)


# ---------- admin ----------
@app.get("/api/admin/stats")
@admin_required
def stats():
    c = db()
    n = lambda sql: c.execute(sql).fetchone()["n"]
    return jsonify(users=n("SELECT COUNT(*) AS n FROM users WHERE is_guest=0"),
                   guests=n("SELECT COUNT(*) AS n FROM users WHERE is_guest=1"),
                   lost=n("SELECT COUNT(*) AS n FROM items WHERE kind='lost'"),
                   found=n("SELECT COUNT(*) AS n FROM items WHERE kind='found'"),
                   returned=n("SELECT COUNT(*) AS n FROM items WHERE status='returned'"))


init_db()  # runs on import too, so gunicorn / hosts create the tables

if __name__ == "__main__":
    app.run(debug=True)