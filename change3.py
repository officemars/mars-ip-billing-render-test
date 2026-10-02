"""MARS-IP Change 3 consolidated deployment fix."""
from datetime import datetime, timezone, date
from flask import request, redirect, url_for, flash, session, render_template_string
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
ASSETS = previous.ASSETS

def fnum(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def checked(name):
    return name in request.form

class AppSetting(db.Model):
    __tablename__ = "app_setting_v5"
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)

with app.app_context():
    db.create_all()

def setting(key, default=""):
    row = db.session.get(AppSetting, key)
    return row.value if row else default

def set_setting(key, value):
    row = db.session.get(AppSetting, key) or AppSetting(key=key)
    row.value = str(value)
    db.session.add(row)
    db.session.commit()

PAYMENT = {
    "English": {"8 days": "eight days", "30 days": "30 days", "30 days end of month": "30 days end of month"},
    "French": {"8 days": "huit jours", "30 days": "30 jours", "30 days end of month": "30 jours fin de mois"},
    "German": {"8 days": "acht Tage", "30 days": "30 Tage", "30 days end of month": "30 Tage zum Monatsende"},
}

def translated_terms(language, code, other=""):
    return other.strip() if code == "other" else PAYMENT.get(language, PAYMENT["English"]).get(code, code)

MASTER = '<h1>Data management</h1>'

@login_required
def master_v4():
    return render_template_string('{% extends "base.html" %}{% block content %}<h1>Data management</h1><p>Use the existing master-data administration tools to import or maintain clients, contacts and matters.</p><p><a href="{{url_for(\'admin_original_v4\')}}">Open full administration</a></p>{% endblock %}')

@app.post("/admin/invoice-sequence-v4")
@login_required
@admin_required
def invoice_sequence_v4():
    set_setting("invoice_prefix", request.form["prefix"])
    set_setting("invoice_next", request.form["next_number"])
    flash("Invoice numbering saved.")
    return redirect(url_for("admin"))

# ==================== CHANGE 3.3: INVOICE REGISTER, LIVE EDITOR, PREVIEW ====================
DEDUCTION_LINE_TYPES={'Provision','Credit / discount'}

def recalculate_v5(draft):
    net=0.0;vat=0.0
    for line in FeeNoteLine.query.filter_by(draft_id=draft.id,included=True).all():
        amount=float(line.amount or 0) if line.amount_override else float(line.quantity or 0)*float(line.rate or 0)
        if line.line_type in DEDUCTION_LINE_TYPES:amount=-abs(amount)
        line.amount=round(amount,2);net+=line.amount
        treatment=(line.vat_treatment or draft.vat_treatment or '').lower()
        rate=0.0 if ('reverse' in treatment or 'exempt' in treatment) else float(line.vat_rate if line.vat_rate is not None else draft.vat_rate or 0)
        vat+=line.amount*rate/100
    if draft.show_telecom:
        telecom=float(draft.telecom_amount or 0);net+=telecom
        if draft.vat_treatment not in ('Reverse charge','Other exemption'):vat+=telecom*float(draft.vat_rate or 0)/100
    draft.net_total=round(net,2);draft.vat_amount=round(vat,2);draft.gross_total=round(net+vat,2);db.session.commit()
core.recalculate=recalculate_v5

