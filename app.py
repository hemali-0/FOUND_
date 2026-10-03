import os, re, secrets, sqlite3
from functools import wraps
from flask import Flask, request, jsonify, session, render_template, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "found.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL, is_admin INTEGER DEFAULT 0, is_guest INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL,
  category TEXT, stop TEXT NOT NULL, spot TEXT, description TEXT, date TEXT,
  contact TEXT, vq TEXT, va TEXT, status TEXT DEFAULT 'open',
  user_id INTEGER, claimed_by INTEGER, created TEXT DEFAULT CURRENT_TIMESTAMP);
"""


def init_db():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone():
        c.execute("INSERT INTO users(username,password,is_admin) VALUES(?,?,1)",
                  ("admin", generate_password_hash(os.environ.get("ADMIN_PASSWORD", "admin123"))))
    c.commit()
    c.close()


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
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


def user_json(u):
    return dict(id=u["id"], username=u["username"], is_admin=bool(u["is_admin"]), is_guest=bool(u["is_guest"]))


def item_json(r, u):
    mine = bool(u and r["user_id"] == u["id"])
    admin = bool(u and u["is_admin"])
    d = {k: r[k] for k in ("id", "kind", "name", "category", "stop", "spot",
                           "description", "date", "status", "created")}
    d["mine"] = mine
    if r["kind"] == "found":
        d["vq"] = r["vq"]
    if mine or admin or (u and r["claimed_by"] == u["id"]):
        d["contact"] = r["contact"]
    return d


# ---------- pages ----------
@app.route("/")
def index():
    return render_template("index.html")


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
    except sqlite3.IntegrityError:
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
    sql, args = "SELECT * FROM items WHERE 1=1", []
    if kind in ("lost", "found"):
        sql += " AND kind=?"; args.append(kind)
    if status:
        sql += " AND status=?"; args.append(status)
    if q:
        sql += " AND (name LIKE ? OR stop LIKE ? OR description LIKE ? OR category LIKE ?)"
        args += [f"%{q}%"] * 4
    rows = db().execute(sql + " ORDER BY id DESC", args).fetchall()
    u = user()
    return jsonify([item_json(r, u) for r in rows])


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
    cur = db().execute(
        "INSERT INTO items(kind,name,category,stop,spot,description,date,contact,vq,va,user_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (kind, f["name"], f["category"], f["stop"], f["spot"], f["description"], f["date"],
         f["contact"], f["vq"], f["va"], u["id"]))
    db().commit()
    return jsonify(id=cur.lastrowid)


def get_item(iid):
    return db().execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()


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
    rows = db().execute("SELECT * FROM items WHERE user_id=? ORDER BY id DESC", (user()["id"],)).fetchall()
    return jsonify([item_json(r, user()) for r in rows])


@app.get("/api/items/<int:iid>/matches")
@login_required
def matches(iid):
    """Smart matching: open found items in the same category or sharing a word in the name."""
    r, u = get_item(iid), user()
    if not r or r["user_id"] != u["id"] or r["kind"] != "lost":
        return err("Not allowed", 403)
    words = set(re.findall(r"\w{3,}", r["name"].lower()))
    out = []
    for f in db().execute("SELECT * FROM items WHERE kind='found' AND status='open'").fetchall():
        fw = set(re.findall(r"\w{3,}", f["name"].lower()))
        if (r["category"] and f["category"] == r["category"]) or words & fw:
            out.append(item_json(f, u))
    return jsonify(out)


# ---------- admin ----------
@app.get("/api/admin/stats")
@admin_required
def stats():
    c = db()
    one = lambda sql: c.execute(sql).fetchone()[0]
    return jsonify(users=one("SELECT COUNT(*) FROM users WHERE is_guest=0"),
                   guests=one("SELECT COUNT(*) FROM users WHERE is_guest=1"),
                   lost=one("SELECT COUNT(*) FROM items WHERE kind='lost'"),
                   found=one("SELECT COUNT(*) FROM items WHERE kind='found'"),
                   returned=one("SELECT COUNT(*) FROM items WHERE status='returned'"))


init_db()  # runs on import too, so gunicorn / hosts create the database

if __name__ == "__main__":
    app.run(debug=True)