# FOUND_
CEP project
# FOUND_ (v2: Login, Track Status, Admin Panel)

Flask + SQLite web app for reporting and recovering lost/found items at bus stops, now with
accounts, claim verification, and an admin panel.

## Run it

```
pip install flask flask_sqlalchemy werkzeug
python app.py
```

Open http://127.0.0.1:5000 — you'll be sent to the login page.

A default admin account is auto-created on first run:
- username: `admin`
- password: `admin123`

## Modules (matches the system flow)

1. **Login / Signup / Guest** — simple username + password, stored (hashed) in the database. A "Continue as Guest" button on the login page creates a fresh temporary account instantly — guests get full access (report, browse, claim) except the admin panel, with a prompt to sign up to keep permanent access to their reports.
2. **Dashboard** — landing page after login, with links into each module, your own reports, pending claims on your reports, and claims you've made.
3. **Report Lost** — post a lost item: name, description, bus stop, optional spot, contact info.
4. **Report Found** — same fields, plus a required verification question + answer (e.g. "What color is the zipper?") that only the true owner would know.
5. **Track Status** — browse/search/filter all open reports; claim one that might be yours.
6. **Claim flow** —
   - On a **found** item: claimant answers the verification question. A correct answer (case-insensitive) is auto-approved and the reporter's contact info is revealed immediately on the claimant's dashboard.
   - On a **lost** item (or a wrong answer): the claim goes to "pending" and the reporter reviews it manually from their dashboard (Approve/Reject).
7. **Admin Panel** — visible only to admin accounts. Shows stats (total/open/resolved reports, pending claims, users), and tables of all reports, claims, and users, with the ability to delete reports.

## Structure
- `app.py` — Flask routes + models (User, Report, ClaimRequest)
- `templates/` — login, signup, dashboard, report, track, claim, admin, base
- `static/css/style.css` — same dark noticeboard theme (yellow/red/green, Syne + Space Mono), extended for auth/dashboard/admin UI
- `instance/` — SQLite DB created here on first run

## Notes
- Location is still bus-stop only: required bus stop name + optional spot, free text (no dropdown).
- Passwords are hashed with Werkzeug's `generate_password_hash` — never stored in plain text.
- To reset all data, delete `instance/lost_and_found.db` and restart the app (a fresh admin account is recreated automatically).