FULL_EDITOR_V5=r'''{% extends "base.html" %}{% block content %}
<div style="display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap"><div><h1>Fee note preparation</h1><p>{{draft.invoice_number or 'Draft fee note'}} · {{draft.client_name}} · {{draft.matter_text}}</p></div><div><a href="{{url_for('invoice_archive_v5')}}">Invoice register</a> | <a href="{{url_for('invoice_preview_v5',draft_id=draft.id)}}">Preview</a></div></div>
<section class="card"><h2>Invoice details</h2><form method="post" action="{{url_for('save_invoice_header_v5',draft_id=draft.id)}}"><div class="grid">
<label>Invoice number<input name="invoice_number" value="{{draft.invoice_number or ''}}" placeholder="Assigned automatically when issued"></label>
<label>Language<select id="language" name="language">{% for x in ['English','French','German'] %}<option {% if draft.language==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></label>
<label>Payment deadline<select id="payment_choice" name="payment_choice"><option value="8 days">{{payment_labels[0]}}</option><option value="30 days">{{payment_labels[1]}}</option><option value="30 days end of month">{{payment_labels[2]}}</option><option value="other">{{payment_labels[3]}}</option></select></label>
<label>Other payment deadline<input name="payment_other" placeholder="Complete only if Other is selected"></label>
<label>Billing name / Client ID<input name="billing_name" value="{{draft.billing_name or ''}}"></label><label>Client name<input name="client_name" value="{{draft.client_name or ''}}"></label><label>Billing address<textarea name="billing_address">{{draft.billing_address or ''}}</textarea></label><label>Country<input name="country" value="{{draft.country or ''}}"></label><label>Contact person<input name="contact_name" value="{{draft.contact_name or ''}}"></label><label>Place of issue<input name="issue_place" value="{{draft.issue_place or 'Berlin'}}"></label><label>Document date<input type="date" name="document_date" value="{{draft.document_date or ''}}"></label><label>VAT number<input name="vat_number" value="{{draft.vat_number or ''}}"></label><label>Matter<input name="matter_text" value="{{draft.matter_text or ''}}"></label><label>Engagement letter date<input type="date" name="engagement_date" value="{{draft.engagement_date or ''}}"></label><label>Currency<input id="currency" name="currency" value="{{draft.currency or 'EUR'}}"></label><label>Default VAT treatment<select name="vat_treatment">{% for x in ['Standard VAT','Reverse charge','Other exemption','Free data entry'] %}<option {% if draft.vat_treatment==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></label><label>Default VAT %<input type="number" step="0.01" name="vat_rate" value="{{draft.vat_rate or 0}}"></label><label>VAT label<input name="vat_label" value="{{draft.vat_label or 'VAT'}}"></label><label>Custom VAT wording<textarea name="vat_wording">{{draft.vat_wording or ''}}</textarea></label><label>Telecommunications description<textarea name="telecom_description">{{draft.telecom_description or ''}}</textarea></label><label>Telecommunications amount<input id="telecom_amount" type="number" step="0.01" name="telecom_amount" value="{{draft.telecom_amount or 0}}"></label></div>
<div class="grid"><label class="check"><input type="checkbox" name="show_vat_number" {% if draft.show_vat_number %}checked{% endif %}> Show VAT number</label><label class="check"><input type="checkbox" name="show_engagement" {% if draft.show_engagement %}checked{% endif %}> Show engagement sentence</label><label class="check"><input type="checkbox" name="show_time_schedule" {% if draft.show_time_schedule %}checked{% endif %}> Show detailed services schedule</label><label class="check"><input id="show_telecom" type="checkbox" name="show_telecom" {% if draft.show_telecom %}checked{% endif %}> Include telecommunications</label></div><button>Save invoice details</button></form></section>
<section class="card wide"><h2>Professional services and invoice components</h2><p>Changes to quantity, rate, VAT or inclusion update the totals below immediately. Select Save on the relevant row to store the change.</p><table><tr><th>Order</th><th>Include</th><th>Type</th><th>Service</th><th>Date</th><th>Employee</th><th>Hours / quantity</th><th>Rate</th><th>Manual amount</th><th>Net amount</th><th>VAT treatment</th><th>VAT %</th><th>Detailed schedule</th><th></th></tr>
{% for line in lines %}<tr class="bill-line"><form method="post" action="{{url_for('save_line_v5',line_id=line.id)}}"><td><input class="order" type="number" name="sort_order" value="{{line.sort_order}}"></td><td><input class="included" type="checkbox" name="included" {% if line.included %}checked{% endif %}></td><td><select class="line-type" name="line_type">{% for x in line_types %}<option {% if line.line_type==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></td><td><textarea name="description">{{line.description}}</textarea></td><td><input name="service_date" value="{{line.service_date or ''}}"></td><td><input name="timekeeper" value="{{line.timekeeper or ''}}"></td><td><input class="qty" type="number" step="0.0001" name="quantity" value="{{line.quantity or 0}}"></td><td><input class="rate" type="number" step="0.01" name="rate" value="{{line.rate or 0}}"></td><td><input class="override" type="checkbox" name="amount_override" {% if line.amount_override %}checked{% endif %}></td><td><input class="amount" type="number" step="0.01" name="amount" value="{{line.amount or 0}}"></td><td><select class="vat-treatment" name="vat_treatment">{% for x in ['Standard VAT','Reverse charge','Other exemption','Free data entry'] %}<option {% if (line.vat_treatment or draft.vat_treatment)==x %}selected{% endif %}>{{x}}</option>{% endfor %}</select></td><td><input class="vat-rate" type="number" step="0.01" name="vat_rate" value="{{line.vat_rate if line.vat_rate is not none else draft.vat_rate}}"></td><td><input type="checkbox" name="include_in_detail" {% if line.include_in_detail %}checked{% endif %}></td><td><button>Save</button></form><form method="post" action="{{url_for('delete_line_v5',line_id=line.id)}}"><button class="danger">Remove</button></form></td></tr>{% endfor %}</table>
<h3>Add invoice component</h3><form method="post" action="{{url_for('add_line_v5',draft_id=draft.id)}}"><div class="grid"><label>Type<select name="line_type">{% for x in line_types %}<option>{{x}}</option>{% endfor %}</select></label><label>Service description<textarea name="description" required></textarea></label><label>Date<input name="service_date"></label><label>Employee<input name="timekeeper"></label><label>Hours / quantity<input type="number" step="0.0001" name="quantity" value="1"></label><label>Rate<input type="number" step="0.01" name="rate" value="0"></label><label>Manual net amount<input type="number" step="0.01" name="amount" value="0"></label><label>VAT treatment<select name="vat_treatment"><option>Standard VAT</option><option>Reverse charge</option><option>Other exemption</option><option>Free data entry</option></select></label><label>VAT %<input type="number" step="0.01" name="vat_rate" value="{{draft.vat_rate or 0}}"></label><label class="check"><input type="checkbox" name="amount_override"> Use manual amount</label><label class="check"><input type="checkbox" name="include_in_detail"> Show in detailed schedule</label></div><button>Add component</button></form></section>
<section class="card" style="position:sticky;bottom:0;border:2px solid #ee7933"><div class="grid"><h3>Net: <span id="live_net">{{draft.net_total}}</span> <span class="live_currency">{{draft.currency}}</span></h3><h3>VAT: <span id="live_vat">{{draft.vat_amount}}</span> <span class="live_currency">{{draft.currency}}</span></h3><h2>Total gross: <span id="live_gross">{{draft.gross_total}}</span> <span class="live_currency">{{draft.currency}}</span></h2></div><a href="{{url_for('invoice_preview_v5',draft_id=draft.id)}}" style="display:inline-block;background:#273746;color:white;padding:10px 16px;border-radius:6px;text-decoration:none">Preview invoice</a></section>
<script>
function liveTotals(){let net=0,vat=0;document.querySelectorAll('.bill-line').forEach(row=>{if(!row.querySelector('.included').checked)return;let type=row.querySelector('.line-type').value,qty=parseFloat(row.querySelector('.qty').value)||0,rate=parseFloat(row.querySelector('.rate').value)||0,manual=row.querySelector('.override').checked,amount=manual?(parseFloat(row.querySelector('.amount').value)||0):qty*rate;if(type==='Provision'||type==='Credit / discount')amount=-Math.abs(amount);row.querySelector('.amount').value=amount.toFixed(2);net+=amount;let treatment=row.querySelector('.vat-treatment').value,v=parseFloat(row.querySelector('.vat-rate').value)||0;if(treatment==='Reverse charge'||treatment==='Other exemption')v=0;vat+=amount*v/100;});if(document.getElementById('show_telecom').checked){let t=parseFloat(document.getElementById('telecom_amount').value)||0;net+=t;vat+=t*({{draft.vat_rate or 0}})/100;}document.getElementById('live_net').textContent=net.toFixed(2);document.getElementById('live_vat').textContent=vat.toFixed(2);document.getElementById('live_gross').textContent=(net+vat).toFixed(2);document.querySelectorAll('.live_currency').forEach(x=>x.textContent=document.getElementById('currency').value||'EUR');}
document.querySelectorAll('.bill-line input,.bill-line select,#show_telecom,#telecom_amount,#currency').forEach(x=>x.addEventListener('input',liveTotals));liveTotals();
</script>{% endblock %}'''

