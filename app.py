import os
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

# Ensure the instance folder exists before initializing SQLite
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "lost-and-found-bus-stop-secret")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(INSTANCE_DIR, 'lost_and_found.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


# ---------------- Models ----------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    is_guest = db.Column(db.Boolean, default=False)
    date_joined = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_type = db.Column(db.String(10), nullable=False)  # 'lost' or 'found'
    item_name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    bus_stop_name = db.Column(db.String(150), nullable=False)
    spot = db.Column(db.String(150), nullable=True)
    contact_info = db.Column(db.String(150), nullable=False)
    verification_question = db.Column(db.String(255), nullable=True)  # only used on 'found' reports
    verification_answer = db.Column(db.String(255), nullable=True)
    date_reported = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default="open")  # open, claim_pending, resolved
    resolved = db.Column(db.Boolean, default=False)

    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reporter = db.relationship("User", backref="reports")


class ClaimRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey("report.id"), nullable=False)
    claimant_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    submitted_answer = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending, approved, rejected
    date_submitted = db.Column(db.DateTime, default=datetime.utcnow)

    report = db.relationship("Report", backref="claims")
    claimant = db.relationship("User")


with app.app_context():
    db.create_all()
    # seed a default admin account if none exists
    if not User.query.filter_by(is_admin=True).first():
        admin = User(username="admin", is_admin=True)
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()


# ---------------- Auth helpers ----------------

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        user = User.query.get(session["user_id"])
        if not user or not user.is_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrapper


def current_user():
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


# ---------------- Auth routes ----------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("signup.html")

        if User.query.filter_by(username=username).first():
            flash("That username is already taken.", "error")
            return render_template("signup.html")

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        session["user_id"] = user.id
        flash("Account created! Welcome.", "success")
        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            flash(f"Welcome back, {user.username}!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You've been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/guest")
def guest_login():
    # create a fresh temporary guest account each time
    guest_number = User.query.filter_by(is_guest=True).count() + 1
    username = f"guest{guest_number}_{int(datetime.utcnow().timestamp())}"

    guest = User(username=username, is_guest=True)
    db.session.add(guest)
    db.session.commit()

    session["user_id"] = guest.id
    flash("Continuing as guest — you have full access except the admin panel.", "success")
    return redirect(url_for("dashboard"))


# ---------------- Dashboard ----------------

@app.route("/")
def root():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    my_reports = Report.query.filter_by(reporter_id=user.id).order_by(Report.date_reported.desc()).all()
    my_claims = ClaimRequest.query.filter_by(claimant_id=user.id).order_by(ClaimRequest.date_submitted.desc()).all()
    return render_template("dashboard.html", my_reports=my_reports, my_claims=my_claims)


# ---------------- Report Lost / Found ----------------

@app.route("/report/<report_type>", methods=["GET", "POST"])
@login_required
def report(report_type):
    if report_type not in ("lost", "found"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        item_name = request.form.get("item_name", "").strip()
        description = request.form.get("description", "").strip()
        bus_stop_name = request.form.get("bus_stop_name", "").strip()
        spot = request.form.get("spot", "").strip()
        contact_info = request.form.get("contact_info", "").strip()
        verification_question = request.form.get("verification_question", "").strip()
        verification_answer = request.form.get("verification_answer", "").strip()

        if not item_name or not description or not bus_stop_name or not contact_info:
            flash("Please fill in all required fields.", "error")
            return render_template("report.html", report_type=report_type, form=request.form)

        if report_type == "found" and (not verification_question or not verification_answer):
            flash("For a found item, please set a verification question and answer.", "error")
            return render_template("report.html", report_type=report_type, form=request.form)

        new_report = Report(
            report_type=report_type,
            item_name=item_name,
            description=description,
            bus_stop_name=bus_stop_name,
            spot=spot or None,
            contact_info=contact_info,
            verification_question=verification_question if report_type == "found" else None,
            verification_answer=verification_answer if report_type == "found" else None,
            reporter_id=current_user().id,
        )
        db.session.add(new_report)
        db.session.commit()

        flash(f"Your {report_type} item report has been posted!", "success")
        return redirect(url_for("track_status"))

    return render_template("report.html", report_type=report_type, form={})


# ---------------- Track Status / Search / Claims ----------------

@app.route("/track")
@login_required
def track_status():
    filter_type = request.args.get("type", "all")
    search = request.args.get("q", "").strip()

    query = Report.query.filter(Report.status != "resolved")

    if filter_type in ("lost", "found"):
        query = query.filter_by(report_type=filter_type)

    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(
                Report.item_name.ilike(like),
                Report.bus_stop_name.ilike(like),
                Report.description.ilike(like),
            )
        )

    reports = query.order_by(Report.date_reported.desc()).all()
    return render_template("track.html", reports=reports, filter_type=filter_type, search=search)


