"""MARS-IP Change 3.

Upload beside app.py and change2.py, then set Render Start Command to:
    gunicorn change3:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120
"""
from flask import render_template_string, request, redirect, url_for, flash, jsonify, session
import change2 as previous
import app as core

app = previous.app
db = previous.db
User = previous.User
Client = previous.Client
Contact = previous.Contact
Matter = previous.Matter
Timer = previous.Timer
FeeNoteDraft = previous.FeeNoteDraft
FeeNoteLine = previous.FeeNoteLine
login_required = previous.login_required
admin_required = previous.admin_required
LINE_TYPES = core.LINE_TYPES

DEDUCTION_TYPES = {"Provision", "Credit / discount"}


def signed_amount(line_type, value):
    value = abs(float(value or 0))
    return -value if line_type in DEDUCTION_TYPES else value


def recalculate_v3(draft):
    included = FeeNoteLine.query.filter_by(draft_id=draft.id, included=True).all()
    net = 0.0
    vat = 0.0
    for line in included:
        raw = float(line.amount or 0) if line.amount_override else float(line.quantity or 0) * float(line.rate or 0)
        line.amount = round(signed_amount(line.line_type, raw), 2)
        net += float(line.amount)
        treatment = (line.vat_treatment or draft.vat_treatment or "").lower()
        rate = 0.0 if "reverse" in treatment or "exempt" in treatment else float(line.vat_rate if line.vat_rate is not None else draft.vat_rate or 0)
        vat += float(line.amount) * rate / 100
    if draft.show_telecom:
        telecom = float(draft.telecom_amount or 0)
        net += telecom
        if draft.vat_treatment != "Reverse charge":
            vat += telecom * float(draft.vat_rate or 0) / 100
    draft.net_total = round(net, 2)
    draft.vat_amount = round(vat, 2)
    draft.gross_total = round(net + vat, 2)
    db.session.commit()


# Change 2's Word issuing function calls core.recalculate. Point it to the corrected calculation.
core.recalculate = recalculate_v3


SELECTOR = r'''{% extends "base.html" %}{% block content %}
<h1>Create draft fee note</h1><section class="card"><form method="post">
<div class="grid"><label>Client<select id="client" name="client_id" required><option value="">Select</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label>
<label>Matter / project<select id="matter" name="matter_id"><option value="">All matters</option></select></label>
<label>Contact<select id="contact" name="contact_id"><option value="">None / free entry</option></select></label>
<label>Language<select name="language"><option>English</option><option>French</option><option>German</option></select></label></div>
<div id="workers" class="flash" style="display:none"></div>
<h2>Available completed, unbilled timekeeper entries</h2><p>Select exactly which entries belong in the invoice. The service, timekeeper, hours, rate, amount and VAT remain editable in the next screen.</p>
<div id="timers"><p>Select a client.</p></div><button>Create prefilled editable draft</button></form></section>
<script>
let allTimers=[];
function draw(){
 const matterId=matter.value;
 const visible=allTimers.filter(x=>!matterId||String(x.matter_id)===String(matterId));
 const names=[...new Set(visible.map(x=>x.timekeeper).filter(Boolean))];
 workers.style.display=visible.length?'block':'none';
 workers.textContent=visible.length?'Employees with available entries: '+names.join(', '):'';
 timers.innerHTML=visible.map(x=>`<label class="check"><input type="checkbox" name="timer_ids" value="${x.id}" checked> <b>${x.date}</b> | ${x.timekeeper} | ${x.matter} | ${x.services} | ${x.billable_hours} h × ${x.rate} = ${x.amount}</label>`).join('')||'<p>No matching completed unbilled entries.</p>';
}
client.onchange=async()=>{let d=await(await fetch('/api/invoice-context-v3/'+client.value)).json();allTimers=d.timers;matter.innerHTML='<option value="">All matters</option>'+d.matters.map(x=>`<option value="${x.id}">${x.reference||''} | ${x.name}</option>`).join('');contact.innerHTML='<option value="">None / free entry</option>'+d.contacts.map(x=>`<option value="${x.id}">${x.display_name}</option>`).join('');draw()};
matter.onchange=draw;
</script>{% endblock %}'''


