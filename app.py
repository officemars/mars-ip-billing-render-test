from flask import Flask, render_template, render_template_string, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timezone, date
from pathlib import Path
from openpyxl import load_workbook
from docx import Document
from copy import deepcopy
import os, re, io

BASE = Path(__file__).resolve().parent
ASSETS = BASE / "assets"
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
db_url = os.environ.get("DATABASE_URL", "sqlite:///local-test.db")
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config.update(
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
)
db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.Text, nullable=False)
    display_name = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(80), default="Timekeeper")
    system_role = db.Column(db.String(40), default="Timekeeper", index=True)
    active = db.Column(db.Boolean, default=True)
    default_hourly_rate = db.Column(db.Numeric(12, 2), default=0)
    default_daily_rate = db.Column(db.Numeric(12, 2), default=0)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True, index=True)
    name = db.Column(db.String(255), nullable=False, index=True)
    billing_name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(100))
    address = db.Column(db.Text)
    country = db.Column(db.String(100))
    vat_number = db.Column(db.String(100))
    language = db.Column(db.String(40), default="English")
    currency = db.Column(db.String(10), default="EUR")
    payment_terms = db.Column(db.String(255))
    vat_treatment = db.Column(db.String(255))
    billing_email = db.Column(db.String(255))
    client_hourly_rate = db.Column(db.Numeric(12, 2))


class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True)
    display_name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255))
    phone = db.Column(db.String(100))
    salutation = db.Column(db.String(100))
    language = db.Column(db.String(40))
    organisation = db.Column(db.String(255))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))


class Matter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(100), unique=True, index=True)
    reference = db.Column(db.String(100))
    name = db.Column(db.String(500), nullable=False, index=True)
    nature = db.Column(db.String(255))
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    primary_contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"))
    language = db.Column(db.String(40))
    billing_mode = db.Column(db.String(100), default="Hourly rate")
    matter_hourly_rate = db.Column(db.Numeric(12, 2))
    matter_daily_rate = db.Column(db.Numeric(12, 2))
    fixed_fee = db.Column(db.Numeric(12, 2))
    currency = db.Column(db.String(10), default="EUR")
    payment_terms = db.Column(db.String(255))
    vat_treatment = db.Column(db.String(255))
    billable = db.Column(db.Boolean, default=True)
    closing_date = db.Column(db.String(40))
    notes = db.Column(db.Text)


class Timer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False, index=True)
    matter_id = db.Column(db.Integer, db.ForeignKey("matter.id"), nullable=False, index=True)
    category = db.Column(db.String(100))
    services = db.Column(db.Text, nullable=False)
    notes = db.Column(db.Text)
    start_time = db.Column(db.DateTime(timezone=True), nullable=False)
    stop_time = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20), default="Running", index=True)
    paused_at = db.Column(db.DateTime(timezone=True))
    paused_seconds = db.Column(db.Float, default=0)
    worked_hours = db.Column(db.Float)
    billable_hours = db.Column(db.Float)
    timekeeper_rate = db.Column(db.Numeric(12, 2))
    client_rate = db.Column(db.Numeric(12, 2))
    matter_rate = db.Column(db.Numeric(12, 2))
    applied_rate = db.Column(db.Numeric(12, 2))
    billable = db.Column(db.Boolean, default=True)
    amount = db.Column(db.Numeric(14, 2))
    invoiced_status = db.Column(db.String(30), default="Unbilled", index=True)
    invoice_number = db.Column(db.String(100))
    user = db.relationship(User)
    client = db.relationship(Client)
    matter = db.relationship(Matter)


