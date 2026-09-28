"""MARS-IP Change 2 hotfix.

Upload this file to the repository root and change Render Start Command to:
    gunicorn change2:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120

It imports the existing working app.py and replaces only the affected views.
"""
from copy import deepcopy
from io import BytesIO
from datetime import date

from flask import render_template_string, request, redirect, url_for, flash, jsonify, send_file, session
from docx import Document
from docx.table import _Row

import app as core

app = core.app
db = core.db
User = core.User
Client = core.Client
Contact = core.Contact
Matter = core.Matter
Timer = core.Timer
FeeNoteDraft = core.FeeNoteDraft
FeeNoteLine = core.FeeNoteLine
admin_required = core.admin_required
login_required = core.login_required
utcnow = core.utcnow
ASSETS = core.ASSETS


class EmployeeMatterAssignment(db.Model):
    __tablename__ = "employee_matter_assignment_v2"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    matter_id = db.Column(db.Integer, db.ForeignKey("matter.id", ondelete="CASCADE"), nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    user = db.relationship(User)
    matter = db.relationship(Matter)
    __table_args__ = (db.UniqueConstraint("user_id", "matter_id", name="uq_employee_matter_v2"),)


with app.app_context():
    db.create_all()


ADMIN_PAGE = r'''{% extends "base.html" %}{% block content %}
<h1>Administration</h1>
<section class="card"><p>{{users|length}} users · {{client_count}} clients · {{matter_count}} matters</p>
<form method="post" action="{{url_for('reload_data')}}"><button>Reload master workbook</button></form></section>
<section class="card wide"><h2>Users, rates and assigned projects</h2>
<table><tr><th>User</th><th>Role</th><th>Hourly rate</th><th>Daily rate</th><th>Active</th><th>Assigned matters</th><th>Save</th></tr>
{% for u in users %}<tr><form method="post" action="{{url_for('change2_user',user_id=u.id)}}">
<td>{{u.display_name}}<br><small>{{u.email}}</small></td>
<td><select name="system_role"><option {% if u.system_role=='Timekeeper' %}selected{% endif %}>Timekeeper</option><option {% if u.system_role=='Administrator' %}selected{% endif %}>Administrator</option></select></td>
<td><input type="number" step="0.01" name="hourly_rate" value="{{u.default_hourly_rate or 0}}"></td>
<td><input type="number" step="0.01" name="daily_rate" value="{{u.default_daily_rate or 0}}"></td>
<td><input type="checkbox" name="active" {% if u.active %}checked{% endif %}></td>
<td><select name="matter_ids" multiple size="8">{% for m in matters %}<option value="{{m.id}}" {% if m.id in assigned[u.id] %}selected{% endif %}>{{m.reference or ''}} | {{m.name}}</option>{% endfor %}</select><small>Use Ctrl/Command to select several. No selection means access to all matters.</small></td>
<td><button>Save</button></td></form></tr>{% endfor %}</table></section>
<section class="card wide"><h2>All time entries</h2>
<table><tr><th>User</th><th>Client</th><th>Matter</th><th>Services</th><th>Status</th><th>Billable hours</th><th>Rate</th><th>Amount</th><th>Save</th></tr>
{% for t in entries %}<tr><form method="post" action="{{url_for('edit_entry',timer_id=t.id)}}"><td>{{t.user.display_name}}</td><td>{{t.client.name}}</td><td>{{t.matter.name}}</td><td><input name="services" value="{{t.services}}"></td><td>{{t.status}}</td><td><input type="number" step="0.0001" name="billable_hours" value="{{t.billable_hours or 0}}"></td><td><input type="number" step="0.01" name="applied_rate" value="{{t.applied_rate or 0}}"></td><td>{{t.amount or 0}}</td><td><button>Save</button></td></form></tr>{% endfor %}</table></section>
{% endblock %}'''


@login_required
@admin_required
def admin_fixed():
    users = User.query.order_by(User.display_name).all()
    matters = Matter.query.order_by(Matter.name).all()
    assignments = EmployeeMatterAssignment.query.filter_by(active=True).all()
    assigned = {user.id: set() for user in users}
    for item in assignments:
        assigned.setdefault(item.user_id, set()).add(item.matter_id)
    return render_template_string(
        ADMIN_PAGE,
        users=users,
        matters=matters,
        assigned=assigned,
        entries=Timer.query.order_by(Timer.id.desc()).all(),
        client_count=Client.query.count(),
        matter_count=Matter.query.count(),
    )


# Replace the existing broken /admin endpoint without changing its URL.
app.view_functions["admin"] = admin_fixed


@app.post("/admin/change2/users/<int:user_id>")
@login_required
@admin_required
def change2_user(user_id):
    user = db.session.get(User, user_id)
    user.default_hourly_rate = float(request.form.get("hourly_rate") or 0)
    user.default_daily_rate = float(request.form.get("daily_rate") or 0)
    user.system_role = request.form.get("system_role", "Timekeeper")
    user.active = "active" in request.form
    selected = {int(value) for value in request.form.getlist("matter_ids")}
    EmployeeMatterAssignment.query.filter_by(user_id=user.id).delete()
    for matter_id in selected:
        db.session.add(EmployeeMatterAssignment(user_id=user.id, matter_id=matter_id, active=True))
    db.session.commit()
    flash("User rates and matter assignments saved.")
    return redirect(url_for("admin"))


@login_required
def matters_fixed(client_id):
    query = Matter.query.filter_by(client_id=client_id)
    if session.get("role") != "Administrator":
        assignment_ids = [item.matter_id for item in EmployeeMatterAssignment.query.filter_by(user_id=session["uid"], active=True).all()]
        if assignment_ids:
            query = query.filter(Matter.id.in_(assignment_ids))
    return jsonify([{"id": item.id, "reference": item.reference, "name": item.name} for item in query.order_by(Matter.name).all()])


# Replace the existing matter API so assignments take effect in the timer screen.
app.view_functions["matters"] = matters_fixed


def text_of_row(row):
    return " ".join(cell.text for cell in row.cells).strip().lower()


def money(value, currency):
    return f"{float(value or 0):,.2f} {currency}"


def set_cell(cell, value):
    if cell.paragraphs:
        paragraph = cell.paragraphs[0]
        if paragraph.runs:
            paragraph.runs[0].text = str(value or "")
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            paragraph.text = str(value or "")
    else:
        cell.text = str(value or "")


def replace_in_paragraph(paragraph, replacements):
    original = "".join(run.text for run in paragraph.runs)
    changed = original
    for old, new in replacements.items():
        changed = changed.replace(old, str(new or ""))
    if changed != original:
        if paragraph.runs:
            paragraph.runs[0].text = changed
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            paragraph.text = changed


def replace_in_table(table, replacements):
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                replace_in_paragraph(paragraph, replacements)
            for nested in cell.tables:
                replace_in_table(nested, replacements)


def replace_everywhere(document, replacements):
    for paragraph in document.paragraphs:
        replace_in_paragraph(paragraph, replacements)
    for table in document.tables:
        replace_in_table(table, replacements)
    for section in document.sections:
        for paragraph in section.header.paragraphs:
            replace_in_paragraph(paragraph, replacements)
        for table in section.header.tables:
            replace_in_table(table, replacements)
        for paragraph in section.footer.paragraphs:
            replace_in_paragraph(paragraph, replacements)
        for table in section.footer.tables:
            replace_in_table(table, replacements)


def find_summary_table(document):
    for table in document.tables:
        text = " ".join(cell.text for row in table.rows for cell in row.cells).lower()
        if any(marker in text for marker in ["total net", "nettobetrag", "montant h.t.", "montant ht"]):
            return table
    return None


def find_detail_table(document):
    for table in document.tables:
        if not table.rows or len(table.rows[0].cells) < 5:
            continue
        header = " ".join(cell.text for cell in table.rows[0].cells).lower()
        if ("date" in header or "datum" in header) and any(marker in header for marker in ["timekeeper", "intervenant", "bearbeiter"]):
            return table
    return None


def add_row_before(table, reference_row, template_row, values):
    new_xml = deepcopy(template_row._tr)
    reference_row._tr.addprevious(new_xml)
    # Resolve the inserted row from the table after insertion.
    inserted = _Row(new_xml, table)
    for index, value in enumerate(values):
        if index < len(inserted.cells):
            set_cell(inserted.cells[index], value)
    return inserted


def rebuild_summary(document, draft, included_lines):
    table = find_summary_table(document)
    if table is None:
        return
    net_index = next((index for index, row in enumerate(table.rows) if any(marker in text_of_row(row) for marker in ["total net", "nettobetrag", "montant h.t.", "montant ht"])), None)
    if net_index is None or net_index < 2:
        return
    header_row = table.rows[0]
    template_row = table.rows[1]
    net_row = table.rows[net_index]
    # Remove every optional/example fee row. Only rows actually approved in the draft are reinserted.
    for row in list(table.rows[1:net_index]):
        table._tbl.remove(row._tr)
    for line in sorted(included_lines, key=lambda item: (item.sort_order, item.id)):
        add_row_before(table, net_row, template_row, [line.description, money(line.amount, draft.currency)])
    if draft.show_telecom and float(draft.telecom_amount or 0) != 0:
        add_row_before(table, net_row, template_row, [draft.telecom_description, money(draft.telecom_amount, draft.currency)])
    if len(net_row.cells) >= 2:
        set_cell(net_row.cells[-1], money(draft.net_total, draft.currency))
    # Update VAT and gross rows directly so both 20% and 0% are never shown together.
    rows = list(table.rows)
    net_pos = next((index for index, row in enumerate(rows) if any(marker in text_of_row(row) for marker in ["total net", "nettobetrag", "montant h.t.", "montant ht"])), None)
    vat_row = rows[net_pos + 1] if net_pos is not None and len(rows) > net_pos + 1 else None
    gross_row = rows[net_pos + 2] if net_pos is not None and len(rows) > net_pos + 2 else None
    if vat_row:
        if draft.vat_treatment == "Reverse charge":
            vat_text = draft.vat_wording or "Reverse charge (VAT payable by the recipient, Article 196 of VAT Directive 2006/112/EC)"
            vat_value = "0%"
        elif draft.vat_treatment == "Standard VAT":
            vat_text = draft.vat_label or "VAT"
            vat_value = f"{float(draft.vat_rate or 0):g}%"
        else:
            vat_text = draft.vat_wording or draft.vat_treatment
            vat_value = f"{float(draft.vat_rate or 0):g}%"
        set_cell(vat_row.cells[0], vat_text)
        set_cell(vat_row.cells[-1], vat_value)
    if gross_row:
        set_cell(gross_row.cells[-1], money(draft.gross_total, draft.currency))


def rebuild_detail(document, draft, detail_lines):
    table = find_detail_table(document)
    if table is None:
        return
    total_row = next((row for row in table.rows if "total" in text_of_row(row) or "gesamt" in text_of_row(row)), table.rows[-1])
    template_row = table.rows[1] if len(table.rows) > 1 else total_row
    # Remove all template/example rows between the header and TOTAL.
    for row in list(table.rows[1:]):
        if row._tr is not total_row._tr:
            table._tbl.remove(row._tr)
    for line in sorted(detail_lines, key=lambda item: (item.sort_order, item.id)):
        add_row_before(table, total_row, template_row, [
            line.service_date or "",
            line.timekeeper or "",
            line.description,
            f"{float(line.quantity or 0):g}",
            money(line.amount, draft.currency),
        ])
    if total_row.cells:
        set_cell(total_row.cells[-1], money(sum(float(line.amount or 0) for line in detail_lines), draft.currency))


@login_required
@admin_required
def issue_fee_note_fixed(draft_id):
    draft = db.session.get(FeeNoteDraft, draft_id)
    core.recalculate(draft)
    sequence = FeeNoteDraft.query.filter(FeeNoteDraft.invoice_number.like(f"MARS-INV-{date.today().year}-%")).count() + 1
    number = f"MARS-INV-{date.today().year}-{sequence:04d}"
    language_code = {"English": "EN", "French": "FR", "German": "DE"}[draft.language]
    template = ASSETS / f"MARSIP reference invoice {language_code}.docx"
    if not template.exists():
        flash(f"Template missing: {template.name}")
        return redirect(url_for("edit_fee_note", draft_id=draft.id))

    included = FeeNoteLine.query.filter_by(draft_id=draft.id, included=True).order_by(FeeNoteLine.sort_order, FeeNoteLine.id).all()
    # Hourly lines always populate page 2; manually-created lines can also opt into the detail schedule.
    detail = [line for line in included if line.include_in_detail or line.line_type == "Time-based fees"]

    document = Document(template)
    rebuild_summary(document, draft, included)
    if draft.show_time_schedule:
        rebuild_detail(document, draft, detail)

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
        "[payment due date: eight days/30 days/30 days end of month]": draft.payment_terms,
        "[payment due date: acht Tage/30 Tage/30 Tage zum Monatsende]": draft.payment_terms,
        "[payment due date: huitainte/30 jours/30 jours fin de mois]": draft.payment_terms,
        "Final Total Including VAT": money(draft.gross_total, draft.currency),
        "Total Final TTC": money(draft.gross_total, draft.currency),
    }
    replace_everywhere(document, replacements)

    output = BytesIO()
    document.save(output)
    output.seek(0)
    draft.invoice_number = number
    draft.status = "Issued"
    draft.issued_at = utcnow()
    for line in included:
        if line.source_timer_id:
            timer = db.session.get(Timer, line.source_timer_id)
            if timer:
                timer.invoiced_status = "Invoiced"
                timer.invoice_number = number
    db.session.commit()
    return send_file(
        output,
        as_attachment=True,
        download_name=f"{number}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# Replace only the issue endpoint; its existing URL and editor button continue to work.
app.view_functions["issue_fee_note"] = issue_fee_note_fixed