EDITOR = r'''{% extends "base.html" %}{% block content %}
<h1>Edit draft fee note</h1>
<section class="card"><form method="post" action="{{url_for('update_fee_note',draft_id=draft.id)}}"><div class="grid">
<label>Language<select name="language">{% for x in ['English','French','German'] %}<option {% if draft.language==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></label>
<label>Billing name / ClientId<input name="billing_name" value="{{draft.billing_name or ''}}"></label><label>Client name<input name="client_name" value="{{draft.client_name or ''}}"></label>
<label>Billing address<textarea name="billing_address">{{draft.billing_address or ''}}</textarea></label><label>Country<input name="country" value="{{draft.country or ''}}"></label><label>Contact person<input name="contact_name" value="{{draft.contact_name or ''}}"></label>
<label>Place of issue<input name="issue_place" value="{{draft.issue_place or 'Berlin'}}"></label><label>Document date<input type="date" name="document_date" value="{{draft.document_date or ''}}"></label><label>VAT number<input name="vat_number" value="{{draft.vat_number or ''}}"></label>
<label>Matter<input name="matter_text" value="{{draft.matter_text or ''}}"></label><label>Engagement date<input type="date" name="engagement_date" value="{{draft.engagement_date or ''}}"></label><label>Payment terms<input name="payment_terms" value="{{draft.payment_terms or ''}}"></label>
<label>Currency<input name="currency" value="{{draft.currency or 'EUR'}}"></label><label>Default VAT treatment<select name="vat_treatment">{% for x in ['Standard VAT','Reverse charge','Other exemption','Free data entry'] %}<option {% if draft.vat_treatment==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></label><label>Default VAT rate %<input type="number" step="0.01" name="vat_rate" value="{{draft.vat_rate or 0}}"></label>
<label>VAT label<input name="vat_label" value="{{draft.vat_label or 'VAT'}}"></label><label>Custom VAT wording<textarea name="vat_wording">{{draft.vat_wording or ''}}</textarea></label><label>Telecom description<textarea name="telecom_description">{{draft.telecom_description or ''}}</textarea></label><label>Telecom amount<input type="number" step="0.01" name="telecom_amount" value="{{draft.telecom_amount or 0}}"></label>
<label>Annex title<input name="annex_title" value="{{draft.annex_title or ''}}"></label><label>Annex text<textarea name="annex_text">{{draft.annex_text or ''}}</textarea></label></div>
<div class="grid"><label class="check"><input type="checkbox" name="show_vat_number" {% if draft.show_vat_number %}checked{% endif %}> Show VAT number</label><label class="check"><input type="checkbox" name="show_engagement" {% if draft.show_engagement %}checked{% endif %}> Show engagement sentence</label><label class="check"><input type="checkbox" name="show_time_schedule" {% if draft.show_time_schedule %}checked{% endif %}> Show page-2 schedule</label><label class="check"><input type="checkbox" name="show_telecom" {% if draft.show_telecom %}checked{% endif %}> Show telecom line</label></div><button>Save header and recalculate</button></form></section>
<section class="card wide"><h2>Editable bill lines and page-2 schedule</h2><p>Tick <b>Include</b> to bill a line. Tick <b>Page 2</b> to show the line in the detailed schedule. Time-based entries are preselected for Page 2. Each line may have its own VAT treatment and rate.</p>
<table><tr><th>Order</th><th>Include</th><th>Type</th><th>Service</th><th>Date</th><th>Employee</th><th>Hours/qty</th><th>Rate</th><th>Manual amount</th><th>Net</th><th>VAT treatment</th><th>VAT %</th><th>Page 2</th><th></th></tr>
{% for line in lines %}<tr><form method="post" action="{{url_for('update_fee_line',line_id=line.id)}}"><td><input type="number" name="sort_order" value="{{line.sort_order}}"></td><td><input type="checkbox" name="included" {% if line.included %}checked{% endif %}></td><td><select name="line_type">{% for x in line_types %}<option {% if line.line_type==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></td><td><textarea name="description">{{line.description}}</textarea></td><td><input name="service_date" value="{{line.service_date or ''}}"></td><td><input name="timekeeper" value="{{line.timekeeper or ''}}"></td><td><input type="number" step="0.0001" name="quantity" value="{{line.quantity or 0}}"></td><td><input type="number" step="0.01" name="rate" value="{{line.rate or 0}}"></td><td><input type="checkbox" name="amount_override" {% if line.amount_override %}checked{% endif %}></td><td><input type="number" step="0.01" name="amount" value="{{line.amount or 0}}"></td><td><select name="vat_treatment">{% for x in ['Standard VAT','Reverse charge','Other exemption','Free data entry'] %}<option {% if (line.vat_treatment or draft.vat_treatment)==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></td><td><input type="number" step="0.01" name="vat_rate" value="{{line.vat_rate if line.vat_rate is not none else draft.vat_rate}}"></td><td><input type="checkbox" name="include_in_detail" {% if line.include_in_detail %}checked{% endif %}></td><td><button>Save</button></form><form method="post" action="{{url_for('delete_fee_line',line_id=line.id)}}"><button class="danger">Remove</button></form></td></tr>{% endfor %}</table>
<h3>Add custom line</h3><form method="post" action="{{url_for('add_fee_line',draft_id=draft.id)}}"><div class="grid"><label>Type<select name="line_type">{% for x in line_types %}<option>{{x}}</option>{% endfor %}</select></label><label>Service<textarea name="description" required></textarea></label><label>Date<input name="service_date"></label><label>Employee<input name="timekeeper"></label><label>Hours/quantity<input type="number" step="0.0001" name="quantity" value="1"></label><label>Rate<input type="number" step="0.01" name="rate" value="0"></label><label>Net amount<input type="number" step="0.01" name="amount" value="0"></label><label>VAT treatment<select name="vat_treatment"><option>Standard VAT</option><option>Reverse charge</option><option>Other exemption</option><option>Free data entry</option></select></label><label>VAT %<input type="number" step="0.01" name="vat_rate" value="{{draft.vat_rate or 0}}"></label><label class="check"><input type="checkbox" name="amount_override"> Keep manual amount</label><label class="check"><input type="checkbox" name="include_in_detail"> Show on page 2</label></div><button>Add line</button></form></section>
<section class="card"><p>Net: <b>{{draft.net_total}} {{draft.currency}}</b></p><p>VAT across included components: <b>{{draft.vat_amount}} {{draft.currency}}</b></p><h2>Total gross: {{draft.gross_total}} {{draft.currency}}</h2>{% if draft.status=='Draft' %}<form method="post" action="{{url_for('issue_fee_note',draft_id=draft.id)}}"><button>Issue and download Word fee note</button></form>{% else %}<p>Issued as {{draft.invoice_number}}</p>{% endif %}</section>
{% endblock %}'''