def payment_labels(language):return {'English':['8 days','30 days','30 days end of month','Other'],'French':['8 jours','30 jours','30 jours fin de mois','Autre'],'German':['8 Tage','30 Tage','30 Tage zum Monatsende','Autre']}.get(language,['8 days','30 days','30 days end of month','Other'])

@login_required
@admin_required
def full_editor_v5(draft_id):
    d=db.session.get(FeeNoteDraft,draft_id);recalculate_v5(d);lines=FeeNoteLine.query.filter_by(draft_id=d.id).order_by(FeeNoteLine.sort_order,FeeNoteLine.id).all()
    return render_template_string(FULL_EDITOR_V5,draft=d,lines=lines,line_types=LINE_TYPES,payment_labels=payment_labels(d.language))
app.view_functions['edit_fee_note']=full_editor_v5
# The prior full-edit link now resolves to the same complete editor.
app.view_functions['edit_fee_note_original_v4']=full_editor_v5

@app.post('/invoices/<int:draft_id>/header-v5')
@login_required
@admin_required
def save_invoice_header_v5(draft_id):
    d=db.session.get(FeeNoteDraft,draft_id)
    for field in ['language','billing_name','client_name','billing_address','country','contact_name','issue_place','document_date','vat_number','matter_text','engagement_date','currency','vat_treatment','vat_label','vat_wording','telecom_description']:
        setattr(d,field,request.form.get(field,''))
    number=request.form.get('invoice_number','').strip()
    if number:d.invoice_number=number
    d.payment_terms=translated_terms(d.language,request.form.get('payment_choice'),request.form.get('payment_other',''))
    d.vat_rate=fnum(request.form.get('vat_rate'));d.telecom_amount=fnum(request.form.get('telecom_amount'))
    for field in ['show_vat_number','show_engagement','show_time_schedule','show_telecom']:setattr(d,field,checked(field))
    db.session.commit();recalculate_v5(d);flash('Invoice details saved.');return redirect(url_for('edit_fee_note',draft_id=d.id))

