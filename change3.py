"""MARS-IP consolidated Change 3.
Replaces the prior change3.py while keeping app.py and change2.py unchanged.
Render: gunicorn change3:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120
"""
from datetime import datetime, timezone, timedelta, date
from io import BytesIO
from copy import deepcopy
import calendar, re

from flask import request, redirect, url_for, flash, jsonify, session, render_template_string, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from openpyxl import load_workbook
from docx import Document
from docx.table import _Row

import change2 as previous
import app as core

app=previous.app; db=previous.db
User=previous.User; Client=previous.Client; Contact=previous.Contact; Matter=previous.Matter; Timer=previous.Timer
FeeNoteDraft=previous.FeeNoteDraft; FeeNoteLine=previous.FeeNoteLine
login_required=previous.login_required; admin_required=previous.admin_required
ASSETS=previous.ASSETS
LINE_TYPES=core.LINE_TYPES

# ---------- extra persistent settings ----------
class AppSetting(db.Model):
    __tablename__='app_setting_v4'
    key=db.Column(db.String(100),primary_key=True)
    value=db.Column(db.Text)

class PasswordResetRequest(db.Model):
    __tablename__='password_reset_request_v4'
    id=db.Column(db.Integer,primary_key=True)
    email=db.Column(db.String(255),nullable=False,index=True)
    requested_at=db.Column(db.DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    resolved_at=db.Column(db.DateTime(timezone=True)); resolved_by=db.Column(db.Integer,db.ForeignKey('user.id'))

with app.app_context(): db.create_all()

def setting(key,default=''):
    row=db.session.get(AppSetting,key); return row.value if row else default

def set_setting(key,value):
    row=db.session.get(AppSetting,key) or AppSetting(key=key); row.value=str(value); db.session.add(row); db.session.commit()

def fnum(v,default=0):
    try:return float(v)
    except:return default

def dt_local(v):
    if not v:return None
    out=datetime.fromisoformat(v)
    return out.replace(tzinfo=timezone.utc) if out.tzinfo is None else out.astimezone(timezone.utc)

def checked(name):return name in request.form

# ---------- passwords ----------
LOGIN=r'''{% extends "base.html" %}{% block content %}<section class="card narrow"><h1>MARS-IP Time & Billing</h1><form method="post"><label>Email<input type="email" name="email" required></label><label>Password<input type="password" name="password" required></label><button>Sign in</button></form><p><a href="{{url_for('register')}}">Create account</a> | <a href="{{url_for('forgot_v4')}}">Forgot password?</a></p></section>{% endblock %}'''
def login_v4():
    if request.method=='POST':
        u=User.query.filter(db.func.lower(User.email)==request.form['email'].strip().lower(),User.active.is_(True)).first()
        if u and check_password_hash(u.password_hash,request.form['password']): session.clear();session.update(uid=u.id,name=u.display_name,role=u.system_role);return redirect(url_for('timer'))
        flash('Invalid login details.')
    return render_template_string(LOGIN)
app.view_functions['login']=login_v4

@app.route('/account/password-v4',methods=['GET','POST'])
@login_required
def password_v4():
    if request.method=='POST':
        u=db.session.get(User,session['uid']);old=request.form['old'];new=request.form['new'];confirm=request.form['confirm']
        if not check_password_hash(u.password_hash,old):flash('Current password is incorrect.')
        elif len(new)<10 or new!=confirm:flash('Use matching passwords with at least 10 characters.')
        else:u.password_hash=generate_password_hash(new);db.session.commit();flash('Password changed.');return redirect(url_for('timer'))
    return render_template_string(r'''{% extends "base.html" %}{% block content %}<section class="card narrow"><h1>Change password</h1><form method="post"><label>Current password<input type="password" name="old" required></label><label>New password<input type="password" minlength="10" name="new" required></label><label>Confirm<input type="password" minlength="10" name="confirm" required></label><button>Save new password</button></form></section>{% endblock %}''')

@app.route('/forgot-password-v4',methods=['GET','POST'])
def forgot_v4():
    if request.method=='POST':
        email=request.form['email'].strip().lower()
        if User.query.filter(db.func.lower(User.email)==email).first() and not PasswordResetRequest.query.filter_by(email=email,resolved_at=None).first():db.session.add(PasswordResetRequest(email=email));db.session.commit()
        flash('If the account exists, the administrator has received a reset request.');return redirect(url_for('login'))
    return render_template_string(r'''{% extends "base.html" %}{% block content %}<section class="card narrow"><h1>Forgot password</h1><form method="post"><label>Account email<input type="email" name="email" required></label><button>Request reset</button></form></section>{% endblock %}''')

# ---------- timer: editable service, custom start/end, no category ----------
TIMER_PAGE=r'''{% extends "base.html" %}{% block content %}<div style="display:flex;justify-content:space-between;align-items:center"><div><h1>Time recording</h1><p>Record current time or enter a custom date and duration.</p></div><a href="{{url_for('password_v4')}}">Change password</a></div>
<section class="card"><form method="post" action="{{url_for('start_timer_v4')}}"><div class="grid"><label>Client<select id="client" name="client_id" required><option value="">Select</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Matter<select id="matter" name="matter_id" required></select></label><label>Entry method<select id="mode" name="mode"><option value="live">Start timer now</option><option value="custom_running">Start timer at custom date/time</option><option value="custom_complete">Enter completed duration</option></select></label><label>Custom start<input type="datetime-local" name="custom_start"></label><label>Custom end<input type="datetime-local" name="custom_end"></label><label>Custom duration hours<input type="number" min="0" step="0.0001" name="custom_hours"></label><label>Billable<select name="billable"><option value="1">Yes</option><option value="0">No</option></select></label></div><label>Service description<textarea name="services" required></textarea></label><label>Internal notes<input name="notes"></label><button>Save / start</button></form></section>
<h2>Active entries</h2><div class="cards">{% for t in active %}<section class="card"><h3>{{t.client.name}}</h3><p>{{t.matter.name}}</p><form method="post" action="{{url_for('edit_service_v4',timer_id=t.id)}}"><label>Service description<textarea name="services" required>{{t.services}}</textarea></label><label>Notes<input name="notes" value="{{t.notes or ''}}"></label><button>Save description</button></form><div class="clock" data-s="{{t.seconds}}" data-run="{{1 if t.status=='Running' else 0}}"></div><p>{{t.status}}</p>{% if t.status=='Running' %}<form method="post" action="{{url_for('pause_timer',timer_id=t.id)}}"><button>Pause</button></form>{% else %}<form method="post" action="{{url_for('resume_timer',timer_id=t.id)}}"><button>Resume</button></form>{% endif %}<form method="post" action="{{url_for('stop_timer',timer_id=t.id)}}"><label>Billable hours override<input type="number" step="0.0001" name="billable_hours"></label><label>Final service description<textarea name="services">{{t.services}}</textarea></label><label>Notes<input name="notes" value="{{t.notes or ''}}"></label><button class="danger">Stop and save</button></form></section>{% else %}<section class="card">No active entries.</section>{% endfor %}</div>
<section class="card wide"><h2>Recent entries</h2><table><tr><th>Date</th><th>Client</th><th>Matter</th><th>Service</th><th>Hours</th><th>Rate</th><th>Amount</th><th></th></tr>{% for t in recent %}<tr><form method="post" action="{{url_for('edit_service_v4',timer_id=t.id)}}"><td>{{t.start_time.date()}}</td><td>{{t.client.name}}</td><td>{{t.matter.name}}</td><td><textarea name="services">{{t.services}}</textarea><input type="hidden" name="notes" value="{{t.notes or ''}}"></td><td>{{t.billable_hours}}</td><td>{{t.applied_rate}}</td><td>{{t.amount}}</td><td><button>Save service</button></td></form></tr>{% endfor %}</table></section>
<script>client.onchange=async()=>{let r=await fetch('/api/matters/'+client.value),d=await r.json();matter.innerHTML=d.map(x=>`<option value="${x.id}">${x.reference||''} | ${x.name}</option>`).join('')};document.querySelectorAll('.clock').forEach(e=>{let s=+e.dataset.s;function d(){e.textContent=[Math.floor(s/3600),Math.floor(s%3600/60),Math.floor(s%60)].map(x=>String(x).padStart(2,'0')).join(':');if(e.dataset.run==='1')s++}d();setInterval(d,1000)})</script>{% endblock %}'''

def elapsed_safe(t):
    now=datetime.now(timezone.utc);start=t.start_time.replace(tzinfo=timezone.utc) if t.start_time and t.start_time.tzinfo is None else t.start_time
    sec=max(0,(now-start).total_seconds()-float(t.paused_seconds or 0)) if start else 0
    if t.status=='Paused' and t.paused_at:
        p=t.paused_at.replace(tzinfo=timezone.utc) if t.paused_at.tzinfo is None else t.paused_at;sec=max(0,sec-(now-p).total_seconds())
    return sec

@login_required
def timer_v4():
    active=Timer.query.filter(Timer.user_id==session['uid'],Timer.status.in_(['Running','Paused'])).order_by(Timer.id.desc()).all()
    for t in active:t.seconds=elapsed_safe(t)
    recent=Timer.query.filter_by(user_id=session['uid'],status='Stopped').order_by(Timer.id.desc()).limit(30).all()
    return render_template_string(TIMER_PAGE,clients=Client.query.order_by(Client.name).all(),active=active,recent=recent)
app.view_functions['timer']=timer_v4

@app.post('/timer/start-v4')
@login_required
def start_timer_v4():
    u=db.session.get(User,session['uid']);c=db.session.get(Client,int(request.form['client_id']));m=db.session.get(Matter,int(request.form['matter_id']))
    rate=m.matter_hourly_rate if m.matter_hourly_rate is not None else c.client_hourly_rate if c.client_hourly_rate is not None else u.default_hourly_rate
    mode=request.form.get('mode','live');start=dt_local(request.form.get('custom_start')) or datetime.now(timezone.utc);stop=None;status='Running';worked=None
    if mode=='custom_complete':
        stop=dt_local(request.form.get('custom_end'));hours=fnum(request.form.get('custom_hours'),None)
        if stop and start:worked=max(0,(stop-start).total_seconds()/3600)
        elif hours is not None:worked=max(0,hours);stop=start+timedelta(hours=worked)
        else:flash('Enter a custom end time or duration.');return redirect(url_for('timer'))
        status='Stopped'
    t=Timer(user_id=u.id,client_id=c.id,matter_id=m.id,category=None,services=request.form['services'].strip(),notes=request.form.get('notes',''),start_time=start,stop_time=stop,status=status,paused_seconds=0,timekeeper_rate=u.default_hourly_rate,client_rate=c.client_hourly_rate,matter_rate=m.matter_hourly_rate,applied_rate=rate or 0,billable=m.billable and request.form.get('billable')=='1',worked_hours=worked,billable_hours=worked if worked and m.billable else 0,invoiced_status='Unbilled')
    if worked is not None:t.amount=round(float(t.billable_hours or 0)*float(rate or 0),2)
    db.session.add(t);db.session.commit();return redirect(url_for('timer'))

@app.post('/timer/<int:timer_id>/service-v4')
@login_required
def edit_service_v4(timer_id):
    q=Timer.query.filter_by(id=timer_id,user_id=session['uid']) if session.get('role')!='Administrator' else Timer.query.filter_by(id=timer_id)
    t=q.first_or_404();t.services=request.form['services'].strip();t.notes=request.form.get('notes',t.notes);db.session.commit();flash('Service description saved.');return redirect(request.referrer or url_for('timer'))

# Patch stop to retain last-minute service edits.
original_stop=app.view_functions['stop_timer']
def stop_v4(timer_id):
    t=Timer.query.filter_by(id=timer_id,user_id=session['uid']).first_or_404()
    if request.form.get('services','').strip():t.services=request.form['services'].strip();db.session.commit()
    return original_stop(timer_id)
app.view_functions['stop_timer']=login_required(stop_v4)

# ---------- master-data UI and Excel replacement ----------
MASTER=r'''{% extends "base.html" %}{% block content %}<h1>Clients, contacts and matters</h1><section class="card"><h2>Replace or update master data from Excel</h2><p>Upload a workbook containing sheets named Clients, Contacts and Matters. Existing records with matching external IDs are updated; new records are added. Operational time and invoice records are retained.</p><form method="post" action="{{url_for('upload_master_v4')}}" enctype="multipart/form-data"><input type="file" name="file" accept=".xlsx" required><button>Import workbook</button></form></section>
<section class="card"><h2>Add or correct a client</h2><form method="post" action="{{url_for('client_v4')}}"><div class="grid"><label>Existing client to edit<select name="id"><option value="">New client</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Name<input name="name" required></label><label>Billing name<input name="billing_name"></label><label>Address<textarea name="address"></textarea></label><label>Country<input name="country"></label><label>VAT number<input name="vat_number"></label><label>Language<input name="language" value="English"></label><label>Currency<input name="currency" value="EUR"></label><label>Hourly rate<input type="number" step="0.01" name="rate"></label></div><button>Save client</button></form></section>
<section class="card"><h2>Add or correct a contact</h2><form method="post" action="{{url_for('contact_v4')}}"><div class="grid"><label>Existing contact<select name="id"><option value="">New contact</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Name<input name="name" required></label><label>Email<input type="email" name="email"></label><label>Phone<input name="phone"></label><label>Client mapping<select name="client_id"><option value="">No client</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label></div><button>Save contact</button></form></section>
<section class="card"><h2>Add or correct a matter</h2><form method="post" action="{{url_for('matter_v4')}}"><div class="grid"><label>Existing matter<select name="id"><option value="">New matter</option>{% for m in matters %}<option value="{{m.id}}">{{m.name}}</option>{% endfor %}</select></label><label>Reference<input name="reference"></label><label>Name<input name="name" required></label><label>Client mapping<select name="client_id" required>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Primary contact<select name="contact_id"><option value="">None</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Hourly rate<input type="number" step="0.01" name="rate"></label></div><button>Save matter</button></form></section>{% endblock %}'''

@login_required
def master_v4():return render_template_string(MASTER,clients=Client.query.order_by(Client.name).all(),contacts=Contact.query.order_by(Contact.display_name).all(),matters=Matter.query.order_by(Matter.name).all())

@app.post('/master/client-v4')
@login_required
def client_v4():
    c=db.session.get(Client,int(request.form['id'])) if request.form.get('id') else Client(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    c.name=request.form['name'];c.billing_name=request.form.get('billing_name') or c.name;c.address=request.form.get('address');c.country=request.form.get('country');c.vat_number=request.form.get('vat_number');c.language=request.form.get('language') or 'English';c.currency=request.form.get('currency') or 'EUR';c.client_hourly_rate=fnum(request.form.get('rate'),None);db.session.add(c);db.session.commit();return redirect(url_for('master_v4'))
@app.post('/master/contact-v4')
@login_required
def contact_v4():
    x=db.session.get(Contact,int(request.form['id'])) if request.form.get('id') else Contact(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    x.display_name=request.form['name'];x.email=request.form.get('email');x.phone=request.form.get('phone');x.client_id=int(request.form['client_id']) if request.form.get('client_id') else None;db.session.add(x);db.session.commit();return redirect(url_for('master_v4'))
@app.post('/master/matter-v4')
@login_required
def matter_v4():
    m=db.session.get(Matter,int(request.form['id'])) if request.form.get('id') else Matter(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    m.reference=request.form.get('reference');m.name=request.form['name'];m.client_id=int(request.form['client_id']);m.primary_contact_id=int(request.form['contact_id']) if request.form.get('contact_id') else None;m.matter_hourly_rate=fnum(request.form.get('rate'),None);m.billable=True;db.session.add(m);db.session.commit();return redirect(url_for('master_v4'))

@app.post('/master/upload-v4')
@login_required
@admin_required
def upload_master_v4():
    try:
        wb=load_workbook(request.files['file'],data_only=True,read_only=True)
        def rows(sheet):
            ws=wb[sheet];head=[str(c.value or '').strip() for c in ws[1]]
            return [dict(zip(head,r)) for r in ws.iter_rows(min_row=2,values_only=True)]
        # Flexible aliases for common exports.
        if 'Clients' in wb.sheetnames:
            for r in rows('Clients'):
                eid=str(r.get('Client ID') or r.get('ID') or '').strip();name=str(r.get('Client Name') or r.get('Name') or '').strip()
                if not name:continue
                c=Client.query.filter_by(external_id=eid).first() if eid else None;c=c or Client(external_id=eid or 'XLS-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
                c.name=name;c.billing_name=r.get('Billing Name') or name;c.address=r.get('Address');c.country=r.get('Country');c.vat_number=r.get('VAT Number');c.language=r.get('Language') or 'English';c.currency=r.get('Currency') or 'EUR';c.client_hourly_rate=fnum(r.get('Client Hourly Rate'),None);db.session.add(c)
            db.session.flush()
        clients={re.sub(r'\W','',c.name.lower()):c.id for c in Client.query.all()}
        if 'Contacts' in wb.sheetnames:
            for r in rows('Contacts'):
                eid=str(r.get('Contact ID') or r.get('ID') or '').strip();name=str(r.get('Display Name') or r.get('Name') or '').strip()
                if not name:continue
                x=Contact.query.filter_by(external_id=eid).first() if eid else None;x=x or Contact(external_id=eid or 'XLS-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
                x.display_name=name;x.email=r.get('Email');x.phone=r.get('Phone');org=str(r.get('Linked Organisation') or r.get('Client Name') or '');x.client_id=clients.get(re.sub(r'\W','',org.lower()));db.session.add(x)
        if 'Matters' in wb.sheetnames:
            for r in rows('Matters'):
                eid=str(r.get('Matter ID') or r.get('ID') or '').strip();name=str(r.get('Matter Name') or r.get('Name') or '').strip();cn=str(r.get('Client Name') or '')
                cid=clients.get(re.sub(r'\W','',cn.lower()))
                if not name or not cid:continue
                m=Matter.query.filter_by(external_id=eid).first() if eid else None;m=m or Matter(external_id=eid or 'XLS-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
                m.name=name;m.reference=r.get('Matter Reference');m.client_id=cid;m.matter_hourly_rate=fnum(r.get('Matter Hourly Rate'),None);m.billable=str(r.get('Billable?') or 'Yes')!='No';db.session.add(m)
        db.session.commit();flash('Master data imported successfully.')
    except Exception as exc:db.session.rollback();flash('Import failed: '+str(exc))
    return redirect(url_for('master_v4'))

# ---------- invoice payment terms, numbering, editing, repeated download ----------
PAYMENT={
'English':{'8 days':'eight days','30 days':'30 days','30 days end of month':'30 days end of month'},
'French':{'8 days':'huit jours','30 days':'30 jours','30 days end of month':'30 jours fin de mois'},
'German':{'8 days':'acht Tagen','30 days':'30 Tagen','30 days end of month':'30 Tage zum Monatsende'}}

def translated_terms(language,code,other=''):
    return other.strip() if code=='other' else PAYMENT.get(language,PAYMENT['English']).get(code,code)

def next_number(draft):
    if draft.invoice_number:return draft.invoice_number
    prefix=setting('invoice_prefix','MARS-INV-'+str(date.today().year)+'-')
    start=int(setting('invoice_next','1'))
    existing=[]
    for value, in db.session.query(FeeNoteDraft.invoice_number).filter(FeeNoteDraft.invoice_number.isnot(None)).all():
        if value and value.startswith(prefix):
            tail=value[len(prefix):]
            if tail.isdigit():existing.append(int(tail))
    number=max([start-1]+existing)+1;set_setting('invoice_next',number+1);return prefix+f'{number:04d}'

# Override header update to handle term choices and editable invoice number.
original_header=app.view_functions['update_fee_note']
def header_v4(draft_id):
    draft=db.session.get(FeeNoteDraft,draft_id)
    if request.form.get('payment_choice'):request.form=request.form.copy();request.form['payment_terms']=translated_terms(request.form.get('language',draft.language),request.form['payment_choice'],request.form.get('payment_other',''))
    if request.form.get('invoice_number','').strip():draft.invoice_number=request.form['invoice_number'].strip();db.session.commit()
    return original_header(draft_id)
app.view_functions['update_fee_note']=login_required(admin_required(header_v4))

# Patch editor HTML from core with payment dropdown, invoice number, and re-download controls.
old_edit=app.view_functions['edit_fee_note']
def edit_invoice_v4(draft_id):
    draft=db.session.get(FeeNoteDraft,draft_id);lines=FeeNoteLine.query.filter_by(draft_id=draft_id).order_by(FeeNoteLine.sort_order,FeeNoteLine.id).all()
    # Reuse existing editor, injecting controls through a separate professional control card.
    controls=r'''{% extends "base.html" %}{% block content %}<section class="card"><h1>Fee note {{draft.invoice_number or 'draft'}}</h1><form method="post" action="{{url_for('invoice_controls_v4',draft_id=draft.id)}}"><div class="grid"><label>Invoice number<input name="invoice_number" value="{{draft.invoice_number or ''}}" placeholder="Assigned on issue"></label><label>Payment deadline<select name="payment_choice"><option value="8 days">{{labels[0]}}</option><option value="30 days">{{labels[1]}}</option><option value="30 days end of month">{{labels[2]}}</option><option value="other">{{labels[3]}}</option></select></label><label>Other payment deadline<input name="payment_other"></label></div><button>Save invoice controls</button></form><p><a href="{{url_for('edit_fee_note_original_v4',draft_id=draft.id)}}">Open full editable fee-note details</a></p>{% if draft.invoice_number %}<form method="post" action="{{url_for('issue_fee_note',draft_id=draft.id)}}"><button>Download updated Word invoice</button></form>{% endif %}</section>{% endblock %}'''
    labels={'English':['8 days','30 days','30 days end of month','Other'],'French':['8 jours','30 jours','30 jours fin de mois','Autre'],'German':['8 Tage','30 Tage','30 Tage zum Monatsende','Andere']}.get(draft.language,['8 days','30 days','30 days end of month','Other'])
    return render_template_string(controls,draft=draft,lines=lines,labels=labels)

# Preserve full prior editor under a new route.
app.add_url_rule('/invoices/<int:draft_id>/full-edit-v4','edit_fee_note_original_v4',old_edit)
app.view_functions['edit_fee_note']=login_required(admin_required(edit_invoice_v4))

@app.post('/invoices/<int:draft_id>/controls-v4')
@login_required
@admin_required
def invoice_controls_v4(draft_id):
    d=db.session.get(FeeNoteDraft,draft_id);num=request.form.get('invoice_number','').strip()
    if num:d.invoice_number=num
    d.payment_terms=translated_terms(d.language,request.form.get('payment_choice'),request.form.get('payment_other',''));db.session.commit();return redirect(url_for('edit_fee_note',draft_id=d.id))

# Invoice prefix settings in admin.
@app.post('/admin/invoice-sequence-v4')
@login_required
@admin_required
def invoice_sequence_v4():
    set_setting('invoice_prefix',request.form['prefix']);set_setting('invoice_next',int(request.form['next_number']));flash('Invoice numbering settings saved.');return redirect(url_for('admin'))

# Issue or re-download an invoice without changing an existing number.
def issue_v4(draft_id):
    d=db.session.get(FeeNoteDraft,draft_id)
    core.recalculate(d)
    if not d.invoice_number:
        d.invoice_number=next_number(d)
        db.session.commit()
    number=d.invoice_number
    code={'English':'EN','French':'FR','German':'DE'}[d.language]
    template=ASSETS/f'MARSIP reference invoice {code}.docx'
    if not template.exists():flash('Template missing: '+template.name);return redirect(url_for('edit_fee_note',draft_id=d.id))
    included=FeeNoteLine.query.filter_by(draft_id=d.id,included=True).order_by(FeeNoteLine.sort_order,FeeNoteLine.id).all()
    detail=[x for x in included if x.include_in_detail or x.line_type=='Time-based fees']
    doc=Document(template)
    previous.rebuild_summary(doc,d,included)
    if d.show_time_schedule:previous.rebuild_detail(doc,d,detail)
    # The sample shows the VAT percentage in the left cell and calculated VAT amount in the right cell.
    table=previous.find_summary_table(doc)
    if table:
        for row in table.rows:
            text=previous.text_of_row(row)
            if 'vat' in text or 'mwst' in text or 'tva' in text:
                if d.vat_treatment=='Reverse charge':
                    previous.set_cell(row.cells[0],d.vat_wording or 'Reverse charge (VAT payable by the recipient, Article 196 of VAT Directive 2006/112/EC)')
                else:
                    previous.set_cell(row.cells[0],f'{d.vat_label or "VAT"} {float(d.vat_rate or 0):g}%')
                previous.set_cell(row.cells[-1],previous.money(d.vat_amount,d.currency))
                break
    replacements={
      '[final sequential document number, assigned only when the document is issued]':number,
      '[ClientId]':d.billing_name,'[billing address]':d.billing_address,'[country]':d.country,
      '[contact person at the client or contact person linked to the matter]':d.contact_name,
      '[place of issue, with Berlin as the default]':d.issue_place,'[place of issue, with Berlin as the default ]':d.issue_place,
      '[document date, with the current date as the default]':d.document_date,'[document date, with the current date as the default ]':d.document_date,
      '[VAT number Client]':d.vat_number if d.show_vat_number else '',
      '[client Name]':d.client_name,'[Matter]':d.matter_text,
      '[date of the Engagement Letter]':d.engagement_date if d.show_engagement else '',
      '[date of the Engagement letter]':d.engagement_date if d.show_engagement else '',
      '[payment due date: eight days/30 days/30 days end of month]':d.payment_terms,
      '[payment due date: acht Tage/30 Tage/30 Tage zum Monatsende]':d.payment_terms,
      '[payment due date: huitainte/30 jours/30 jours fin de mois]':d.payment_terms,
      'XXXXXX':d.payment_terms,
      'calculate the amount':previous.money(d.vat_amount,d.currency),
      'Final Total Including VAT':previous.money(d.gross_total,d.currency),'Total Final TTC':previous.money(d.gross_total,d.currency),
    }
    previous.replace_everywhere(doc,replacements)
    out=BytesIO();doc.save(out);out.seek(0)
    d.status='Issued';d.issued_at=datetime.now(timezone.utc)
    for line in included:
        if line.source_timer_id:
            timer=db.session.get(Timer,line.source_timer_id)
            if timer:timer.invoiced_status='Invoiced';timer.invoice_number=number
    db.session.commit()
    return send_file(out,as_attachment=True,download_name=number+'.docx',mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
app.view_functions['issue_fee_note']=login_required(admin_required(issue_v4))

# Add convenient master data and password links by replacing admin with expanded dashboard wrapper.
old_admin=app.view_functions['admin']
def admin_v4():
    if request.method if False else False:pass
    return render_template_string(r'''{% extends "base.html" %}{% block content %}<h1>Administration</h1><section class="card"><p><a href="{{url_for('master_v4')}}">Manage clients, contacts and matters</a> | <a href="{{url_for('password_v4')}}">Change password</a></p><form method="post" action="{{url_for('invoice_sequence_v4')}}"><div class="grid"><label>Invoice prefix<input name="prefix" value="{{prefix}}"></label><label>Next minimum number<input type="number" min="1" name="next_number" value="{{next_number}}"></label></div><button>Save numbering</button></form><p><a href="{{url_for('admin_original_v4')}}">Open users, rates, matter assignments and recorded time</a></p></section><section class="card"><h2>Issued and draft fee notes</h2><table><tr><th>Number</th><th>Status</th><th>Client</th><th>Matter</th><th>Date</th><th></th></tr>{% for d in drafts %}<tr><td>{{d.invoice_number or 'Draft'}}</td><td>{{d.status}}</td><td>{{d.client_name}}</td><td>{{d.matter_text}}</td><td>{{d.document_date}}</td><td><a href="{{url_for('edit_fee_note',draft_id=d.id)}}">Edit / download</a></td></tr>{% endfor %}</table></section>{% endblock %}''',prefix=setting('invoice_prefix','MARS-INV-'+str(date.today().year)+'-'),next_number=setting('invoice_next','1'),drafts=FeeNoteDraft.query.order_by(FeeNoteDraft.id.desc()).all())
app.add_url_rule('/admin/full-v4','admin_original_v4',old_admin)
app.view_functions['admin']=login_required(admin_required(admin_v4))