@app.route("/claim/<int:report_id>", methods=["GET", "POST"])
@login_required
def claim(report_id):
    target = Report.query.get_or_404(report_id)
    user = current_user()

    if target.reporter_id == user.id:
        flash("You can't claim your own report.", "error")
        return redirect(url_for("track_status"))

    if request.method == "POST":
        answer = request.form.get("answer", "").strip()
        if not answer:
            flash("Please provide an answer.", "error")
            return render_template("claim.html", report=target)

        claim_req = ClaimRequest(report_id=target.id, claimant_id=user.id, submitted_answer=answer)

        # auto-check against the finder's stored answer (case-insensitive) if this is a found item
        if target.report_type == "found" and target.verification_answer:
            if answer.strip().lower() == target.verification_answer.strip().lower():
                claim_req.status = "approved"
                target.status = "claim_pending"
            else:
                claim_req.status = "pending"
        else:
            claim_req.status = "pending"

        db.session.add(claim_req)
        db.session.commit()

        if claim_req.status == "approved":
            flash("Verification correct! Contact details are now available on your dashboard.", "success")
        else:
            flash("Claim submitted. The reporter will review it.", "success")
        return redirect(url_for("dashboard"))

    return render_template("claim.html", report=target)


@app.route("/claim/<int:claim_id>/decide/<decision>", methods=["POST"])
@login_required
def decide_claim(claim_id, decision):
    claim_req = ClaimRequest.query.get_or_404(claim_id)
    user = current_user()

    if claim_req.report.reporter_id != user.id:
        flash("You can only decide on claims for your own reports.", "error")
        return redirect(url_for("dashboard"))

    if decision == "approve":
        claim_req.status = "approved"
        claim_req.report.status = "claim_pending"
        flash("Claim approved. Contact details shared with the claimant.", "success")
    elif decision == "reject":
        claim_req.status = "rejected"
        flash("Claim rejected.", "success")

    db.session.commit()
    return redirect(url_for("dashboard"))


@app.route("/resolve/<int:report_id>", methods=["POST"])
@login_required
def resolve(report_id):
    r = Report.query.get_or_404(report_id)
    if r.reporter_id != current_user().id and not current_user().is_admin:
        flash("You can only resolve your own reports.", "error")
        return redirect(url_for("dashboard"))
    r.resolved = True
    r.status = "resolved"
    db.session.commit()
    flash("Marked as resolved.", "success")
    return redirect(url_for("dashboard"))


# ---------------- Admin Panel ----------------

@app.route("/admin")
@admin_required
def admin_panel():
    reports = Report.query.order_by(Report.date_reported.desc()).all()
    claims = ClaimRequest.query.order_by(ClaimRequest.date_submitted.desc()).all()
    users = User.query.order_by(User.date_joined.desc()).all()
    stats = {
        "total_reports": Report.query.count(),
        "open_reports": Report.query.filter_by(status="open").count(),
        "resolved_reports": Report.query.filter_by(status="resolved").count(),
        "total_users": User.query.count(),
        "pending_claims": ClaimRequest.query.filter_by(status="pending").count(),
    }
    return render_template("admin.html", reports=reports, claims=claims, users=users, stats=stats)


@app.route("/admin/report/<int:report_id>/delete", methods=["POST"])
@admin_required
def admin_delete_report(report_id):
    r = Report.query.get_or_404(report_id)
    ClaimRequest.query.filter_by(report_id=r.id).delete()
    db.session.delete(r)
    db.session.commit()
    flash("Report removed by admin.", "success")
    return redirect(url_for("admin_panel"))


if __name__ == "__main__":
    app.run(debug=True)