def apply_line_v5(line,draft):
    line.sort_order=int(request.form.get('sort_order') or line.sort_order or 0);line.included=checked('included');line.line_type=request.form.get('line_type',line.line_type);line.description=request.form.get('description','').strip();line.service_date=request.form.get('service_date','');line.timekeeper=request.form.get('timekeeper','');line.quantity=fnum(request.form.get('quantity'));line.rate=fnum(request.form.get('rate'));line.amount_override=checked('amount_override');line.amount=fnum(request.form.get('amount')) if line.amount_override else line.quantity*float(line.rate or 0);line.vat_treatment=request.form.get('vat_treatment') or draft.vat_treatment;line.vat_rate=0 if line.vat_treatment in ('Reverse charge','Other exemption') else fnum(request.form.get('vat_rate'));line.include_in_detail=checked('include_in_detail')

@app.post('/invoice-lines/<int:line_id>/save-v5')
@login_required
@admin_required
def save_line_v5(line_id):
    line=db.session.get(FeeNoteLine,line_id);apply_line_v5(line,line.draft);db.session.commit();recalculate_v5(line.draft);return redirect(url_for('edit_fee_note',draft_id=line.draft_id))
@app.post('/invoices/<int:draft_id>/add-line-v5')
@login_required
@admin_required
def add_line_v5(draft_id):
    d=db.session.get(FeeNoteDraft,draft_id);order=(db.session.query(db.func.max(FeeNoteLine.sort_order)).filter_by(draft_id=d.id).scalar() or 0)+1;line=FeeNoteLine(draft_id=d.id,sort_order=order,included=True,description=request.form['description']);apply_line_v5(line,d);line.included=True;db.session.add(line);db.session.commit();recalculate_v5(d);return redirect(url_for('edit_fee_note',draft_id=d.id))