@app.get("/api/invoice-context-v3/<int:client_id>")
@login_required
@admin_required
def invoice_context_v3(client_id):
    timers = Timer.query.filter_by(client_id=client_id, status="Stopped", billable=True, invoiced_status="Unbilled").order_by(Timer.start_time, Timer.id).all()
    return jsonify({
        "matters": [{"id": m.id, "reference": m.reference, "name": m.name} for m in Matter.query.filter_by(client_id=client_id).order_by(Matter.name).all()],
        "contacts": [{"id": c.id, "display_name": c.display_name} for c in Contact.query.filter(db.or_(Contact.client_id == client_id, Contact.client_id.is_(None))).order_by(Contact.display_name).all()],
        "timers": [{
            "id": t.id, "matter_id": t.matter_id, "date": t.start_time.date().isoformat(),
            "timekeeper": t.user.display_name, "matter": t.matter.name, "services": t.services,
            "billable_hours": float(t.billable_hours or 0), "rate": float(t.applied_rate or 0),
            "amount": float(t.amount or 0),
        } for t in timers],
    })


@login_required
@admin_required
def invoice_new_v3():
    if request.method == "GET":
        return render_template_string(SELECTOR, clients=Client.query.order_by(Client.name).all())
    client = db.session.get(Client, int(request.form["client_id"]))
    matter = db.session.get(Matter, int(request.form["matter_id"])) if request.form.get("matter_id") else None
    contact = db.session.get(Contact, int(request.form["contact_id"])) if request.form.get("contact_id") else None
    language = request.form.get("language") or client.language or "English"
    telecom = {
        "English": "Post and telecommunications flat rate pursuant to statutory provisions (No. 7002 RVG).",
        "French": "Forfait de télécommunications conformément aux dispositions légales (n° 7002 RVG).",
        "German": "Telekommunikationspauschale entsprechend den gesetzlichen Regelungen (Nr. 7002 RVG).",
    }[language]
    treatment = "Reverse charge" if "reverse" in (client.vat_treatment or "").lower() else "Standard VAT"
    draft = FeeNoteDraft(
        client=client, matter=matter, contact=contact, language=language,
        billing_name=client.billing_name or client.name, billing_address=client.address,
        country=client.country, contact_name=contact.display_name if contact else "",
        issue_place="Berlin", document_date=core.date.today().isoformat(), vat_number=client.vat_number,
        client_name=client.name, matter_text=matter.name if matter else "",
        payment_terms=(matter.payment_terms if matter and matter.payment_terms else client.payment_terms),
        currency=(matter.currency if matter and matter.currency else client.currency or "EUR"),
        vat_treatment=treatment, vat_rate=0 if treatment == "Reverse charge" else 20,
        telecom_description=telecom, telecom_amount=20, created_by=session["uid"],
    )
    db.session.add(draft)
    db.session.flush()
    selected_ids = [int(value) for value in request.form.getlist("timer_ids")]
    selected = Timer.query.filter(Timer.id.in_(selected_ids)).order_by(Timer.start_time, Timer.id).all() if selected_ids else []
    for order, timer in enumerate(selected, 1):
        db.session.add(FeeNoteLine(
            draft=draft, sort_order=order, included=True, line_type="Time-based fees",
            description=timer.services, service_date=timer.start_time.date().isoformat(),
            timekeeper=timer.user.display_name, quantity=float(timer.billable_hours or 0),
            rate=float(timer.applied_rate or 0), amount=float(timer.amount or 0),
            amount_override=False, vat_treatment=treatment, vat_rate=draft.vat_rate,
            include_in_detail=True, source_timer_id=timer.id,
        ))
    db.session.commit()
    recalculate_v3(draft)
    return redirect(url_for("edit_fee_note", draft_id=draft.id))