class Pause(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timer_id = db.Column(db.Integer, db.ForeignKey("timer.id", ondelete="CASCADE"), nullable=False, index=True)
    paused_at = db.Column(db.DateTime(timezone=True), nullable=False)
    resumed_at = db.Column(db.DateTime(timezone=True))
    seconds = db.Column(db.Float)
    timer = db.relationship(Timer)


# Change 1 uses new tables, so the working tables already in Neon are not altered.
class FeeNoteDraft(db.Model):
    __tablename__ = "fee_note_draft_v2"
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(30), default="Draft", nullable=False)
    invoice_number = db.Column(db.String(100), unique=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    matter_id = db.Column(db.Integer, db.ForeignKey("matter.id"))
    contact_id = db.Column(db.Integer, db.ForeignKey("contact.id"))
    language = db.Column(db.String(30), default="English")
    billing_name = db.Column(db.String(255))
    billing_address = db.Column(db.Text)
    country = db.Column(db.String(100))
    contact_name = db.Column(db.String(255))
    issue_place = db.Column(db.String(100), default="Berlin")
    document_date = db.Column(db.String(40))
    vat_number = db.Column(db.String(100))
    client_name = db.Column(db.String(255))
    matter_text = db.Column(db.Text)
    engagement_date = db.Column(db.String(40))
    payment_terms = db.Column(db.String(255))
    currency = db.Column(db.String(10), default="EUR")
    vat_treatment = db.Column(db.String(100), default="Standard VAT")
    vat_label = db.Column(db.String(255), default="VAT")
    vat_wording = db.Column(db.Text)
    vat_rate = db.Column(db.Float, default=20)
    show_vat_number = db.Column(db.Boolean, default=True)
    show_engagement = db.Column(db.Boolean, default=True)
    show_time_schedule = db.Column(db.Boolean, default=True)
    show_telecom = db.Column(db.Boolean, default=True)
    telecom_description = db.Column(db.Text)
    telecom_amount = db.Column(db.Numeric(14, 2), default=20)
    annex_title = db.Column(db.String(255))
    annex_text = db.Column(db.Text)
    net_total = db.Column(db.Numeric(14, 2), default=0)
    vat_amount = db.Column(db.Numeric(14, 2), default=0)
    gross_total = db.Column(db.Numeric(14, 2), default=0)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    issued_at = db.Column(db.DateTime(timezone=True))
    client = db.relationship(Client)
    matter = db.relationship(Matter)
    contact = db.relationship(Contact)


class FeeNoteLine(db.Model):
    __tablename__ = "fee_note_line_v2"
    id = db.Column(db.Integer, primary_key=True)
    draft_id = db.Column(db.Integer, db.ForeignKey("fee_note_draft_v2.id", ondelete="CASCADE"), nullable=False, index=True)
    sort_order = db.Column(db.Integer, default=0)
    included = db.Column(db.Boolean, default=True)
    line_type = db.Column(db.String(100), default="Free data entry")
    description = db.Column(db.Text, nullable=False)
    service_date = db.Column(db.String(40))
    timekeeper = db.Column(db.String(255))
    quantity = db.Column(db.Float, default=1)
    rate = db.Column(db.Numeric(14, 2), default=0)
    amount = db.Column(db.Numeric(14, 2), default=0)
    amount_override = db.Column(db.Boolean, default=False)
    vat_treatment = db.Column(db.String(100))
    vat_rate = db.Column(db.Float, default=0)
    include_in_detail = db.Column(db.Boolean, default=False)
    source_timer_id = db.Column(db.Integer, db.ForeignKey("timer.id"))
    draft = db.relationship(FeeNoteDraft, backref=db.backref("fee_lines", cascade="all, delete-orphan"))
    timer = db.relationship(Timer)


def utcnow():
    return datetime.now(timezone.utc)


def n(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean(value):
    return "" if value is None else str(value).strip()


def key(value):
    return re.sub(r"[^a-z0-9]", "", clean(value).lower())


def header(ws, needle):
    for rn in range(1, min(ws.max_row, 25) + 1):
        values = [clean(cell.value) for cell in ws[rn]]
        if needle in values:
            return rn, {value: index for index, value in enumerate(values) if value}
    return None, {}


def import_master():
    workbook_path = ASSETS / "MARS-IP_Unified_Billing_System_FINAL 1.xlsx"
    if not workbook_path.exists():
        return False
    wb = load_workbook(workbook_path, data_only=True, read_only=True)
    if "Clients" in wb.sheetnames:
        ws = wb["Clients"]
        rn, headings = header(ws, "Client Name")
        for values in ws.iter_rows(min_row=rn + 1, values_only=True):
            get = lambda label: clean(values[headings[label]]) if label in headings and headings[label] < len(values) else ""
            if not get("Client Name"):
                continue
            record = Client.query.filter_by(external_id=get("Client ID")).first() or Client(external_id=get("Client ID"))
            record.name = get("Client Name")
            record.billing_name = get("Billing Name") or record.name
            record.email = get("Email")
            record.phone = get("Phone")
            record.address = get("Address")
            record.vat_number = get("VAT Number")
            record.language = get("Language") or "English"
            record.currency = get("Currency") or "EUR"
            record.payment_terms = get("Payment Terms")
            record.vat_treatment = get("VAT Treatment")
            record.billing_email = get("Billing Email")
            record.client_hourly_rate = n(get("Client Hourly Rate"))
            db.session.add(record)
        db.session.flush()
    client_ids = {key(record.name): record.id for record in Client.query.all()}
    if "Contacts" in wb.sheetnames:
        ws = wb["Contacts"]
        rn, headings = header(ws, "Display Name")
        for values in ws.iter_rows(min_row=rn + 1, values_only=True):
            get = lambda label: clean(values[headings[label]]) if label in headings and headings[label] < len(values) else ""
            if not get("Display Name"):
                continue
            record = Contact.query.filter_by(external_id=get("Contact ID")).first() or Contact(external_id=get("Contact ID"))
            record.display_name = get("Display Name")
            record.email = get("Email")
            record.phone = get("Phone")
            record.salutation = get("Exact Salutation")
            record.language = get("Preferred Language")
            record.organisation = get("Linked Organisation")
            record.client_id = client_ids.get(key(record.organisation))
            db.session.add(record)
    if "Matters" in wb.sheetnames:
        ws = wb["Matters"]
        rn, headings = header(ws, "Matter Name")
        for values in ws.iter_rows(min_row=rn + 1, values_only=True):
            get = lambda label: clean(values[headings[label]]) if label in headings and headings[label] < len(values) else ""
            client_id = client_ids.get(key(get("Client Name")))
            if not get("Matter Name") or not client_id:
                continue
            record = Matter.query.filter_by(external_id=get("Matter ID")).first() or Matter(external_id=get("Matter ID"))
            record.reference = get("Matter Reference")
            record.name = get("Matter Name")
            record.nature = get("Nature")
            record.client_id = client_id
            record.language = get("Language")
            record.billing_mode = get("Billing Mode") or "Hourly rate"
            record.matter_hourly_rate = n(get("Matter Hourly Rate"))
            record.matter_daily_rate = n(get("Matter Daily Rate"))
            record.fixed_fee = n(get("Fixed Fee"))
            record.currency = get("Currency") or "EUR"
            record.payment_terms = get("Payment Terms")
            record.vat_treatment = get("VAT Treatment")
            record.billable = get("Billable?") != "No"
            record.closing_date = get("Closing Date")
            record.notes = get("Notes")
            db.session.add(record)
    if "Users & Timekeepers" in wb.sheetnames:
        ws = wb["Users & Timekeepers"]
        rn, headings = header(ws, "Display Name")
        for values in ws.iter_rows(min_row=rn + 1, values_only=True):
            get = lambda label: clean(values[headings[label]]) if label in headings and headings[label] < len(values) else ""
            if not get("Display Name") or not get("Email"):
                continue
            record = User.query.filter(db.func.lower(User.email) == get("Email").lower()).first()
            if not record:
                record = User(email=get("Email").lower(), password_hash=generate_password_hash("ChangeMe123!"))
            record.display_name = get("Display Name")
            record.role = get("Role")
            record.system_role = get("System Role") or "Timekeeper"
            record.active = get("Active?") != "No"
            record.default_hourly_rate = n(get("Default Hourly Rate")) or 0
            record.default_daily_rate = n(get("Default Daily Rate")) or 0
            db.session.add(record)
    db.session.commit()
    return True


def bootstrap():
    db.create_all()
    email = os.environ.get("ADMIN_EMAIL", "admin@mars-ip.eu").lower()
    admin_user = User.query.filter(db.func.lower(User.email) == email).first()
    if not admin_user:
        admin_user = User(
            email=email,
            password_hash=generate_password_hash(os.environ.get("ADMIN_PASSWORD", "ChangeMe123!")),
            display_name="MARS-IP Administrator",
            role="Administrator",
            system_role="Administrator",
            active=True,
        )
        db.session.add(admin_user)
        db.session.commit()
    if Client.query.count() == 0:
        import_master()


def login_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return function(*args, **kwargs) if session.get("uid") else redirect(url_for("login"))
    return wrapped


def admin_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return function(*args, **kwargs) if session.get("role") == "Administrator" else ("Forbidden", 403)
    return wrapped


def elapsed(timer, at=None):
    at = at or utcnow()
    start = timer.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    seconds = (at - start).total_seconds() - float(timer.paused_seconds or 0)
    if timer.status == "Paused" and timer.paused_at:
        paused = timer.paused_at if timer.paused_at.tzinfo else timer.paused_at.replace(tzinfo=timezone.utc)
        seconds -= (at - paused).total_seconds()
    return max(0, seconds)


@app.before_request
def ensure_db():
    if not getattr(app, "_booted", False):
        bootstrap()
        app._booted = True


@app.get("/health")
def health():
    return {"status": "ok"}


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = User.query.filter(db.func.lower(User.email) == request.form["email"].strip().lower(), User.active.is_(True)).first()
        if user and check_password_hash(user.password_hash, request.form["password"]):
            session.clear()
            session.update(uid=user.id, name=user.display_name, role=user.system_role)
            return redirect(url_for("timer"))
        flash("Invalid login details.")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        form = request.form
        email = form["email"].strip().lower()
        if User.query.filter(db.func.lower(User.email) == email).first():
            flash("Account already exists.")
        elif len(form["password"]) < 10 or form["password"] != form["confirm"]:
            flash("Use matching passwords with at least 10 characters.")
        else:
            db.session.add(User(email=email, password_hash=generate_password_hash(form["password"]), display_name=form["name"].strip(), role="Timekeeper", system_role="Timekeeper", active=True, default_hourly_rate=0))
            db.session.commit()
            flash("Account created.")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/matters/<int:client_id>")
@login_required
def matters(client_id):
    return jsonify([{"id": matter.id, "reference": matter.reference, "name": matter.name} for matter in Matter.query.filter_by(client_id=client_id).order_by(Matter.name).all()])


@app.get("/timer")
@login_required
def timer():
    active = Timer.query.filter(Timer.user_id == session["uid"], Timer.status.in_(["Running", "Paused"])).order_by(Timer.id.desc()).all()
    for entry in active:
        entry.elapsed_seconds = elapsed(entry)
    recent = Timer.query.filter_by(user_id=session["uid"], status="Stopped").order_by(Timer.id.desc()).limit(20).all()
    return render_template("timer.html", clients=Client.query.order_by(Client.name).all(), active=active, recent=recent)


@app.post("/timer/start")
@login_required
def start_timer():
    user = db.session.get(User, session["uid"])
    client = db.session.get(Client, int(request.form["client_id"]))
    matter = db.session.get(Matter, int(request.form["matter_id"]))
    rate = matter.matter_hourly_rate if matter.matter_hourly_rate is not None else client.client_hourly_rate if client.client_hourly_rate is not None else user.default_hourly_rate
    entry = Timer(user=user, client=client, matter=matter, category=request.form.get("category"), services=request.form["services"].strip(), notes=request.form.get("notes", ""), start_time=utcnow(), status="Running", paused_seconds=0, timekeeper_rate=user.default_hourly_rate, client_rate=client.client_hourly_rate, matter_rate=matter.matter_hourly_rate, applied_rate=rate or 0, billable=matter.billable and request.form.get("billable", "1") == "1", invoiced_status="Unbilled")
    db.session.add(entry)
    db.session.commit()
    return redirect(url_for("timer"))


def owned(timer_id):
    return Timer.query.filter_by(id=timer_id, user_id=session["uid"]).first_or_404()


@app.post("/timer/<int:timer_id>/pause")
@login_required
def pause_timer(timer_id):
    entry = owned(timer_id)
    if entry.status == "Running":
        entry.status = "Paused"
        entry.paused_at = utcnow()
        db.session.add(Pause(timer=entry, paused_at=entry.paused_at))
        db.session.commit()
    return redirect(url_for("timer"))


@app.post("/timer/<int:timer_id>/resume")
@login_required
def resume_timer(timer_id):
    entry = owned(timer_id)
    if entry.status == "Paused" and entry.paused_at:
        paused = entry.paused_at if entry.paused_at.tzinfo else entry.paused_at.replace(tzinfo=timezone.utc)
        seconds = max(0, (utcnow() - paused).total_seconds())
        entry.paused_seconds = (entry.paused_seconds or 0) + seconds
        entry.status = "Running"
        entry.paused_at = None
        current_pause = Pause.query.filter_by(timer_id=entry.id, resumed_at=None).order_by(Pause.id.desc()).first()
        if current_pause:
            current_pause.resumed_at = utcnow()
            current_pause.seconds = seconds
        db.session.commit()
    return redirect(url_for("timer"))


@app.post("/timer/<int:timer_id>/stop")
@login_required
def stop_timer(timer_id):
    entry = owned(timer_id)
    at = utcnow()
    worked_hours = round(elapsed(entry, at) / 3600, 4)
    billable_hours = float(request.form.get("billable_hours") or worked_hours) if entry.billable else 0
    entry.stop_time = at
    entry.status = "Stopped"
    entry.paused_at = None
    entry.worked_hours = worked_hours
    entry.billable_hours = billable_hours
    entry.amount = round(billable_hours * float(entry.applied_rate or 0), 2)
    entry.notes = request.form.get("notes", entry.notes)
    db.session.commit()
    return redirect(url_for("timer"))


@app.get("/entries")
@login_required
def entries():
    query = Timer.query.order_by(Timer.id.desc()) if session["role"] == "Administrator" else Timer.query.filter_by(user_id=session["uid"]).order_by(Timer.id.desc())
    return render_template("entries.html", rows=query.all())


@app.get("/admin")
@login_required
@admin_required
def admin():
    return render_template("admin.html", users=User.query.order_by(User.display_name).all(), entries=Timer.query.order_by(Timer.id.desc()).all(), clients=Client.query.count(), matters=Matter.query.count())


@app.post("/admin/entry/<int:timer_id>")
@login_required
@admin_required
def edit_entry(timer_id):
    entry = db.session.get(Timer, timer_id)
    entry.services = request.form["services"]
    entry.billable_hours = float(request.form["billable_hours"])
    entry.applied_rate = float(request.form["applied_rate"])
    entry.amount = round(entry.billable_hours * float(entry.applied_rate), 2)
    db.session.commit()
    return redirect(url_for("admin"))


@app.post("/admin/reload")
@login_required
@admin_required
def reload_data():
    flash("Master data reloaded." if import_master() else "Workbook not found in assets/.")
    return redirect(url_for("admin"))


# ---------------- CHANGE 1: COMPLETE EDITABLE FEE-NOTE DRAFT ----------------
LINE_TYPES = [
    "Time-based fees", "Annual trademark monitoring", "Flat fee",
    "Trademark filing package", "Renewal package", "Success fee",
    "Costs", "Disbursement", "Post and telecommunications",
    "Provision", "Credit / discount", "Free data entry",
]

EDITOR = r'''{% extends "base.html" %}{% block content %}
<h1>{{ "Edit draft fee note" if draft else "Create draft fee note" }}</h1>
{% if not draft %}
<section class="card"><form method="post">
<div class="grid"><label>Client<select id="client" name="client_id" required><option value="">Select</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Matter<select id="matter" name="matter_id"><option value="">All matters / free entry</option></select></label><label>Contact<select id="contact" name="contact_id"><option value="">None / free entry</option></select></label><label>Language<select name="language"><option>English</option><option>French</option><option>German</option></select></label></div>
<h2>Completed unbilled time entries</h2><div id="timers"><p>Select a client.</p></div><button>Create prefilled editable draft</button></form></section>
<script>client.onchange=async()=>{let d=await(await fetch('/api/invoice-context/'+client.value)).json();matter.innerHTML='<option value="">All matters / free entry</option>'+d.matters.map(x=>`<option value="${x.id}">${x.reference||''} | ${x.name}</option>`).join('');contact.innerHTML='<option value="">None / free entry</option>'+d.contacts.map(x=>`<option value="${x.id}">${x.display_name}</option>`).join('');timers.innerHTML=d.timers.map(x=>`<label class="check"><input type="checkbox" name="timer_ids" value="${x.id}" checked> ${x.date} | ${x.timekeeper} | ${x.matter} | ${x.services} | ${x.billable_hours} h | ${x.amount}</label>`).join('')||'<p>No completed unbilled billable entries.</p>'}</script>
{% else %}
<section class="card"><form method="post" action="{{url_for('update_fee_note',draft_id=draft.id)}}">
<div class="grid">
<label>Language<select name="language">{% for x in ['English','French','German'] %}<option {% if draft.language==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></label>
<label>Billing name / ClientId<input name="billing_name" value="{{draft.billing_name or ''}}"></label>
<label>Client name<input name="client_name" value="{{draft.client_name or ''}}"></label>
<label>Billing address<textarea name="billing_address">{{draft.billing_address or ''}}</textarea></label>
<label>Country<input name="country" value="{{draft.country or ''}}"></label>
<label>Contact person<input name="contact_name" value="{{draft.contact_name or ''}}"></label>
<label>Place of issue<input name="issue_place" value="{{draft.issue_place or 'Berlin'}}"></label>
<label>Document date<input type="date" name="document_date" value="{{draft.document_date or ''}}"></label>
<label>VAT number<input name="vat_number" value="{{draft.vat_number or ''}}"></label>
<label>Matter<input name="matter_text" value="{{draft.matter_text or ''}}"></label>
<label>Engagement letter date<input type="date" name="engagement_date" value="{{draft.engagement_date or ''}}"></label>
<label>Payment terms<input name="payment_terms" value="{{draft.payment_terms or ''}}"></label>
<label>Currency<input name="currency" value="{{draft.currency or 'EUR'}}"></label>
<label>VAT treatment<select name="vat_treatment">{% for x in ['Standard VAT','Reverse charge','Other exemption','Free data entry'] %}<option {% if draft.vat_treatment==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></label>
<label>VAT label<input name="vat_label" value="{{draft.vat_label or 'VAT'}}"></label>
<label>VAT rate %<input type="number" step="0.01" name="vat_rate" value="{{draft.vat_rate or 0}}"></label>
<label>Custom VAT wording<textarea name="vat_wording">{{draft.vat_wording or ''}}</textarea></label>
<label>Telecom description<textarea name="telecom_description">{{draft.telecom_description or ''}}</textarea></label>
<label>Telecom amount<input type="number" step="0.01" name="telecom_amount" value="{{draft.telecom_amount or 0}}"></label>
<label>Annex title<input name="annex_title" value="{{draft.annex_title or ''}}"></label>
<label>Annex text<textarea name="annex_text">{{draft.annex_text or ''}}</textarea></label>
</div>
<div class="grid"><label class="check"><input type="checkbox" name="show_vat_number" {% if draft.show_vat_number %}checked{% endif %}> Show VAT-number line</label><label class="check"><input type="checkbox" name="show_engagement" {% if draft.show_engagement %}checked{% endif %}> Show engagement-letter sentence</label><label class="check"><input type="checkbox" name="show_time_schedule" {% if draft.show_time_schedule %}checked{% endif %}> Show detailed schedule</label><label class="check"><input type="checkbox" name="show_telecom" {% if draft.show_telecom %}checked{% endif %}> Show telecom line</label></div><button>Save header and recalculate</button></form></section>
<section class="card wide"><h2>Editable billing lines</h2><table><tr><th>Order</th><th>Include</th><th>Type</th><th>Description</th><th>Date</th><th>Timekeeper</th><th>Qty/hours</th><th>Rate</th><th>Manual amount</th><th>Net amount</th><th>VAT</th><th>Detail</th><th></th></tr>{% for line in lines %}<tr><form method="post" action="{{url_for('update_fee_line',line_id=line.id)}}"><td><input type="number" name="sort_order" value="{{line.sort_order}}"></td><td><input type="checkbox" name="included" {% if line.included %}checked{% endif %}></td><td><select name="line_type">{% for x in line_types %}<option {% if line.line_type==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></td><td><textarea name="description">{{line.description}}</textarea></td><td><input name="service_date" value="{{line.service_date or ''}}"></td><td><input name="timekeeper" value="{{line.timekeeper or ''}}"></td><td><input type="number" step="0.0001" name="quantity" value="{{line.quantity or 0}}"></td><td><input type="number" step="0.01" name="rate" value="{{line.rate or 0}}"></td><td><input type="checkbox" name="amount_override" {% if line.amount_override %}checked{% endif %}></td><td><input type="number" step="0.01" name="amount" value="{{line.amount or 0}}"></td><td><input type="number" step="0.01" name="vat_rate" value="{{line.vat_rate or 0}}"></td><td><input type="checkbox" name="include_in_detail" {% if line.include_in_detail %}checked{% endif %}></td><td><button>Save</button></form><form method="post" action="{{url_for('delete_fee_line',line_id=line.id)}}"><button class="danger">Remove</button></form></td></tr>{% endfor %}</table>
<h3>Add line</h3><form method="post" action="{{url_for('add_fee_line',draft_id=draft.id)}}"><div class="grid"><label>Type<select name="line_type">{% for x in line_types %}<option>{{x}}</option>{% endfor %}</select></label><label>Description<textarea name="description" required></textarea></label><label>Quantity/hours<input type="number" step="0.0001" name="quantity" value="1"></label><label>Rate<input type="number" step="0.01" name="rate" value="0"></label><label>Net amount<input type="number" step="0.01" name="amount" value="0"></label><label>VAT rate<input type="number" step="0.01" name="vat_rate" value="{{draft.vat_rate or 0}}"></label><label class="check"><input type="checkbox" name="amount_override"> Keep manual amount</label><label class="check"><input type="checkbox" name="include_in_detail"> Include in schedule</label></div><button>Add line</button></form></section>
<section class="card"><p>Net: <b>{{draft.net_total}} {{draft.currency}}</b></p><p>VAT: <b>{{draft.vat_amount}}</b></p><h2>Total gross: {{draft.gross_total}} {{draft.currency}}</h2>{% if draft.status=='Draft' %}<form method="post" action="{{url_for('issue_fee_note',draft_id=draft.id)}}"><button>Issue and download Word fee note</button></form>{% else %}<p>Issued as {{draft.invoice_number}}</p>{% endif %}</section>
{% endif %}{% endblock %}'''


@app.get("/api/invoice-context/<int:client_id>")
@login_required
@admin_required
def invoice_context(client_id):
    return jsonify({
        "matters": [{"id": m.id, "reference": m.reference, "name": m.name} for m in Matter.query.filter_by(client_id=client_id).order_by(Matter.name).all()],
        "contacts": [{"id": c.id, "display_name": c.display_name} for c in Contact.query.filter(db.or_(Contact.client_id == client_id, Contact.client_id.is_(None))).order_by(Contact.display_name).all()],
        "timers": [{"id": t.id, "date": t.start_time.date().isoformat(), "timekeeper": t.user.display_name, "matter": t.matter.name, "services": t.services, "billable_hours": t.billable_hours, "amount": float(t.amount or 0)} for t in Timer.query.filter_by(client_id=client_id, status="Stopped", billable=True, invoiced_status="Unbilled").order_by(Timer.start_time).all()],
    })


def recalculate(draft):
    included = FeeNoteLine.query.filter_by(draft_id=draft.id, included=True).all()
    for line in included:
        if not line.amount_override:
            line.amount = round(float(line.quantity or 0) * float(line.rate or 0), 2)
    net = sum(float(line.amount or 0) for line in included)
    if draft.show_telecom:
        net += float(draft.telecom_amount or 0)
    if draft.vat_treatment == "Reverse charge":
        draft.vat_rate = 0
        draft.vat_amount = 0
    else:
        draft.vat_amount = round(sum(float(line.amount or 0) * float(line.vat_rate if line.vat_rate is not None else draft.vat_rate or 0) / 100 for line in included), 2)
        if draft.show_telecom:
            draft.vat_amount += round(float(draft.telecom_amount or 0) * float(draft.vat_rate or 0) / 100, 2)
    draft.net_total = round(net, 2)
    draft.gross_total = round(net + float(draft.vat_amount or 0), 2)
    db.session.commit()


@app.route("/invoices/new", methods=["GET", "POST"])
@login_required
@admin_required
def invoice_new():
    if request.method == "GET":
        return render_template_string(EDITOR, draft=None, clients=Client.query.order_by(Client.name).all())
    client = db.session.get(Client, int(request.form["client_id"]))
    matter = db.session.get(Matter, int(request.form["matter_id"])) if request.form.get("matter_id") else None
    contact = db.session.get(Contact, int(request.form["contact_id"])) if request.form.get("contact_id") else None
    language = request.form.get("language") or client.language or "English"
    telecom_text = {
        "English": "Post and telecommunications flat rate pursuant to statutory provisions (No. 7002 RVG).",
        "French": "Forfait de télécommunications conformément aux dispositions légales (n° 7002 RVG).",
        "German": "Telekommunikationspauschale entsprechend den gesetzlichen Regelungen (Nr. 7002 RVG).",
    }[language]
    draft = FeeNoteDraft(client=client, matter=matter, contact=contact, language=language, billing_name=client.billing_name or client.name, billing_address=client.address, country=client.country, contact_name=contact.display_name if contact else "", issue_place="Berlin", document_date=date.today().isoformat(), vat_number=client.vat_number, client_name=client.name, matter_text=matter.name if matter else "", payment_terms=(matter.payment_terms if matter and matter.payment_terms else client.payment_terms), currency=(matter.currency if matter and matter.currency else client.currency or "EUR"), vat_treatment="Reverse charge" if "reverse" in (client.vat_treatment or "").lower() else "Standard VAT", vat_rate=0 if "reverse" in (client.vat_treatment or "").lower() else 20, telecom_description=telecom_text, telecom_amount=20, created_by=session["uid"])
    db.session.add(draft)
    db.session.flush()
    for order, timer_id in enumerate(request.form.getlist("timer_ids"), 1):
        timer = db.session.get(Timer, int(timer_id))
        db.session.add(FeeNoteLine(draft=draft, sort_order=order, included=True, line_type="Time-based fees", description=timer.services, service_date=timer.start_time.date().isoformat(), timekeeper=timer.user.display_name, quantity=timer.billable_hours or 0, rate=timer.applied_rate or 0, amount=timer.amount or 0, vat_treatment=draft.vat_treatment, vat_rate=draft.vat_rate, include_in_detail=True, source_timer_id=timer.id))
    db.session.commit()
    recalculate(draft)
    return redirect(url_for("edit_fee_note", draft_id=draft.id))


@app.get("/invoices/<int:draft_id>")
@login_required
@admin_required
def edit_fee_note(draft_id):
    draft = db.session.get(FeeNoteDraft, draft_id)
    lines = FeeNoteLine.query.filter_by(draft_id=draft_id).order_by(FeeNoteLine.sort_order, FeeNoteLine.id).all()
    return render_template_string(EDITOR, draft=draft, lines=lines, line_types=LINE_TYPES)


@app.post("/invoices/<int:draft_id>/header")
@login_required
@admin_required
def update_fee_note(draft_id):
    draft = db.session.get(FeeNoteDraft, draft_id)
    fields = ["language", "billing_name", "client_name", "billing_address", "country", "contact_name", "issue_place", "document_date", "vat_number", "matter_text", "engagement_date", "payment_terms", "currency", "vat_treatment", "vat_label", "vat_wording", "telecom_description", "annex_title", "annex_text"]
    for field in fields:
        setattr(draft, field, request.form.get(field, ""))
    draft.vat_rate = float(request.form.get("vat_rate") or 0)
    draft.telecom_amount = float(request.form.get("telecom_amount") or 0)
    for field in ["show_vat_number", "show_engagement", "show_time_schedule", "show_telecom"]:
        setattr(draft, field, field in request.form)
    recalculate(draft)
    return redirect(url_for("edit_fee_note", draft_id=draft.id))


def apply_line_form(line):
    line.sort_order = int(request.form.get("sort_order") or line.sort_order or 0)
    line.included = "included" in request.form
    line.line_type = request.form.get("line_type", line.line_type)
    line.description = request.form.get("description", line.description)
    line.service_date = request.form.get("service_date", "")
    line.timekeeper = request.form.get("timekeeper", "")
    line.quantity = float(request.form.get("quantity") or 0)
    line.rate = float(request.form.get("rate") or 0)
    line.amount_override = "amount_override" in request.form
    line.amount = float(request.form.get("amount") or 0) if line.amount_override else round(line.quantity * line.rate, 2)
    line.vat_rate = float(request.form.get("vat_rate") or 0)
    line.include_in_detail = "include_in_detail" in request.form


@app.post("/invoice-lines/<int:line_id>")
@login_required
@admin_required
def update_fee_line(line_id):
    line = db.session.get(FeeNoteLine, line_id)
    apply_line_form(line)
    db.session.commit()
    recalculate(line.draft)
    return redirect(url_for("edit_fee_note", draft_id=line.draft_id))


@app.post("/invoices/<int:draft_id>/lines")
@login_required
@admin_required
def add_fee_line(draft_id):
    order = (db.session.query(db.func.max(FeeNoteLine.sort_order)).filter_by(draft_id=draft_id).scalar() or 0) + 1
    line = FeeNoteLine(draft_id=draft_id, sort_order=order, included=True, description=request.form["description"])
    apply_line_form(line)
    db.session.add(line)
    db.session.commit()
    recalculate(line.draft)
    return redirect(url_for("edit_fee_note", draft_id=draft_id))


@app.post("/invoice-lines/<int:line_id>/delete")
@login_required
@admin_required
def delete_fee_line(line_id):
    line = db.session.get(FeeNoteLine, line_id)
    draft_id = line.draft_id
    db.session.delete(line)
    db.session.commit()
    recalculate(db.session.get(FeeNoteDraft, draft_id))
    return redirect(url_for("edit_fee_note", draft_id=draft_id))


def replace_paragraph(paragraph, replacements):
    original = "".join(run.text for run in paragraph.runs)
    updated = original
    for old, new in replacements.items():
        updated = updated.replace(old, str(new or ""))
    if updated != original:
        if paragraph.runs:
            paragraph.runs[0].text = updated
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            paragraph.add_run(updated)


def replace_table(table, replacements):
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                replace_paragraph(paragraph, replacements)
            for nested in cell.tables:
                replace_table(nested, replacements)


def replace_all(document, replacements):
    for paragraph in document.paragraphs:
        replace_paragraph(paragraph, replacements)
    for table in document.tables:
        replace_table(table, replacements)
    for section in document.sections:
        for paragraph in section.header.paragraphs:
            replace_paragraph(paragraph, replacements)
        for table in section.header.tables:
            replace_table(table, replacements)
        for paragraph in section.footer.paragraphs:
            replace_paragraph(paragraph, replacements)
        for table in section.footer.tables:
            replace_table(table, replacements)


def norm(value):
    return " ".join(str(value or "").lower().split())


def find_detail_table(document):
    for table in document.tables:
        text = norm(" ".join(cell.text for row in table.rows for cell in row.cells))
        if ("date" in text or "datum" in text) and ("services" in text or "leistungen" in text) and any(term in text for term in ["timekeeper", "intervenant", "bearbeiter"]):
            return table
    return None


def insert_before(table, reference_row, values):
    new_row = table.add_row()
    element = new_row._tr
    table._tbl.remove(element)
    reference_row._tr.addprevious(element)
    for index, value in enumerate(values):
        if index < len(new_row.cells):
            new_row.cells[index].text = str(value or "")


@app.post("/invoices/<int:draft_id>/issue")
@login_required
@admin_required
def issue_fee_note(draft_id):
    draft = db.session.get(FeeNoteDraft, draft_id)
    recalculate(draft)
    sequence = FeeNoteDraft.query.filter(FeeNoteDraft.invoice_number.like(f"MARS-INV-{date.today().year}-%")).count() + 1
    number = f"MARS-INV-{date.today().year}-{sequence:04d}"
    template = ASSETS / f"MARSIP reference invoice {'EN' if draft.language == 'English' else 'FR' if draft.language == 'French' else 'DE'}.docx"
    if not template.exists():
        flash(f"Template missing: {template.name}")
        return redirect(url_for("edit_fee_note", draft_id=draft.id))
    money = lambda value: f"{float(value or 0):,.2f} {draft.currency}"
    replacements = {
        "[final sequential document number, assigned only when the document is issued]": number,
        "[ClientId]": draft.billing_name,
        "[billing address]": draft.billing_address,
        "[country]": draft.country,
        "[contact person at the client or contact person linked to the matter]": draft.contact_name,
        "[place of issue, with Berlin as the default]": draft.issue_place,
        "[place of issue, with Berlin as the default ]": draft.issue_place,
        "[document date, with the current date as the default]": draft.document_date,
        "[document date, with the current date as the default ]": draft.document_date,
        "[VAT number Client]": draft.vat_number if draft.show_vat_number else "",
        "[client Name]": draft.client_name,
        "[Matter]": draft.matter_text,
        "[date of the Engagement Letter]": draft.engagement_date if draft.show_engagement else "",
        "[date of the Engagement letter]": draft.engagement_date if draft.show_engagement else "",
        "[total fees]": money(draft.net_total),
        "[total costs]": money(sum(float(line.amount or 0) for line in draft.fee_lines if line.included and line.line_type in ["Costs", "Disbursement"])),
        "Final Total Including VAT": money(draft.gross_total),
        "Total Final TTC": money(draft.gross_total),
        "[payment due date: eight days/30 days/30 days end of month]": draft.payment_terms,
        "[payment due date: acht Tage/30 Tage/30 Tage zum Monatsende]": draft.payment_terms,
        "[payment due date: huitainte/30 jours/30 jours fin de mois]": draft.payment_terms,
    }
    document = Document(template)
    replace_all(document, replacements)
    if draft.show_time_schedule:
        table = find_detail_table(document)
        detail_lines = [line for line in sorted(draft.fee_lines, key=lambda item: (item.sort_order, item.id)) if line.included and line.include_in_detail]
        if table and detail_lines:
            total_row = next((row for row in table.rows if "total" in norm(" ".join(cell.text for cell in row.cells)) or "gesamt" in norm(" ".join(cell.text for cell in row.cells))), table.rows[-1])
            for row in list(table.rows[1:]):
                if row is not total_row and not "".join(cell.text.strip() for cell in row.cells):
                    table._tbl.remove(row._tr)
            for line in detail_lines:
                insert_before(table, total_row, [line.service_date, line.timekeeper, line.description, line.quantity, money(line.amount)])
            total_row.cells[-1].text = money(sum(float(line.amount or 0) for line in detail_lines))
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    draft.invoice_number = number
    draft.status = "Issued"
    draft.issued_at = utcnow()
    for line in draft.fee_lines:
        if line.included and line.source_timer_id:
            timer = db.session.get(Timer, line.source_timer_id)
            timer.invoiced_status = "Invoiced"
            timer.invoice_number = number
    db.session.commit()
    return send_file(output, as_attachment=True, download_name=f"{number}.docx", mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


with app.app_context():
    bootstrap()
    app._booted = True