@app.post('/invoice-lines/<int:line_id>/delete-v5')
@login_required
@admin_required
def delete_line_v5(line_id):
    line=db.session.get(FeeNoteLine,line_id);did=line.draft_id;db.session.delete(line);db.session.commit();d=db.session.get(FeeNoteDraft,did);recalculate_v5(d);return redirect(url_for('edit_fee_note',draft_id=did))

ARCHIVE=r'''{% extends "base.html" %}{% block content %}<div style="display:flex;justify-content:space-between;align-items:center"><div><h1>Invoice register</h1><p>All draft and issued fee notes stored in Neon.</p></div><a href="{{url_for('invoice_new')}}">Create fee note</a></div><section class="card wide"><table><tr><th>Invoice number</th><th>Status</th><th>Client</th><th>Matter</th><th>Language</th><th>Date</th><th>Net</th><th>VAT</th><th>Total</th><th>Actions</th></tr>{% for d in drafts %}<tr><td>{{d.invoice_number or 'Not issued'}}</td><td>{{d.status}}</td><td>{{d.client_name}}</td><td>{{d.matter_text}}</td><td>{{d.language}}</td><td>{{d.document_date}}</td><td>{{d.net_total}} {{d.currency}}</td><td>{{d.vat_amount}} {{d.currency}}</td><td>{{d.gross_total}} {{d.currency}}</td><td><a href="{{url_for('edit_fee_note',draft_id=d.id)}}">Edit</a> | <a href="{{url_for('invoice_preview_v5',draft_id=d.id)}}">Preview</a></td></tr>{% else %}<tr><td colspan="10">No invoices found.</td></tr>{% endfor %}</table></section>{% endblock %}'''
@app.get('/invoices/archive-v5')
@login_required
@admin_required
def invoice_archive_v5():return render_template_string(ARCHIVE,drafts=FeeNoteDraft.query.order_by(FeeNoteDraft.id.desc()).all())