app.view_functions["invoice_new"] = invoice_new_v3


@login_required
@admin_required
def edit_fee_note_v3(draft_id):
    draft = db.session.get(FeeNoteDraft, draft_id)
    lines = FeeNoteLine.query.filter_by(draft_id=draft_id).order_by(FeeNoteLine.sort_order, FeeNoteLine.id).all()
    recalculate_v3(draft)
    return render_template_string(EDITOR, draft=draft, lines=lines, line_types=LINE_TYPES)


app.view_functions["edit_fee_note"] = edit_fee_note_v3


def apply_line_v3(line):
    line.sort_order = int(request.form.get("sort_order") or line.sort_order or 0)
    line.included = "included" in request.form if "included" in request.form or request.endpoint == "update_fee_line_v3" else True
    line.line_type = request.form.get("line_type", line.line_type)
    line.description = request.form.get("description", line.description)
    line.service_date = request.form.get("service_date", "")
    line.timekeeper = request.form.get("timekeeper", "")
    line.quantity = float(request.form.get("quantity") or 0)
    line.rate = float(request.form.get("rate") or 0)
    line.amount_override = "amount_override" in request.form
    entered = float(request.form.get("amount") or 0)
    calculated = line.quantity * float(line.rate)
    line.amount = signed_amount(line.line_type, entered if line.amount_override else calculated)
    line.vat_treatment = request.form.get("vat_treatment", line.draft.vat_treatment)
    line.vat_rate = 0 if line.vat_treatment in {"Reverse charge", "Other exemption"} else float(request.form.get("vat_rate") or 0)
    line.include_in_detail = "include_in_detail" in request.form


@login_required
@admin_required
def update_fee_line_v3(line_id):
    line = db.session.get(FeeNoteLine, line_id)
    apply_line_v3(line)
    db.session.commit()
    recalculate_v3(line.draft)
    return redirect(url_for("edit_fee_note", draft_id=line.draft_id))


app.view_functions["update_fee_line"] = update_fee_line_v3


@login_required
@admin_required
def add_fee_line_v3(draft_id):
    order = (db.session.query(db.func.max(FeeNoteLine.sort_order)).filter_by(draft_id=draft_id).scalar() or 0) + 1
    line = FeeNoteLine(draft_id=draft_id, sort_order=order, included=True, description=request.form["description"])
    apply_line_v3(line)
    line.included = True
    db.session.add(line)
    db.session.commit()
    recalculate_v3(line.draft)
    return redirect(url_for("edit_fee_note", draft_id=draft_id))


app.view_functions["add_fee_line"] = add_fee_line_v3


@login_required
@admin_required
def update_fee_note_v3(draft_id):
    draft = db.session.get(FeeNoteDraft, draft_id)
    for field in ["language", "billing_name", "client_name", "billing_address", "country", "contact_name", "issue_place", "document_date", "vat_number", "matter_text", "engagement_date", "payment_terms", "currency", "vat_treatment", "vat_label", "vat_wording", "telecom_description", "annex_title", "annex_text"]:
        setattr(draft, field, request.form.get(field, ""))
    draft.vat_rate = float(request.form.get("vat_rate") or 0)
    draft.telecom_amount = float(request.form.get("telecom_amount") or 0)
    for field in ["show_vat_number", "show_engagement", "show_time_schedule", "show_telecom"]:
        setattr(draft, field, field in request.form)
    # Update lines that were inheriting the old draft VAT rate/treatment.
    for line in draft.fee_lines:
        if not line.vat_treatment:
            line.vat_treatment = draft.vat_treatment
            line.vat_rate = draft.vat_rate
    db.session.commit()
    recalculate_v3(draft)
    return redirect(url_for("edit_fee_note", draft_id=draft.id))


app.view_functions["update_fee_note"] = update_fee_note_v3