PREVIEW=r'''{% extends "base.html" %}{% block content %}<div style="display:flex;justify-content:space-between;align-items:center"><h1>Invoice preview</h1><a href="{{url_for('edit_fee_note',draft_id=draft.id)}}">Return to edit</a></div><section class="card" style="max-width:850px;margin:auto;padding:45px"><h2>Fee Note {{draft.invoice_number or '(number assigned on issue)'}}</h2><p style="white-space:pre-line">{{draft.billing_name}}\n{{draft.billing_address}}\n{{draft.country}}\n{{draft.contact_name}}</p><p>{{draft.issue_place}}, {{draft.document_date}}</p>{% if draft.show_vat_number %}<p><b>Your VAT number: {{draft.vat_number}}</b></p>{% endif %}<p><b>Matter: {{draft.client_name}} | {{draft.matter_text}}</b></p>{% if draft.show_engagement %}<p><i>According to our Engagement Letter dated {{draft.engagement_date}}</i></p>{% endif %}<table><tr><th>Fees</th><th>Net amount</th></tr>{% for l in lines if l.included %}<tr><td>{{l.description}}</td><td>{{'%.2f'|format(l.amount|float)}} {{draft.currency}}</td></tr>{% endfor %}{% if draft.show_telecom %}<tr><td>{{draft.telecom_description}}</td><td>{{'%.2f'|format(draft.telecom_amount|float)}} {{draft.currency}}</td></tr>{% endif %}<tr><td><b>TOTAL NET</b></td><td><b>{{draft.net_total}} {{draft.currency}}</b></td></tr><tr><td>{{draft.vat_label}} {{draft.vat_rate}}%</td><td>{{draft.vat_amount}} {{draft.currency}}</td></tr><tr><td><b>TOTAL GROSS</b></td><td><b>{{draft.gross_total}} {{draft.currency}}</b></td></tr></table><p><b>Payment due within {{draft.payment_terms}} from receipt of this Fee Note.</b></p>{% if draft.show_time_schedule %}<h3>Detailed services schedule</h3><table><tr><th>Date</th><th>Timekeeper</th><th>Services</th><th>Time</th><th>Net amount</th></tr>{% for l in detail %}<tr><td>{{l.service_date}}</td><td>{{l.timekeeper}}</td><td>{{l.description}}</td><td>{{l.quantity}}</td><td>{{l.amount}} {{draft.currency}}</td></tr>{% endfor %}</table>{% endif %}<form method="post" action="{{url_for('issue_fee_note',draft_id=draft.id)}}"><button>{{'Download updated Word invoice' if draft.invoice_number else 'Issue and download Word invoice'}}</button></form></section>{% endblock %}'''
@app.get('/invoices/<int:draft_id>/preview-v5')
@login_required
@admin_required
def invoice_preview_v5(draft_id):
    d=db.session.get(FeeNoteDraft,draft_id);recalculate_v5(d);lines=FeeNoteLine.query.filter_by(draft_id=d.id).order_by(FeeNoteLine.sort_order,FeeNoteLine.id).all();detail=[x for x in lines if x.included and (x.include_in_detail or x.line_type=='Time-based fees')];return render_template_string(PREVIEW,draft=d,lines=lines,detail=detail)

# Data Management page title takes precedence over the previous title.
MASTER=MASTER.replace('<h1>Clients, contacts and matters</h1>','<h1>Data management</h1><p>Import, add, correct and map clients, contacts and matters.</p>')

# Admin landing page with direct access to invoice archive and Data Management.
def admin_landing_v5():
    return render_template_string(r'''{% extends "base.html" %}{% block content %}<h1>Administration</h1><div class="cards"><section class="card"><h2>Invoice register</h2><p>Review, edit, preview and download all previous fee notes.</p><a href="{{url_for('invoice_archive_v5')}}">Open invoice register</a></section><section class="card"><h2>Data management</h2><p>Import Excel data and add or correct clients, contacts, matters and mappings.</p><a href="{{url_for('master_v4')}}">Open Data Management</a></section><section class="card"><h2>Users and rates</h2><p>Manage users, rates, matter assignments and recorded time.</p><a href="{{url_for('admin_original_v4')}}">Open user administration</a></section></div><section class="card"><h2>Invoice numbering</h2><form method="post" action="{{url_for('invoice_sequence_v4')}}"><div class="grid"><label>Invoice prefix<input name="prefix" value="{{prefix}}"></label><label>Next minimum number<input type="number" min="1" name="next_number" value="{{next_number}}"></label></div><button>Save numbering</button></form></section>{% endblock %}''',prefix=setting('invoice_prefix','MARS-INV-'+str(date.today().year)+'-'),next_number=setting('invoice_next','1'))
app.view_functions['admin']=admin_landing_v5


# CHANGE 3.4 login safety: retain the proven authentication implementation from app.py.
app.view_functions["login"] = core.login
