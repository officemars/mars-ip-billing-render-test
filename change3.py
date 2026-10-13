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

# ---------- Change 3.5: admin invoice register, combined editor, live totals and preview ----------
# These additions deliberately do not modify the proven login or timer endpoints above.

INVOICE_REGISTER_V5 = r'''{% extends "base.html" %}{% block content %}
<h1>Invoice register</h1><p>All draft and issued fee notes. Administrator access only.</p>
<section class="card wide"><table><tr><th>Number</th><th>Status</th><th>Client</th><th>Matter</th><th>Date</th><th>Net</th><th>VAT</th><th>Total gross</th><th>Actions</th></tr>
{% for d in drafts %}<tr><td>{{d.invoice_number or 'Draft'}}</td><td>{{d.status}}</td><td>{{d.client_name}}</td><td>{{d.matter_text}}</td><td>{{d.document_date}}</td><td>{{d.net_total}} {{d.currency}}</td><td>{{d.vat_amount}} {{d.currency}}</td><td>{{d.gross_total}} {{d.currency}}</td><td><a href="{{url_for('edit_fee_note',draft_id=d.id)}}">Edit</a> | <a href="{{url_for('invoice_preview_v5',draft_id=d.id)}}">Preview</a></td></tr>{% else %}<tr><td colspan="9">No fee notes are stored.</td></tr>{% endfor %}</table></section>
{% endblock %}'''

@app.get('/invoices/register-v5')
@login_required
@admin_required
def invoice_register_v5():
    drafts=FeeNoteDraft.query.order_by(FeeNoteDraft.id.desc()).all()
    return render_template_string(INVOICE_REGISTER_V5,drafts=drafts)


def recalculate_v5(draft):
    net=0.0; vat=0.0
    for line in FeeNoteLine.query.filter_by(draft_id=draft.id,included=True).all():
        amount=float(line.amount or 0) if line.amount_override else float(line.quantity or 0)*float(line.rate or 0)
        if line.line_type in {'Provision','Credit / discount'}: amount=-abs(amount)
        line.amount=round(amount,2); net+=line.amount
        treatment=(line.vat_treatment or draft.vat_treatment or '').lower()
        rate=0.0 if ('reverse' in treatment or 'exempt' in treatment) else float(line.vat_rate if line.vat_rate is not None else draft.vat_rate or 0)
        vat+=line.amount*rate/100
    if draft.show_telecom:
        telecom=float(draft.telecom_amount or 0); net+=telecom
        if draft.vat_treatment not in ('Reverse charge','Other exemption'): vat+=telecom*float(draft.vat_rate or 0)/100
    draft.net_total=round(net,2); draft.vat_amount=round(vat,2); draft.gross_total=round(net+vat,2)
    db.session.commit()

# Ensure Change 2 Word output and all saved line operations use the corrected totals.
core.recalculate=recalculate_v5

COMBINED_EDITOR_V5 = r'''{% extends "base.html" %}{% block content %}
<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap"><div><h1>Fee note preparation</h1><p>{{draft.invoice_number or 'Draft'}} · {{draft.client_name}} · {{draft.matter_text}}</p></div><div><a href="{{url_for('invoice_register_v5')}}">Invoice register</a> | <a href="{{url_for('invoice_preview_v5',draft_id=draft.id)}}">Preview invoice</a></div></div>
<section class="card"><h2>Invoice controls</h2><form method="post" action="{{url_for('invoice_controls_v5',draft_id=draft.id)}}"><div class="grid"><label>Invoice number<input name="invoice_number" value="{{draft.invoice_number or ''}}" placeholder="Assigned on issue"></label><label>Payment deadline<select name="payment_choice"><option value="8 days">{{labels[0]}}</option><option value="30 days">{{labels[1]}}</option><option value="30 days end of month">{{labels[2]}}</option><option value="other">{{labels[3]}}</option></select></label><label>Other deadline<input name="payment_other"></label></div><button>Save invoice controls</button></form></section>
<section class="card wide"><h2>Full editable fee note</h2><iframe id="fullEditor" src="{{url_for('edit_fee_note_original_v4',draft_id=draft.id)}}" style="width:100%;min-height:1250px;border:0"></iframe></section>
<section class="card"><a href="{{url_for('invoice_preview_v5',draft_id=draft.id)}}">Preview before download</a></section>{% endblock %}'''

# Keep the proven full editor already saved above as edit_fee_note_original_v4.
def edit_fee_note_v5(draft_id):
    draft=db.session.get(FeeNoteDraft,draft_id)
    recalculate_v5(draft)
    labels={'English':['8 days','30 days','30 days end of month','Other'],'French':['8 jours','30 jours','30 jours fin de mois','Autre'],'German':['8 Tage','30 Tage','30 Tage zum Monatsende','Andere']}.get(draft.language,['8 days','30 days','30 days end of month','Other'])
    return render_template_string(COMBINED_EDITOR_V5,draft=draft,labels=labels)
app.view_functions['edit_fee_note']=login_required(admin_required(edit_fee_note_v5))

@app.post('/invoices/<int:draft_id>/controls-v5')
@login_required
@admin_required
def invoice_controls_v5(draft_id):
    draft=db.session.get(FeeNoteDraft,draft_id)
    number=request.form.get('invoice_number','').strip()
    if number: draft.invoice_number=number
    draft.payment_terms=translated_terms(draft.language,request.form.get('payment_choice'),request.form.get('payment_other',''))
    db.session.commit(); flash('Invoice controls saved.')
    return redirect(url_for('edit_fee_note',draft_id=draft.id))

PREVIEW_V5 = r'''{% extends "base.html" %}{% block content %}<div style="display:flex;justify-content:space-between;align-items:center"><h1>Invoice preview</h1><a href="{{url_for('edit_fee_note',draft_id=draft.id)}}">Return to edit</a></div><section class="card" style="max-width:850px;margin:auto;padding:40px"><h2>Fee Note {{draft.invoice_number or '(assigned when issued)'}}</h2><p style="white-space:pre-line">{{draft.billing_name}}\n{{draft.billing_address}}\n{{draft.country}}\n{{draft.contact_name}}</p><p>{{draft.issue_place}}, {{draft.document_date}}</p>{% if draft.show_vat_number %}<p><b>Your VAT number: {{draft.vat_number}}</b></p>{% endif %}<p><b>Matter: {{draft.client_name}} | {{draft.matter_text}}</b></p>{% if draft.show_engagement %}<p><i>According to our Engagement Letter dated {{draft.engagement_date}}</i></p>{% endif %}<table><tr><th>Fees</th><th>Net amount</th></tr>{% for line in lines %}<tr><td>{{line.description}}</td><td>{{'%.2f'|format(line.amount|float)}} {{draft.currency}}</td></tr>{% endfor %}{% if draft.show_telecom %}<tr><td>{{draft.telecom_description}}</td><td>{{'%.2f'|format(draft.telecom_amount|float)}} {{draft.currency}}</td></tr>{% endif %}<tr><td><b>TOTAL NET</b></td><td><b>{{draft.net_total}} {{draft.currency}}</b></td></tr><tr><td>{{draft.vat_label}} {{draft.vat_rate}}%</td><td>{{draft.vat_amount}} {{draft.currency}}</td></tr><tr><td><b>TOTAL GROSS</b></td><td><b>{{draft.gross_total}} {{draft.currency}}</b></td></tr></table><p><b>Payment due within {{draft.payment_terms}} from receipt of this Fee Note.</b></p>{% if draft.show_time_schedule %}<h3>Detailed services schedule</h3><table><tr><th>Date</th><th>Timekeeper</th><th>Services</th><th>Time</th><th>Net amount</th></tr>{% for line in detail %}<tr><td>{{line.service_date}}</td><td>{{line.timekeeper}}</td><td>{{line.description}}</td><td>{{line.quantity}}</td><td>{{line.amount}} {{draft.currency}}</td></tr>{% endfor %}</table>{% endif %}<form method="post" action="{{url_for('issue_fee_note',draft_id=draft.id)}}"><button>{{'Download updated Word invoice' if draft.invoice_number else 'Issue and download Word invoice'}}</button></form></section>{% endblock %}'''

@app.get('/invoices/<int:draft_id>/preview-v5')
@login_required
@admin_required
def invoice_preview_v5(draft_id):
    draft=db.session.get(FeeNoteDraft,draft_id); recalculate_v5(draft)
    lines=FeeNoteLine.query.filter_by(draft_id=draft.id,included=True).order_by(FeeNoteLine.sort_order,FeeNoteLine.id).all()
    detail=[x for x in lines if x.include_in_detail or x.line_type=='Time-based fees']
    return render_template_string(PREVIEW_V5,draft=draft,lines=lines,detail=detail)

# Present the already implemented import/add/edit mapping UI under the requested heading.
MASTER=MASTER.replace('<h1>Clients, contacts and matters</h1>','<h1>Data management</h1><p>Import, add, correct and map clients, contacts and matters.</p>')

# Replace only the admin landing view. Original user/rate administration remains available at admin_original_v4.
def admin_v5():
    return render_template_string(r'''{% extends "base.html" %}{% block content %}<h1>Administration</h1><div class="cards"><section class="card"><h2>Invoice register</h2><p>Review, edit, preview and download previous fee notes.</p><a href="{{url_for('invoice_register_v5')}}">Open invoice register</a></section><section class="card"><h2>Data management</h2><p>Upload Excel sheets and add or correct clients, contacts, matters and mappings.</p><a href="{{url_for('master_v4')}}">Open Data management</a></section><section class="card"><h2>Users and rates</h2><p>Manage users, rates, assignments and recorded time.</p><a href="{{url_for('admin_original_v4')}}">Open user administration</a></section></div><section class="card"><h2>Invoice numbering</h2><form method="post" action="{{url_for('invoice_sequence_v4')}}"><div class="grid"><label>Invoice prefix<input name="prefix" value="{{prefix}}"></label><label>Next number<input type="number" min="1" name="next_number" value="{{next_number}}"></label></div><button>Save numbering</button></form></section>{% endblock %}''',prefix=setting('invoice_prefix','MARS-INV-'+str(date.today().year)+'-'),next_number=setting('invoice_next','1'))
app.view_functions['admin']=login_required(admin_required(admin_v5))

# ---------- Change 3.6: repair admin Data Management and add safe delete/mapping controls ----------
DATA_MANAGEMENT_V6 = r'''{% extends "base.html" %}{% block content %}
<div style="display:flex;justify-content:space-between;align-items:center"><div><h1>Data management</h1><p>Import, add, edit, remove and map clients, contacts and matters.</p></div><a href="{{url_for('admin')}}">Back to Administration</a></div>
<section class="card"><h2>Import updated Excel workbook</h2><p>The workbook may contain Clients, Contacts and Matters sheets. Matching external IDs are updated and new records are added.</p><form method="post" action="{{url_for('upload_master_v4')}}" enctype="multipart/form-data"><input type="file" name="file" accept=".xlsx" required><button>Import workbook</button></form></section>
<section class="card wide"><h2>Clients</h2><form method="post" action="{{url_for('save_client_v6')}}"><div class="grid"><label>Existing client<select name="id"><option value="">New client</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Name<input name="name" required></label><label>Billing name<input name="billing_name"></label><label>Address<textarea name="address"></textarea></label><label>Country<input name="country"></label><label>VAT number<input name="vat_number"></label><label>Language<select name="language"><option>English</option><option>French</option><option>German</option></select></label><label>Currency<input name="currency" value="EUR"></label><label>Hourly rate<input type="number" step="0.01" name="rate"></label></div><button>Save client</button></form><h3>Existing clients</h3><table><tr><th>Name</th><th>Billing name</th><th>Country</th><th>VAT number</th><th></th></tr>{% for c in clients %}<tr><td>{{c.name}}</td><td>{{c.billing_name}}</td><td>{{c.country}}</td><td>{{c.vat_number}}</td><td><form method="post" action="{{url_for('delete_client_v6',client_id=c.id)}}" onsubmit="return confirm('Remove this client only if no matter or time entry uses it.');"><button class="danger">Remove</button></form></td></tr>{% endfor %}</table></section>
<section class="card wide"><h2>Contacts</h2><form method="post" action="{{url_for('save_contact_v6')}}"><div class="grid"><label>Existing contact<select name="id"><option value="">New contact</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Name<input name="name" required></label><label>Email<input type="email" name="email"></label><label>Phone<input name="phone"></label><label>Mapped client<select name="client_id"><option value="">No client</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label></div><button>Save contact and mapping</button></form><h3>Existing contacts</h3><table><tr><th>Name</th><th>Email</th><th>Mapped client</th><th></th></tr>{% for x in contacts %}<tr><td>{{x.display_name}}</td><td>{{x.email}}</td><td>{{client_names.get(x.client_id,'')}}</td><td><form method="post" action="{{url_for('delete_contact_v6',contact_id=x.id)}}" onsubmit="return confirm('Remove this contact?');"><button class="danger">Remove</button></form></td></tr>{% endfor %}</table></section>
<section class="card wide"><h2>Matters</h2><form method="post" action="{{url_for('save_matter_v6')}}"><div class="grid"><label>Existing matter<select name="id"><option value="">New matter</option>{% for m in matters %}<option value="{{m.id}}">{{m.reference or ''}} | {{m.name}}</option>{% endfor %}</select></label><label>Reference<input name="reference"></label><label>Name<input name="name" required></label><label>Mapped client<select name="client_id" required>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Primary contact<select name="contact_id"><option value="">None</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Hourly rate<input type="number" step="0.01" name="rate"></label></div><button>Save matter and mapping</button></form><h3>Existing matters</h3><table><tr><th>Reference</th><th>Name</th><th>Mapped client</th><th>Primary contact</th><th></th></tr>{% for m in matters %}<tr><td>{{m.reference}}</td><td>{{m.name}}</td><td>{{client_names.get(m.client_id,'')}}</td><td>{{contact_names.get(m.primary_contact_id,'')}}</td><td><form method="post" action="{{url_for('delete_matter_v6',matter_id=m.id)}}" onsubmit="return confirm('Remove this matter only if no time entry uses it.');"><button class="danger">Remove</button></form></td></tr>{% endfor %}</table></section>
{% endblock %}'''

def data_management_v6():
    clients=Client.query.order_by(Client.name).all(); contacts=Contact.query.order_by(Contact.display_name).all(); matters=Matter.query.order_by(Matter.name).all()
    return render_template_string(DATA_MANAGEMENT_V6,clients=clients,contacts=contacts,matters=matters,client_names={c.id:c.name for c in clients},contact_names={x.id:x.display_name for x in contacts})

# The working file defined master_v4 but did not register a GET URL. This explicit rule fixes /admin.
app.add_url_rule('/admin/data-management','master_v4',login_required(admin_required(data_management_v6)),methods=['GET'])

@app.post('/admin/data-management/client/save')
@login_required
@admin_required
def save_client_v6():
    c=db.session.get(Client,int(request.form['id'])) if request.form.get('id') else Client(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    c.name=request.form['name'].strip(); c.billing_name=request.form.get('billing_name','').strip() or c.name; c.address=request.form.get('address','').strip(); c.country=request.form.get('country','').strip(); c.vat_number=request.form.get('vat_number','').strip(); c.language=request.form.get('language') or 'English'; c.currency=request.form.get('currency') or 'EUR'; c.client_hourly_rate=fnum(request.form.get('rate'),None)
    db.session.add(c); db.session.commit(); flash('Client saved.'); return redirect(url_for('master_v4'))

@app.post('/admin/data-management/contact/save')
@login_required
@admin_required
def save_contact_v6():
    x=db.session.get(Contact,int(request.form['id'])) if request.form.get('id') else Contact(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    x.display_name=request.form['name'].strip(); x.email=request.form.get('email','').strip(); x.phone=request.form.get('phone','').strip(); x.client_id=int(request.form['client_id']) if request.form.get('client_id') else None
    db.session.add(x); db.session.commit(); flash('Contact and client mapping saved.'); return redirect(url_for('master_v4'))

@app.post('/admin/data-management/matter/save')
@login_required
@admin_required
def save_matter_v6():
    m=db.session.get(Matter,int(request.form['id'])) if request.form.get('id') else Matter(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    m.reference=request.form.get('reference','').strip(); m.name=request.form['name'].strip(); m.client_id=int(request.form['client_id']); m.primary_contact_id=int(request.form['contact_id']) if request.form.get('contact_id') else None; m.matter_hourly_rate=fnum(request.form.get('rate'),None); m.billable=True
    db.session.add(m); db.session.commit(); flash('Matter, client and contact mapping saved.'); return redirect(url_for('master_v4'))

@app.post('/admin/data-management/client/<int:client_id>/delete')
@login_required
@admin_required
def delete_client_v6(client_id):
    if Matter.query.filter_by(client_id=client_id).first() or Timer.query.filter_by(client_id=client_id).first() or FeeNoteDraft.query.filter_by(client_id=client_id).first(): flash('Client cannot be removed because matters, time entries or fee notes refer to it.')
    else: db.session.delete(db.session.get(Client,client_id)); db.session.commit(); flash('Client removed.')
    return redirect(url_for('master_v4'))

@app.post('/admin/data-management/contact/<int:contact_id>/delete')
@login_required
@admin_required
def delete_contact_v6(contact_id):
    if Matter.query.filter_by(primary_contact_id=contact_id).first() or FeeNoteDraft.query.filter_by(contact_id=contact_id).first(): flash('Contact cannot be removed because a matter or fee note refers to it.')
    else: db.session.delete(db.session.get(Contact,contact_id)); db.session.commit(); flash('Contact removed.')
    return redirect(url_for('master_v4'))

@app.post('/admin/data-management/matter/<int:matter_id>/delete')
@login_required
@admin_required
def delete_matter_v6(matter_id):
    if Timer.query.filter_by(matter_id=matter_id).first() or FeeNoteDraft.query.filter_by(matter_id=matter_id).first(): flash('Matter cannot be removed because time entries or fee notes refer to it.')
    else: db.session.delete(db.session.get(Matter,matter_id)); db.session.commit(); flash('Matter removed.')
    return redirect(url_for('master_v4'))

# ==================== CHANGE 3.7: SIMPLIFIED TIMER AND COMPLETE FACT SHEETS ====================
import json

class EntityFactSheet(db.Model):
    __tablename__='entity_fact_sheet_v7'
    id=db.Column(db.Integer,primary_key=True)
    entity_type=db.Column(db.String(30),nullable=False,index=True)
    entity_id=db.Column(db.Integer,nullable=False,index=True)
    data_json=db.Column(db.Text,nullable=False,default='{}')
    updated_at=db.Column(db.DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),nullable=False)
    __table_args__=(db.UniqueConstraint('entity_type','entity_id',name='uq_entity_fact_sheet_v7'),)
with app.app_context(): db.create_all()

def fact_data(kind,entity_id):
    row=EntityFactSheet.query.filter_by(entity_type=kind,entity_id=entity_id).first()
    try:return json.loads(row.data_json or '{}') if row else {}
    except:return {}

def save_fact(kind,entity_id,data):
    row=EntityFactSheet.query.filter_by(entity_type=kind,entity_id=entity_id).first() or EntityFactSheet(entity_type=kind,entity_id=entity_id)
    current=fact_data(kind,entity_id);current.update(data);row.data_json=json.dumps(current,ensure_ascii=False);row.updated_at=datetime.now(timezone.utc);db.session.add(row)

TIMER_PAGE_V7=r'''{% extends "base.html" %}{% block content %}<div style="display:flex;justify-content:space-between;align-items:center"><div><h1>Time recording</h1><p>Start now and edit the service description whenever needed.</p></div><a href="{{url_for('password_v4')}}">Change password</a></div><section class="card"><form method="post" action="{{url_for('start_timer_v4')}}"><div class="grid"><label>Client<select id="client" name="client_id" required><option value="">Select</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Matter<select id="matter" name="matter_id" required><option value="">Select a client first</option></select></label><label>Billable<select name="billable"><option value="1">Yes</option><option value="0">No</option></select></label></div><label>Service description<textarea name="services" required></textarea></label><label>Internal notes<input name="notes"></label><button>Start timer now</button></form></section><h2>Active entries</h2><div class="cards">{% for t in active %}<section class="card"><h3>{{t.client.name}}</h3><p>{{t.matter.name}}</p><form method="post" action="{{url_for('edit_service_v4',timer_id=t.id)}}"><label>Service description<textarea name="services" required>{{t.services}}</textarea></label><label>Notes<input name="notes" value="{{t.notes or ''}}"></label><button>Save description</button></form><div class="clock" data-s="{{t.seconds}}" data-run="{{1 if t.status=='Running' else 0}}"></div><p>{{t.status}}</p>{% if t.status=='Running' %}<form method="post" action="{{url_for('pause_timer',timer_id=t.id)}}"><button>Pause</button></form>{% else %}<form method="post" action="{{url_for('resume_timer',timer_id=t.id)}}"><button>Resume</button></form>{% endif %}<form method="post" action="{{url_for('stop_timer',timer_id=t.id)}}"><label>Billable hours override, optional<input type="number" min="0" step="0.0001" name="billable_hours"></label><input type="hidden" name="notes" value="{{t.notes or ''}}"><button class="danger">Stop and save</button></form></section>{% else %}<section class="card">No active entries.</section>{% endfor %}</div><section class="card wide"><h2>Recent entries</h2><table><tr><th>Date</th><th>Client</th><th>Matter</th><th>Service</th><th>Hours</th><th>Rate</th><th>Amount</th><th></th></tr>{% for t in recent %}<tr><form method="post" action="{{url_for('edit_service_v4',timer_id=t.id)}}"><td>{{t.start_time.date()}}</td><td>{{t.client.name}}</td><td>{{t.matter.name}}</td><td><textarea name="services">{{t.services}}</textarea><input type="hidden" name="notes" value="{{t.notes or ''}}"></td><td>{{t.billable_hours}}</td><td>{{t.applied_rate}}</td><td>{{t.amount}}</td><td><button>Save</button></td></form></tr>{% endfor %}</table></section><script>client.onchange=async()=>{let r=await fetch('/api/matters/'+client.value),d=await r.json();matter.innerHTML='<option value="">Select a matter</option>'+d.map(x=>`<option value="${x.id}">${x.reference||''}${x.reference?' | ':''}${x.name}</option>`).join('')};document.querySelectorAll('.clock').forEach(e=>{let s=+e.dataset.s;function d(){e.textContent=[Math.floor(s/3600),Math.floor(s%3600/60),Math.floor(s%60)].map(x=>String(x).padStart(2,'0')).join(':');if(e.dataset.run==='1')s++}d();setInterval(d,1000)})</script>{% endblock %}'''

def timer_v7():
    active=Timer.query.filter(Timer.user_id==session['uid'],Timer.status.in_(['Running','Paused'])).order_by(Timer.id.desc()).all()
    for t in active:t.seconds=elapsed_safe(t)
    recent=Timer.query.filter_by(user_id=session['uid'],status='Stopped').order_by(Timer.id.desc()).limit(30).all()
    return render_template_string(TIMER_PAGE_V7,clients=Client.query.order_by(Client.name).all(),active=active,recent=recent)

def start_timer_v7():
    u=db.session.get(User,session['uid']);c=db.session.get(Client,int(request.form['client_id']));m=db.session.get(Matter,int(request.form['matter_id']))
    rate=m.matter_hourly_rate if m.matter_hourly_rate is not None else c.client_hourly_rate if c.client_hourly_rate is not None else u.default_hourly_rate
    t=Timer(user_id=u.id,client_id=c.id,matter_id=m.id,category=None,services=request.form['services'].strip(),notes=request.form.get('notes',''),start_time=datetime.now(timezone.utc),status='Running',paused_seconds=0,timekeeper_rate=u.default_hourly_rate,client_rate=c.client_hourly_rate,matter_rate=m.matter_hourly_rate,applied_rate=rate or 0,billable=m.billable and request.form.get('billable')=='1',invoiced_status='Unbilled')
    db.session.add(t);db.session.commit();return redirect(url_for('timer'))
app.view_functions['timer']=login_required(timer_v7)
app.view_functions['start_timer_v4']=login_required(start_timer_v7)

FACTS_V7=r'''{% extends "base.html" %}{% block content %}<h1>Data management</h1><p>Complete Client, Contact, Matter and Timekeeper Records.</p><section class="card"><h2>Imports</h2><form method="post" action="{{url_for('upload_master_v4')}}" enctype="multipart/form-data"><label>Clients, Contacts and Matters workbook<input type="file" name="file" accept=".xlsx" required></label><button>Import master data</button></form><form method="post" action="{{url_for('upload_timekeeper_v7')}}" enctype="multipart/form-data"><label>Timekeeper Record workbook<input type="file" name="file" accept=".xlsx" required></label><button>Import timekeeper</button></form></section><section class="card"><h2>Client Record</h2><form method="post" action="{{url_for('client_fact_v7')}}"><div class="grid"><label>Existing<select name="id"><option value="">New client</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Client name<input name="name" required></label><label>Billing name<input name="billing_name"></label><label>Address<textarea name="address"></textarea></label><label>Country<input name="country"></label><label>VAT number<input name="vat_number"></label><label>Language<input name="language" value="English"></label><label>Currency<input name="currency" value="EUR"></label><label>Payment terms<input name="payment_terms"></label><label>VAT treatment<input name="vat_treatment"></label><label>Billing email<input type="email" name="billing_email"></label><label>Optional client rate<input type="number" step="0.01" name="rate"></label></div><button>Save Client Record</button></form></section><section class="card"><h2>Contact Record</h2><form method="post" action="{{url_for('contact_fact_v7')}}"><div class="grid"><label>Existing<select name="id"><option value="">New contact</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Name<input name="name" required></label><label>Title<input name="title"></label><label>Exact salutation<select name="salutation"><option>Tu</option><option>Vous</option><option>Sie</option><option>Du</option></select></label><label>Preferred language<input name="preferred_language"></label><label>Email<input type="email" name="email"></label><label>Phone<input name="phone"></label><label>Address<textarea name="address"></textarea></label><label>Linked client<select name="client_id"><option value="">None</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label></div><button>Save Contact Record</button></form></section><section class="card"><h2>Matter Record</h2><form method="post" action="{{url_for('matter_fact_v7')}}"><div class="grid"><label>Existing<select name="id"><option value="">New matter</option>{% for m in matters %}<option value="{{m.id}}">{{m.reference or ''}} | {{m.name}}</option>{% endfor %}</select></label><label>Matter name<input name="name" required></label><label>Nature<input name="nature"></label><label>Reference number(s)<input name="reference"></label><label>Linked client<select name="client_id" required>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Linked contact<select name="contact_id"><option value="">None</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Billing method<input name="billing_method"></label><label>Hourly rate<input type="number" step="0.01" name="hourly_rate"></label><label>Daily rate<input type="number" step="0.01" name="daily_rate"></label><label>Currency<input name="currency" value="EUR"></label><label>Payment terms<input name="payment_terms"></label><label>Billing notes<textarea name="billing_notes"></textarea></label></div><button>Save Matter Record</button></form></section><section class="card"><h2>Timekeeper Record</h2><form method="post" action="{{url_for('timekeeper_fact_v7')}}"><div class="grid"><label>Existing user<select name="id"><option value="">New timekeeper</option>{% for u in users %}<option value="{{u.id}}">{{u.display_name}} | {{u.email}}</option>{% endfor %}</select></label><label>Title<input name="title"></label><label>First name<input name="first_name"></label><label>Last name<input name="last_name"></label><label>Role / Position<select name="role_position"><option>Partner</option><option>Associate</option><option>Of Counsel</option><option>Trainee</option><option>Assistant</option><option>Free data entry</option></select></label><label>Email<input type="email" name="email" required></label><label>Phone<input name="phone"></label><label>Active<select name="active"><option value="1">Yes</option><option value="0">No</option></select></label><label>Notes<textarea name="notes"></textarea></label><label>Default hourly rate<input type="number" step="0.01" name="hourly_rate"></label><label>Default daily rate<input type="number" step="0.01" name="daily_rate"></label><label>Currency<select name="currency"><option>EUR</option><option>USD</option><option>GBP</option><option>CHF</option></select></label><label>Rate notes<textarea name="rate_notes"></textarea></label></div><button>Save Timekeeper Record</button></form></section>{% endblock %}'''

def facts_v7():return render_template_string(FACTS_V7,clients=Client.query.order_by(Client.name).all(),contacts=Contact.query.order_by(Contact.display_name).all(),matters=Matter.query.order_by(Matter.name).all(),users=User.query.order_by(User.display_name).all())
app.view_functions['master_v4']=login_required(admin_required(facts_v7))

@app.post('/admin/data-management/client-fact-v7')
@login_required
@admin_required
def client_fact_v7():
    c=db.session.get(Client,int(request.form['id'])) if request.form.get('id') else Client(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    c.name=request.form['name'];c.billing_name=request.form.get('billing_name') or c.name;c.address=request.form.get('address');c.country=request.form.get('country');c.vat_number=request.form.get('vat_number');c.language=request.form.get('language') or 'English';c.currency=request.form.get('currency') or 'EUR';c.client_hourly_rate=fnum(request.form.get('rate'),None);db.session.add(c);db.session.flush();save_fact('client',c.id,{'payment_terms':request.form.get('payment_terms'),'vat_treatment':request.form.get('vat_treatment'),'billing_email':request.form.get('billing_email')});db.session.commit();flash('Client Record saved.');return redirect(url_for('master_v4'))
@app.post('/admin/data-management/contact-fact-v7')
@login_required
@admin_required
def contact_fact_v7():
    x=db.session.get(Contact,int(request.form['id'])) if request.form.get('id') else Contact(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    x.display_name=request.form['name'];x.email=request.form.get('email');x.phone=request.form.get('phone');x.client_id=int(request.form['client_id']) if request.form.get('client_id') else None;db.session.add(x);db.session.flush();save_fact('contact',x.id,{'title':request.form.get('title'),'salutation':request.form.get('salutation'),'preferred_language':request.form.get('preferred_language'),'address':request.form.get('address')});db.session.commit();flash('Contact Record saved.');return redirect(url_for('master_v4'))
@app.post('/admin/data-management/matter-fact-v7')
@login_required
@admin_required
def matter_fact_v7():
    m=db.session.get(Matter,int(request.form['id'])) if request.form.get('id') else Matter(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
    m.name=request.form['name'];m.reference=request.form.get('reference');m.client_id=int(request.form['client_id']);m.primary_contact_id=int(request.form['contact_id']) if request.form.get('contact_id') else None;m.matter_hourly_rate=fnum(request.form.get('hourly_rate'),None);m.billable=True;db.session.add(m);db.session.flush();save_fact('matter',m.id,{'nature':request.form.get('nature'),'billing_method':request.form.get('billing_method'),'daily_rate':fnum(request.form.get('daily_rate'),None),'currency':request.form.get('currency'),'payment_terms':request.form.get('payment_terms'),'billing_notes':request.form.get('billing_notes'),'linked_client_ids':[m.client_id],'linked_contact_ids':[m.primary_contact_id] if m.primary_contact_id else []});db.session.commit();flash('Matter Record saved.');return redirect(url_for('master_v4'))
@app.post('/admin/data-management/timekeeper-fact-v7')
@login_required
@admin_required
def timekeeper_fact_v7():
    u=db.session.get(User,int(request.form['id'])) if request.form.get('id') else User.query.filter(db.func.lower(User.email)==request.form['email'].strip().lower()).first()
    if not u:u=User(email=request.form['email'].strip().lower(),display_name=(request.form.get('first_name','')+' '+request.form.get('last_name','')).strip() or request.form['email'].split('@')[0],password_hash=generate_password_hash('ChangeMe-Temp-2026'),system_role='Timekeeper')
    u.email=request.form['email'].strip().lower();u.display_name=(request.form.get('first_name','')+' '+request.form.get('last_name','')).strip() or u.display_name;u.active=request.form.get('active')=='1';u.default_hourly_rate=fnum(request.form.get('hourly_rate'),0);db.session.add(u);db.session.flush();save_fact('timekeeper',u.id,{'title':request.form.get('title'),'first_name':request.form.get('first_name'),'last_name':request.form.get('last_name'),'role_position':request.form.get('role_position'),'phone':request.form.get('phone'),'notes':request.form.get('notes'),'default_daily_rate':fnum(request.form.get('daily_rate'),0),'currency':request.form.get('currency'),'rate_notes':request.form.get('rate_notes')});db.session.commit();flash('Timekeeper Record saved.');return redirect(url_for('master_v4'))
@app.post('/admin/data-management/timekeeper-upload-v7')
@login_required
@admin_required
def upload_timekeeper_v7():
    try:
        wb=load_workbook(request.files['file'],data_only=True);ws=wb['Timekeeper Record'] if 'Timekeeper Record' in wb.sheetnames else wb[wb.sheetnames[0]];labels={'Title','First Name','Last Name','Role / Position','Email','Phone','Active?','Notes','Default Hourly Rate','Default Daily Rate','Currency','Rate Notes'};values={}
        for row in ws.iter_rows(values_only=True):
            cells=[v for v in row if v is not None]
            if len(cells)>=2 and str(cells[0]).strip() in labels:values[str(cells[0]).strip()]=cells[1]
        email=str(values.get('Email') or '').strip().lower()
        if not email:raise ValueError('Email is required in the Timekeeper Record.')
        u=User.query.filter(db.func.lower(User.email)==email).first() or User(email=email,display_name=(str(values.get('First Name') or '')+' '+str(values.get('Last Name') or '')).strip() or email.split('@')[0],password_hash=generate_password_hash('ChangeMe-Temp-2026'),system_role='Timekeeper')
        u.active=str(values.get('Active?') or 'Yes').lower() not in {'no','false','0'};u.default_hourly_rate=fnum(values.get('Default Hourly Rate'),0);db.session.add(u);db.session.flush();save_fact('timekeeper',u.id,{'title':values.get('Title'),'first_name':values.get('First Name'),'last_name':values.get('Last Name'),'role_position':values.get('Role / Position'),'phone':values.get('Phone'),'notes':values.get('Notes'),'default_daily_rate':fnum(values.get('Default Daily Rate'),0),'currency':values.get('Currency') or 'EUR','rate_notes':values.get('Rate Notes')});db.session.commit();flash('Timekeeper Record imported.')
    except Exception as exc:db.session.rollback();flash('Timekeeper import failed: '+str(exc))
    return redirect(url_for('master_v4'))


# ==================== CHANGE 3.8: CUSTOM START, USER LIFECYCLE, LINKS AND RATES ====================

def parse_custom_start_v8(value):
    if not value:
        return datetime.now(timezone.utc)
    parsed=datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

TIMER_PAGE_V8=TIMER_PAGE_V7.replace(
    '<label>Billable<select name="billable"><option value="1">Yes</option><option value="0">No</option></select></label></div>',
    '<label>Billable<select name="billable"><option value="1">Yes</option><option value="0">No</option></select></label><label>Start date and time<input type="datetime-local" name="custom_start" value="{{now_local}}"></label></div>'
).replace(
    '<button>Start timer now</button>',
    '<button>Start timer</button>'
).replace(
    '<h2>Active entries</h2>',
    '<h2>Active entries</h2>'
)

def timer_v8():
    active=Timer.query.filter(Timer.user_id==session['uid'],Timer.status.in_(['Running','Paused'])).order_by(Timer.id.desc()).all()
    for entry in active:entry.seconds=elapsed_safe(entry)
    recent=Timer.query.filter_by(user_id=session['uid'],status='Stopped').order_by(Timer.id.desc()).limit(30).all()
    return render_template_string(TIMER_PAGE_V8,clients=Client.query.order_by(Client.name).all(),active=active,recent=recent,now_local=datetime.now().strftime('%Y-%m-%dT%H:%M'))

def start_timer_v8():
    user=db.session.get(User,session['uid']);client=db.session.get(Client,int(request.form['client_id']));matter=db.session.get(Matter,int(request.form['matter_id']))
    rate=matter.matter_hourly_rate if matter.matter_hourly_rate is not None else client.client_hourly_rate if client.client_hourly_rate is not None else user.default_hourly_rate
    entry=Timer(user_id=user.id,client_id=client.id,matter_id=matter.id,category=None,services=request.form['services'].strip(),notes=request.form.get('notes',''),start_time=parse_custom_start_v8(request.form.get('custom_start')),status='Running',paused_seconds=0,timekeeper_rate=user.default_hourly_rate,client_rate=client.client_hourly_rate,matter_rate=matter.matter_hourly_rate,applied_rate=rate or 0,billable=matter.billable and request.form.get('billable')=='1',invoiced_status='Unbilled')
    db.session.add(entry);db.session.commit();flash('Timer started.');return redirect(url_for('timer'))

app.view_functions['timer']=login_required(timer_v8)
app.view_functions['start_timer_v4']=login_required(start_timer_v8)

USER_ADMIN_V8=r'''{% extends "base.html" %}{% block content %}<div style="display:flex;justify-content:space-between;align-items:center"><div><h1>User and timekeeper management</h1><p>Create, edit rates and safely remove users.</p></div><a href="{{url_for('master_v4')}}">Back to Data management</a></div><section class="card"><h2>Create user / timekeeper</h2><form method="post" action="{{url_for('create_user_v8')}}"><div class="grid"><label>Display name<input name="display_name" required></label><label>Email<input type="email" name="email" required></label><label>Temporary password<input type="password" name="password" minlength="10" required></label><label>System role<select name="system_role"><option>Timekeeper</option><option>Administrator</option></select></label><label>Hourly rate<input type="number" min="0" step="0.01" name="hourly_rate" value="0"></label><label>Active<select name="active"><option value="1">Yes</option><option value="0">No</option></select></label></div><button>Create user</button></form></section><section class="card wide"><h2>Existing users</h2><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Hourly rate</th><th>Active</th><th>Actions</th></tr>{% for u in users %}<tr><td>{{u.display_name}}</td><td>{{u.email}}</td><td>{{u.system_role}}</td><td><form method="post" action="{{url_for('admin_user_rate_v8',user_id=u.id)}}"><input type="number" min="0" step="0.01" name="hourly_rate" value="{{u.default_hourly_rate or 0}}"><button>Save rate</button></form></td><td>{{'Yes' if u.active else 'No'}}</td><td>{% if u.id != session.get('uid') %}<form method="post" action="{{url_for('remove_user_v8',user_id=u.id)}}" onsubmit="return confirm('Remove or deactivate this user?');"><button class="danger">Remove</button></form>{% else %}Current account{% endif %}</td></tr>{% endfor %}</table></section>{% endblock %}'''

@app.get('/admin/users-v8')
@login_required
@admin_required
def users_v8():return render_template_string(USER_ADMIN_V8,users=User.query.order_by(User.display_name).all())

@app.post('/admin/users-v8/create')
@login_required
@admin_required
def create_user_v8():
    email=request.form['email'].strip().lower()
    if User.query.filter(db.func.lower(User.email)==email).first():flash('A user with this email already exists.');return redirect(url_for('users_v8'))
    user=User(email=email,display_name=request.form['display_name'].strip(),password_hash=generate_password_hash(request.form['password']),system_role=request.form.get('system_role') or 'Timekeeper',active=request.form.get('active')=='1',default_hourly_rate=max(0,fnum(request.form.get('hourly_rate'),0)))
    db.session.add(user);db.session.commit();flash('User created.');return redirect(url_for('users_v8'))

@app.post('/admin/users-v8/<int:user_id>/rate')
@login_required
@admin_required
def admin_user_rate_v8(user_id):
    user=db.session.get(User,user_id);user.default_hourly_rate=max(0,fnum(request.form.get('hourly_rate'),0));db.session.commit();flash('Hourly rate updated.');return redirect(url_for('users_v8'))

@app.post('/admin/users-v8/<int:user_id>/remove')
@login_required
@admin_required
def remove_user_v8(user_id):
    user=db.session.get(User,user_id)
    if not user:flash('User not found.');return redirect(url_for('users_v8'))
    if user.id==session['uid']:flash('You cannot remove the account currently in use.');return redirect(url_for('users_v8'))
    if Timer.query.filter_by(user_id=user.id).first():
        user.active=False;db.session.commit();flash('The user has historical time records, so the account was deactivated rather than deleted.')
    else:
        EntityFactSheet.query.filter_by(entity_type='timekeeper',entity_id=user.id).delete();db.session.delete(user);db.session.commit();flash('User removed.')
    return redirect(url_for('users_v8'))

# Strengthen contact-to-client linking for new and existing contacts with a dedicated mapping page.
CONTACT_LINKS_V8=r'''{% extends "base.html" %}{% block content %}<h1>Link contacts to clients</h1><p>Update the client linked to any existing contact.</p><section class="card wide"><table><tr><th>Contact</th><th>Email</th><th>Linked client</th><th></th></tr>{% for x in contacts %}<tr><form method="post" action="{{url_for('link_contact_client_v8',contact_id=x.id)}}"><td>{{x.display_name}}</td><td>{{x.email}}</td><td><select name="client_id"><option value="">No client</option>{% for c in clients %}<option value="{{c.id}}" {% if x.client_id==c.id %}selected{% endif %}>{{c.name}}</option>{% endfor %}</select></td><td><button>Save link</button></td></form></tr>{% endfor %}</table></section>{% endblock %}'''

@app.get('/admin/contact-links-v8')
@login_required
@admin_required
def contact_links_v8():return render_template_string(CONTACT_LINKS_V8,contacts=Contact.query.order_by(Contact.display_name).all(),clients=Client.query.order_by(Client.name).all())

@app.post('/admin/contact-links-v8/<int:contact_id>')
@login_required
@admin_required
def link_contact_client_v8(contact_id):
    contact=db.session.get(Contact,contact_id);contact.client_id=int(request.form['client_id']) if request.form.get('client_id') else None;db.session.commit();flash('Contact-client link updated.');return redirect(url_for('contact_links_v8'))

# Add direct access cards without removing any existing Data Management forms.
FACTS_V8=FACTS_V7.replace('<p>Complete Client, Contact, Matter and Timekeeper Records.</p>','<p>Complete Client, Contact, Matter and Timekeeper Records.</p><div class="cards"><section class="card"><h2>Users and timekeepers</h2><a href="{{url_for(\'users_v8\')}}">Create, remove and manage rates</a></section><section class="card"><h2>Contact-client links</h2><a href="{{url_for(\'contact_links_v8\')}}">Manage existing links</a></section></div>')
def facts_v8():return render_template_string(FACTS_V8,clients=Client.query.order_by(Client.name).all(),contacts=Contact.query.order_by(Contact.display_name).all(),matters=Matter.query.order_by(Matter.name).all(),users=User.query.order_by(User.display_name).all())
app.view_functions['master_v4']=login_required(admin_required(facts_v8))


# Optional custom stop date/time. Leaving it empty keeps the proven system stop-time flow.
_original_stop_v8 = app.view_functions['stop_timer']

def stop_timer_v81(timer_id):
    entry = Timer.query.filter_by(id=timer_id, user_id=session['uid']).first_or_404()
    custom_stop = request.form.get('custom_stop', '').strip()
    if not custom_stop:
        return _original_stop_v8(timer_id)
    stop_at = parse_custom_start_v8(custom_stop)
    start_at = entry.start_time
    if start_at and start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    if not start_at or stop_at < start_at:
        flash('The custom stop date and time must be after the start date and time.')
        return redirect(url_for('timer'))
    entry.stop_time = stop_at
    entry.status = 'Stopped'
    worked = max(0.0, (stop_at - start_at).total_seconds() - float(entry.paused_seconds or 0)) / 3600
    entry.worked_hours = round(worked, 4)
    override = request.form.get('billable_hours', '').strip()
    entry.billable_hours = max(0.0, fnum(override, worked)) if entry.billable else 0
    entry.amount = round(float(entry.billable_hours or 0) * float(entry.applied_rate or 0), 2)
    if request.form.get('notes') is not None:
        entry.notes = request.form.get('notes', '')
    db.session.commit()
    flash('Time entry saved with the selected stop date and time.')
    return redirect(url_for('timer'))

app.view_functions['stop_timer'] = login_required(stop_timer_v81)


# ==================== CHANGE 3.9: CUSTOM STOP, EDITABLE ENTRY TIMES, WIDER SERVICES ====================
# Append this block after the existing Change 3.8 code.
# It keeps system start/stop as the default and adds optional custom timestamps.


def _v39_datetime(value, fallback=None):
    """Parse a datetime-local value as UTC, or return the supplied fallback."""
    value = (value or '').strip()
    if not value:
        return fallback
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _v39_input_datetime(value):
    if not value:
        return ''
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime('%Y-%m-%dT%H:%M')


# Build the final timer page directly from the proven V7 page so prior V8 replacements
# cannot prevent the custom-stop control or recent-entry editing from appearing.
TIMER_PAGE_V39 = TIMER_PAGE_V7.replace(
    '<label>Billable<select name="billable"><option value="1">Yes</option><option value="0">No</option></select></label></div>',
    '<label>Billable<select name="billable"><option value="1">Yes</option><option value="0">No</option></select></label>'
    '<label>Custom start date and time, optional<input type="datetime-local" name="custom_start"></label></div>'
).replace(
    '<button>Start timer now</button>',
    '<button>Start timer</button>'
).replace(
    '<label>Billable hours override, optional<input type="number" min="0" step="0.0001" name="billable_hours"></label>'
    '<input type="hidden" name="notes"',
    '<label>Custom stop date and time, optional<input type="datetime-local" name="custom_stop"></label>'
    '<label>Billable hours override, optional<input type="number" min="0" step="0.0001" name="billable_hours"></label>'
    '<input type="hidden" name="notes"'
).replace(
    '<th>Date</th><th>Client</th><th>Matter</th><th>Service</th>',
    '<th>Start date/time</th><th>Stop date/time</th><th>Client</th><th>Matter</th><th class="service-column">Service description</th>'
).replace(
    '<form method="post" action="{{url_for(\'edit_service_v4\',timer_id=t.id)}}"><td>{{t.start_time.date()}}</td><td>{{t.client.name}}</td><td>{{t.matter.name}}</td><td><textarea name="services">{{t.services}}</textarea><input type="hidden" name="notes" value="{{t.notes or \'\'}}"></td>',
    '<form method="post" action="{{url_for(\'edit_time_entry_v39\',timer_id=t.id)}}">'
    '<td><input type="datetime-local" name="start_time" value="{{format_dt_v39(t.start_time)}}" required></td>'
    '<td><input type="datetime-local" name="stop_time" value="{{format_dt_v39(t.stop_time)}}" required></td>'
    '<td>{{t.client.name}}</td><td>{{t.matter.name}}</td>'
    '<td class="service-column"><textarea class="service-description" name="services" required>{{t.services}}</textarea>'
    '<input type="hidden" name="notes" value="{{t.notes or \'\'}}"></td>'
).replace(
    '<button>Save</button>',
    '<button>Save entry</button>'
)


def timer_v39():
    active = Timer.query.filter(
        Timer.user_id == session['uid'],
        Timer.status.in_(['Running', 'Paused'])
    ).order_by(Timer.id.desc()).all()
    for entry in active:
        entry.seconds = elapsed_safe(entry)
    recent = Timer.query.filter_by(
        user_id=session['uid'], status='Stopped'
    ).order_by(Timer.id.desc()).limit(30).all()
    return render_template_string(
        TIMER_PAGE_V39,
        clients=Client.query.order_by(Client.name).all(),
        active=active,
        recent=recent,
        format_dt_v39=_v39_input_datetime,
    )


def start_timer_v39():
    user = db.session.get(User, session['uid'])
    client = db.session.get(Client, int(request.form['client_id']))
    matter = db.session.get(Matter, int(request.form['matter_id']))
    rate = (
        matter.matter_hourly_rate
        if matter.matter_hourly_rate is not None
        else client.client_hourly_rate
        if client.client_hourly_rate is not None
        else user.default_hourly_rate
    )
    start_at = _v39_datetime(request.form.get('custom_start'), datetime.now(timezone.utc))
    entry = Timer(
        user_id=user.id,
        client_id=client.id,
        matter_id=matter.id,
        category=None,
        services=request.form['services'].strip(),
        notes=request.form.get('notes', ''),
        start_time=start_at,
        status='Running',
        paused_seconds=0,
        timekeeper_rate=user.default_hourly_rate,
        client_rate=client.client_hourly_rate,
        matter_rate=matter.matter_hourly_rate,
        applied_rate=rate or 0,
        billable=matter.billable and request.form.get('billable') == '1',
        invoiced_status='Unbilled',
    )
    db.session.add(entry)
    db.session.commit()
    flash('Timer started.')
    return redirect(url_for('timer'))


# Capture the current stop implementation. It remains the default when custom_stop is blank.
_stop_timer_before_v39 = app.view_functions['stop_timer']


def stop_timer_v39(timer_id):
    custom_stop = (request.form.get('custom_stop') or '').strip()
    if not custom_stop:
        return _stop_timer_before_v39(timer_id)

    entry = Timer.query.filter_by(id=timer_id, user_id=session['uid']).first_or_404()
    stop_at = _v39_datetime(custom_stop)
    start_at = entry.start_time
    if start_at and start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    if not start_at or not stop_at or stop_at < start_at:
        flash('The custom stop date and time must be after the start date and time.')
        return redirect(url_for('timer'))

    worked = max(
        0.0,
        ((stop_at - start_at).total_seconds() - float(entry.paused_seconds or 0)) / 3600,
    )
    override = (request.form.get('billable_hours') or '').strip()
    entry.stop_time = stop_at
    entry.status = 'Stopped'
    entry.worked_hours = round(worked, 4)
    entry.billable_hours = (
        max(0.0, fnum(override, worked)) if entry.billable else 0
    )
    entry.amount = round(
        float(entry.billable_hours or 0) * float(entry.applied_rate or 0), 2
    )
    if request.form.get('notes') is not None:
        entry.notes = request.form.get('notes', '')
    db.session.commit()
    flash('Time entry saved with the selected stop date and time.')
    return redirect(url_for('timer'))


@app.post('/timer/<int:timer_id>/edit-times-v39')
@login_required
def edit_time_entry_v39(timer_id):
    query = (
        Timer.query.filter_by(id=timer_id, user_id=session['uid'])
        if session.get('role') != 'Administrator'
        else Timer.query.filter_by(id=timer_id)
    )
    entry = query.first_or_404()
    start_at = _v39_datetime(request.form.get('start_time'))
    stop_at = _v39_datetime(request.form.get('stop_time'))
    if not start_at or not stop_at or stop_at < start_at:
        flash('The stop date and time must be after the start date and time.')
        return redirect(request.referrer or url_for('timer'))

    worked = max(
        0.0,
        ((stop_at - start_at).total_seconds() - float(entry.paused_seconds or 0)) / 3600,
    )
    entry.start_time = start_at
    entry.stop_time = stop_at
    entry.worked_hours = round(worked, 4)
    entry.billable_hours = round(worked, 4) if entry.billable else 0
    entry.amount = round(
        float(entry.billable_hours or 0) * float(entry.applied_rate or 0), 2
    )
    entry.services = request.form.get('services', entry.services).strip()
    entry.notes = request.form.get('notes', entry.notes)
    db.session.commit()
    flash('Time entry date, time and service description updated.')
    return redirect(request.referrer or url_for('timer'))


app.view_functions['timer'] = login_required(timer_v39)
app.view_functions['start_timer_v4'] = login_required(start_timer_v39)
app.view_functions['stop_timer'] = login_required(stop_timer_v39)


# Make service-description fields wider throughout HTML pages without changing every template.
@app.after_request
def widen_service_descriptions_v39(response):
    content_type = response.headers.get('Content-Type', '')
    if response.status_code == 200 and 'text/html' in content_type:
        html = response.get_data(as_text=True)
        css = '''<style id="marsip-service-width-v39">
textarea[name="services"], textarea[name="description"],
.service-description { width:100% !important; min-width:420px; min-height:90px; box-sizing:border-box; }
th.service-column, td.service-column { min-width:440px !important; width:45% !important; }
table { width:100%; }
</style>'''
        if 'marsip-service-width-v39' not in html:
            html = html.replace('</head>', css + '</head>') if '</head>' in html else css + html
            response.set_data(html)
            response.headers['Content-Length'] = len(response.get_data())
    return response

# ==================== CHANGE 3.10: DURATION ENTRY, ADMIN DELETE, AUTOFILL, MULTI-LINKS ====================
# Append this block after Change 3.9 in the existing change3.py.
# Existing invoice, login, data, and user-management behavior is preserved.


class ContactClientLinkV10(db.Model):
    __tablename__ = 'contact_client_link_v10'
    id = db.Column(db.Integer, primary_key=True)
    contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey('client.id'), nullable=False, index=True)
    __table_args__ = (
        db.UniqueConstraint('contact_id', 'client_id', name='uq_contact_client_v10'),
    )


with app.app_context():
    db.create_all()
    # Preserve every existing single contact-to-client relationship in the new multi-link table.
    for _contact in Contact.query.filter(Contact.client_id.isnot(None)).all():
        if not ContactClientLinkV10.query.filter_by(
            contact_id=_contact.id, client_id=_contact.client_id
        ).first():
            db.session.add(ContactClientLinkV10(
                contact_id=_contact.id, client_id=_contact.client_id
            ))
    db.session.commit()


def _v10_date(value):
    value = (value or '').strip()
    if not value:
        return None
    return datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc)


def _v10_json_fact(kind, entity_id):
    return fact_data(kind, entity_id) if entity_id else {}


def _v10_client_payload(client):
    extra = _v10_json_fact('client', client.id)
    return {
        'id': client.id,
        'name': client.name or '',
        'billing_name': client.billing_name or '',
        'address': client.address or '',
        'country': client.country or '',
        'vat_number': client.vat_number or '',
        'language': client.language or 'English',
        'currency': client.currency or 'EUR',
        'rate': client.client_hourly_rate if client.client_hourly_rate is not None else '',
        'payment_terms': extra.get('payment_terms') or '',
        'vat_treatment': extra.get('vat_treatment') or '',
        'billing_email': extra.get('billing_email') or '',
    }


def _v10_contact_payload(contact):
    extra = _v10_json_fact('contact', contact.id)
    linked = [row.client_id for row in ContactClientLinkV10.query.filter_by(contact_id=contact.id).all()]
    if contact.client_id and contact.client_id not in linked:
        linked.append(contact.client_id)
    return {
        'id': contact.id,
        'name': contact.display_name or '',
        'email': contact.email or '',
        'phone': contact.phone or '',
        'title': extra.get('title') or '',
        'salutation': extra.get('salutation') or 'Vous',
        'preferred_language': extra.get('preferred_language') or '',
        'address': extra.get('address') or '',
        'client_ids': linked,
    }


def _v10_matter_payload(matter):
    extra = _v10_json_fact('matter', matter.id)
    return {
        'id': matter.id,
        'name': matter.name or '',
        'reference': matter.reference or '',
        'nature': extra.get('nature') or '',
        'client_id': matter.client_id,
        'contact_id': matter.primary_contact_id or '',
        'billing_method': extra.get('billing_method') or '',
        'hourly_rate': matter.matter_hourly_rate if matter.matter_hourly_rate is not None else '',
        'daily_rate': extra.get('daily_rate') if extra.get('daily_rate') is not None else '',
        'currency': extra.get('currency') or 'EUR',
        'payment_terms': extra.get('payment_terms') or '',
        'billing_notes': extra.get('billing_notes') or '',
    }


# New time-entry screen:
# - no service description in the entry-creation section;
# - no custom time fields;
# - optional custom date plus duration immediately creates a stopped entry;
# - without custom date, Start timer uses system time;
# - service description remains in Active entries;
# - billable-hours override is editable in Active and Recent entries;
# - administrators can delete Recent entries.
TIMER_PAGE_V10 = r'''{% extends "base.html" %}{% block content %}
<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap">
  <div><h1>Time recording</h1><p>Start a live timer, or enter a date and duration to create a completed entry.</p></div>
  <a href="{{url_for('password_v4')}}">Change password</a>
</div>
<section class="card"><h2>New time entry</h2><form method="post" action="{{url_for('start_timer_v4')}}">
<div class="grid">
<label>Client<select id="client" name="client_id" required><option value="">Select</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label>
<label>Matter<select id="matter" name="matter_id" required><option value="">Select a client first</option></select></label>
<label>Billable<select name="billable"><option value="1">Yes</option><option value="0">No</option></select></label>
<label>Custom date, optional<input type="date" name="custom_date"></label>
<label>Duration in hours<input type="number" min="0.0001" step="0.0001" name="duration_hours" placeholder="Required when a custom date is selected"></label>
</div>
<label>Internal notes<input name="notes"></label>
<p>If Custom date is empty, Start timer records the current system time. If a date and duration are entered, a completed entry is created immediately.</p>
<button>Start timer / save dated duration</button></form></section>
<h2>Active entries</h2><div class="cards">{% for t in active %}<section class="card"><h3>{{t.client.name}}</h3><p>{{t.matter.name}}</p>
<form method="post" action="{{url_for('edit_service_v4',timer_id=t.id)}}"><label>Service description<textarea class="service-description" name="services" required>{{t.services or ''}}</textarea></label><label>Notes<input name="notes" value="{{t.notes or ''}}"></label><button>Save description</button></form>
<div class="clock" data-s="{{t.seconds}}" data-run="{{1 if t.status=='Running' else 0}}"></div><p>{{t.status}}</p>
{% if t.status=='Running' %}<form method="post" action="{{url_for('pause_timer',timer_id=t.id)}}"><button>Pause</button></form>{% else %}<form method="post" action="{{url_for('resume_timer',timer_id=t.id)}}"><button>Resume</button></form>{% endif %}
<form method="post" action="{{url_for('stop_timer',timer_id=t.id)}}"><label>Override billable hours, optional<input type="number" min="0" step="0.0001" name="billable_hours"></label><input type="hidden" name="notes" value="{{t.notes or ''}}"><button class="danger">Stop and save</button></form></section>{% else %}<section class="card">No active entries.</section>{% endfor %}</div>
<section class="card wide"><h2>Recent entries</h2><table><tr><th>Date</th><th>Client</th><th>Matter</th><th>Hours</th><th>Override billable hours</th><th>Rate</th><th>Amount</th><th>Actions</th></tr>
{% for t in recent %}<tr><form method="post" action="{{url_for('update_recent_entry_v10',timer_id=t.id)}}"><td><input type="date" name="entry_date" value="{{t.start_time.strftime('%Y-%m-%d')}}" required></td><td>{{t.client.name}}</td><td>{{t.matter.name}}</td><td>{{t.worked_hours}}</td><td><input type="number" min="0" step="0.0001" name="billable_hours" value="{{t.billable_hours or 0}}"></td><td>{{t.applied_rate}}</td><td>{{t.amount}}</td><td><button>Save entry</button></form>{% if session.get('role')=='Administrator' %}<form method="post" action="{{url_for('delete_time_entry_v10',timer_id=t.id)}}" onsubmit="return confirm('Delete this time entry permanently?');"><button class="danger">Delete</button></form>{% endif %}</td></tr>{% endfor %}
</table></section>
<script>
const client=document.getElementById('client'),matter=document.getElementById('matter');
client.addEventListener('change',async()=>{matter.innerHTML='<option value="">Loading...</option>';if(!client.value){matter.innerHTML='<option value="">Select a client first</option>';return;}const r=await fetch('/api/matters/'+client.value),d=await r.json();matter.innerHTML='<option value="">Select a matter</option>'+d.map(x=>`<option value="${x.id}">${x.reference||''}${x.reference?' | ':''}${x.name}</option>`).join('');});
document.querySelectorAll('.clock').forEach(e=>{let s=Number(e.dataset.s||0);const draw=()=>{e.textContent=[Math.floor(s/3600),Math.floor((s%3600)/60),Math.floor(s%60)].map(x=>String(x).padStart(2,'0')).join(':');if(e.dataset.run==='1')s+=1;};draw();setInterval(draw,1000);});
</script>{% endblock %}'''


def timer_v10():
    active = Timer.query.filter(
        Timer.user_id == session['uid'], Timer.status.in_(['Running', 'Paused'])
    ).order_by(Timer.id.desc()).all()
    for entry in active:
        entry.seconds = elapsed_safe(entry)
    recent_query = Timer.query.filter_by(status='Stopped') if session.get('role') == 'Administrator' else Timer.query.filter_by(user_id=session['uid'], status='Stopped')
    recent = recent_query.order_by(Timer.id.desc()).limit(50).all()
    return render_template_string(
        TIMER_PAGE_V10,
        clients=Client.query.order_by(Client.name).all(),
        active=active,
        recent=recent,
    )


def start_timer_v10():
    user = db.session.get(User, session['uid'])
    client = db.session.get(Client, int(request.form['client_id']))
    matter = db.session.get(Matter, int(request.form['matter_id']))
    rate = matter.matter_hourly_rate if matter.matter_hourly_rate is not None else client.client_hourly_rate if client.client_hourly_rate is not None else user.default_hourly_rate
    custom_date = _v10_date(request.form.get('custom_date'))
    duration_text = (request.form.get('duration_hours') or '').strip()
    is_billable = matter.billable and request.form.get('billable') == '1'
    if custom_date:
        duration = fnum(duration_text, None)
        if duration is None or duration <= 0:
            flash('Enter a duration greater than zero for a custom-date entry.')
            return redirect(url_for('timer'))
        stop_at = custom_date + timedelta(hours=duration)
        entry = Timer(
            user_id=user.id, client_id=client.id, matter_id=matter.id, category=None,
            services='', notes=request.form.get('notes', ''), start_time=custom_date,
            stop_time=stop_at, status='Stopped', paused_seconds=0,
            timekeeper_rate=user.default_hourly_rate, client_rate=client.client_hourly_rate,
            matter_rate=matter.matter_hourly_rate, applied_rate=rate or 0,
            billable=is_billable, worked_hours=round(duration, 4),
            billable_hours=round(duration, 4) if is_billable else 0,
            amount=round(duration * float(rate or 0), 2) if is_billable else 0,
            invoiced_status='Unbilled',
        )
        flash('Completed dated entry saved. Add the service description from Active entries only when using a live timer.')
    else:
        entry = Timer(
            user_id=user.id, client_id=client.id, matter_id=matter.id, category=None,
            services='', notes=request.form.get('notes', ''), start_time=datetime.now(timezone.utc),
            status='Running', paused_seconds=0, timekeeper_rate=user.default_hourly_rate,
            client_rate=client.client_hourly_rate, matter_rate=matter.matter_hourly_rate,
            applied_rate=rate or 0, billable=is_billable, invoiced_status='Unbilled',
        )
        flash('Timer started with the current system time.')
    db.session.add(entry)
    db.session.commit()
    return redirect(url_for('timer'))


@app.post('/timer/<int:timer_id>/recent-v10')
@login_required
def update_recent_entry_v10(timer_id):
    query = Timer.query.filter_by(id=timer_id) if session.get('role') == 'Administrator' else Timer.query.filter_by(id=timer_id, user_id=session['uid'])
    entry = query.first_or_404()
    new_date = _v10_date(request.form.get('entry_date'))
    if new_date:
        duration = float(entry.worked_hours or 0)
        entry.start_time = new_date
        entry.stop_time = new_date + timedelta(hours=duration)
    entry.billable_hours = max(0, fnum(request.form.get('billable_hours'), entry.billable_hours or 0)) if entry.billable else 0
    entry.amount = round(float(entry.billable_hours or 0) * float(entry.applied_rate or 0), 2)
    db.session.commit()
    flash('Recent entry updated.')
    return redirect(url_for('timer'))


@app.post('/admin/time-entry/<int:timer_id>/delete-v10')
@login_required
@admin_required
def delete_time_entry_v10(timer_id):
    entry = Timer.query.filter_by(id=timer_id).first_or_404()
    if entry.invoiced_status == 'Invoiced' or entry.invoice_number:
        flash('This entry is already invoiced and cannot be deleted.')
    else:
        db.session.delete(entry)
        db.session.commit()
        flash('Time entry deleted.')
    return redirect(request.referrer or url_for('timer'))


app.view_functions['timer'] = login_required(timer_v10)
app.view_functions['start_timer_v4'] = login_required(start_timer_v10)


# User management with editable name, email, role, active status and rate.
USER_ADMIN_V10 = r'''{% extends "base.html" %}{% block content %}<h1>User and timekeeper management</h1><p><a href="{{url_for('master_v4')}}">Back to Data management</a></p>
<section class="card"><h2>Create user / timekeeper</h2><form method="post" action="{{url_for('create_user_v8')}}"><div class="grid"><label>Display name<input name="display_name" required></label><label>Email<input type="email" name="email" required></label><label>Temporary password<input type="password" name="password" minlength="10" required></label><label>System role<select name="system_role"><option>Timekeeper</option><option>Administrator</option></select></label><label>Hourly rate<input type="number" min="0" step="0.01" name="hourly_rate" value="0"></label><label>Active<select name="active"><option value="1">Yes</option><option value="0">No</option></select></label></div><button>Create user</button></form></section>
<section class="card wide"><h2>Existing users</h2><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Hourly rate</th><th>Active</th><th>Actions</th></tr>{% for u in users %}<tr><form method="post" action="{{url_for('edit_user_v10',user_id=u.id)}}"><td><input name="display_name" value="{{u.display_name}}" required></td><td><input type="email" name="email" value="{{u.email}}" required></td><td><select name="system_role"><option {% if u.system_role=='Timekeeper' %}selected{% endif %}>Timekeeper</option><option {% if u.system_role=='Administrator' %}selected{% endif %}>Administrator</option></select></td><td><input type="number" min="0" step="0.01" name="hourly_rate" value="{{u.default_hourly_rate or 0}}"></td><td><select name="active"><option value="1" {% if u.active %}selected{% endif %}>Yes</option><option value="0" {% if not u.active %}selected{% endif %}>No</option></select></td><td><button>Save details</button></form>{% if u.id != session.get('uid') %}<form method="post" action="{{url_for('remove_user_v8',user_id=u.id)}}" onsubmit="return confirm('Remove or deactivate this user?');"><button class="danger">Remove</button></form>{% endif %}</td></tr>{% endfor %}</table></section>{% endblock %}'''


def users_v10():
    return render_template_string(USER_ADMIN_V10, users=User.query.order_by(User.display_name).all())


@app.post('/admin/users-v10/<int:user_id>/edit')
@login_required
@admin_required
def edit_user_v10(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.')
        return redirect(url_for('users_v8'))
    email = request.form['email'].strip().lower()
    duplicate = User.query.filter(db.func.lower(User.email) == email, User.id != user.id).first()
    if duplicate:
        flash('Another user already uses this email address.')
        return redirect(url_for('users_v8'))
    user.display_name = request.form['display_name'].strip()
    user.email = email
    user.system_role = request.form.get('system_role') or user.system_role
    user.default_hourly_rate = max(0, fnum(request.form.get('hourly_rate'), 0))
    user.active = request.form.get('active') == '1'
    db.session.commit()
    if user.id == session.get('uid'):
        session['name'] = user.display_name
        session['role'] = user.system_role
    flash('User details updated.')
    return redirect(url_for('users_v8'))


app.view_functions['users_v8'] = login_required(admin_required(users_v10))


# Data-management page with automatic form population for existing records.
DATA_MANAGEMENT_V10 = r'''{% extends "base.html" %}{% block content %}<h1>Data management</h1><p>Select an existing record to load its saved details, edit them, and save.</p>
<div class="cards"><section class="card"><a href="{{url_for('users_v8')}}">Users and timekeepers</a></section><section class="card"><a href="{{url_for('contact_links_v8')}}">Contact-client links</a></section></div>
<section class="card"><h2>Client Record</h2><form method="post" action="{{url_for('client_fact_v7')}}"><div class="grid"><label>Existing<select id="client-record" name="id"><option value="">New client</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Name<input name="name" required></label><label>Billing name<input name="billing_name"></label><label>Address<textarea name="address"></textarea></label><label>Country<input name="country"></label><label>VAT number<input name="vat_number"></label><label>Language<input name="language" value="English"></label><label>Currency<input name="currency" value="EUR"></label><label>Payment terms<input name="payment_terms"></label><label>VAT treatment<input name="vat_treatment"></label><label>Billing email<input type="email" name="billing_email"></label><label>Optional client rate<input type="number" step="0.01" name="rate"></label></div><button>Save Client Record</button></form></section>
<section class="card"><h2>Contact Record</h2><form method="post" action="{{url_for('contact_fact_v7')}}"><div class="grid"><label>Existing<select id="contact-record" name="id"><option value="">New contact</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Name<input name="name" required></label><label>Title<input name="title"></label><label>Exact salutation<select name="salutation"><option>Tu</option><option>Vous</option><option>Sie</option><option>Du</option></select></label><label>Preferred language<input name="preferred_language"></label><label>Email<input type="email" name="email"></label><label>Phone<input name="phone"></label><label>Address<textarea name="address"></textarea></label><label>Primary linked client<select name="client_id"><option value="">None</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label></div><button>Save Contact Record</button></form></section>
<section class="card"><h2>Matter Record</h2><form method="post" action="{{url_for('matter_fact_v7')}}"><div class="grid"><label>Existing<select id="matter-record" name="id"><option value="">New matter</option>{% for m in matters %}<option value="{{m.id}}">{{m.reference or ''}} | {{m.name}}</option>{% endfor %}</select></label><label>Matter name<input name="name" required></label><label>Nature<input name="nature"></label><label>Reference number(s)<input name="reference"></label><label>Linked client<select name="client_id" required>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Linked contact<select name="contact_id"><option value="">None</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}}</option>{% endfor %}</select></label><label>Billing method<input name="billing_method"></label><label>Hourly rate<input type="number" step="0.01" name="hourly_rate"></label><label>Daily rate<input type="number" step="0.01" name="daily_rate"></label><label>Currency<input name="currency" value="EUR"></label><label>Payment terms<input name="payment_terms"></label><label>Billing notes<textarea name="billing_notes"></textarea></label></div><button>Save Matter Record</button></form></section>
<script>
const clients={{client_payload|tojson}},contacts={{contact_payload|tojson}},matters={{matter_payload|tojson}};
function fill(selectId,data){const select=document.getElementById(selectId),form=select.closest('form');select.addEventListener('change',()=>{const row=data[select.value]||{};[...form.elements].forEach(el=>{if(!el.name||el.name==='id')return;let value=row[el.name];if(value===undefined||value===null)value='';el.value=value;});});}
fill('client-record',clients);fill('contact-record',contacts);fill('matter-record',matters);
</script>{% endblock %}'''


def data_management_v10():
    clients = Client.query.order_by(Client.name).all()
    contacts = Contact.query.order_by(Contact.display_name).all()
    matters = Matter.query.order_by(Matter.name).all()
    return render_template_string(
        DATA_MANAGEMENT_V10,
        clients=clients, contacts=contacts, matters=matters,
        client_payload={str(c.id): _v10_client_payload(c) for c in clients},
        contact_payload={str(c.id): _v10_contact_payload(c) for c in contacts},
        matter_payload={str(m.id): _v10_matter_payload(m) for m in matters},
    )


app.view_functions['master_v4'] = login_required(admin_required(data_management_v10))


# Many-to-many contact/client linking. Contact.client_id remains the primary link for
# compatibility, while ContactClientLinkV10 stores every selected relationship.
CONTACT_LINKS_V10 = r'''{% extends "base.html" %}{% block content %}<h1>Link contacts to clients</h1><p>Each contact may be linked to multiple clients, and each client may have multiple contacts.</p><section class="card wide"><table><tr><th>Contact</th><th>Email</th><th>Linked clients</th><th></th></tr>{% for x in contacts %}<tr><form method="post" action="{{url_for('link_contact_clients_v10',contact_id=x.id)}}"><td>{{x.display_name}}</td><td>{{x.email}}</td><td><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px">{% for c in clients %}<label class="check"><input type="checkbox" name="client_ids" value="{{c.id}}" {% if c.id in links.get(x.id,[]) %}checked{% endif %}> {{c.name}}</label>{% endfor %}</div></td><td><button>Save links</button></td></form></tr>{% endfor %}</table></section>{% endblock %}'''


def contact_links_v10():
    links = {}
    for row in ContactClientLinkV10.query.all():
        links.setdefault(row.contact_id, []).append(row.client_id)
    for contact in Contact.query.filter(Contact.client_id.isnot(None)).all():
        links.setdefault(contact.id, [])
        if contact.client_id not in links[contact.id]:
            links[contact.id].append(contact.client_id)
    return render_template_string(
        CONTACT_LINKS_V10,
        contacts=Contact.query.order_by(Contact.display_name).all(),
        clients=Client.query.order_by(Client.name).all(),
        links=links,
    )


@app.post('/admin/contact-links-v10/<int:contact_id>')
@login_required
@admin_required
def link_contact_clients_v10(contact_id):
    contact = db.session.get(Contact, contact_id)
    selected = []
    for value in request.form.getlist('client_ids'):
        try:
            selected.append(int(value))
        except (TypeError, ValueError):
            pass
    ContactClientLinkV10.query.filter_by(contact_id=contact_id).delete()
    for client_id in sorted(set(selected)):
        if db.session.get(Client, client_id):
            db.session.add(ContactClientLinkV10(contact_id=contact_id, client_id=client_id))
    contact.client_id = selected[0] if selected else None
    db.session.commit()
    flash('Contact-client links updated.')
    return redirect(url_for('contact_links_v8'))


app.view_functions['contact_links_v8'] = login_required(admin_required(contact_links_v10))

# ==================== CHANGE 3.10.1: RECENT ENTRY SERVICE DESCRIPTION FIX ====================
# Append this block after Change 3.10.
# It changes only the Recent entries table and its save handler.

TIMER_PAGE_V101 = TIMER_PAGE_V10.replace(
    '<th>Hours</th><th>Override billable hours</th>',
    '<th class="service-column">Service description</th><th>Hours</th><th>Override billable hours</th>'
).replace(
    '<td>{{t.client.name}}</td><td>{{t.matter.name}}</td><td>{{t.worked_hours}}</td><td><input type="number" min="0" step="0.0001" name="billable_hours" value="{{t.billable_hours or 0}}"></td>',
    '<td>{{t.client.name}}</td><td>{{t.matter.name}}</td>'
    '<td class="service-column"><textarea class="service-description" name="services">{{t.services or \'\'}}</textarea></td>'
    '<td>{{t.worked_hours}}</td><td><input type="number" min="0" step="0.0001" name="billable_hours" value="{{t.billable_hours or 0}}"></td>'
)


def timer_v101():
    active = Timer.query.filter(
        Timer.user_id == session['uid'], Timer.status.in_(['Running', 'Paused'])
    ).order_by(Timer.id.desc()).all()
    for entry in active:
        entry.seconds = elapsed_safe(entry)
    recent_query = (
        Timer.query.filter_by(status='Stopped')
        if session.get('role') == 'Administrator'
        else Timer.query.filter_by(user_id=session['uid'], status='Stopped')
    )
    recent = recent_query.order_by(Timer.id.desc()).limit(50).all()
    return render_template_string(
        TIMER_PAGE_V101,
        clients=Client.query.order_by(Client.name).all(),
        active=active,
        recent=recent,
    )


def update_recent_entry_v101(timer_id):
    query = (
        Timer.query.filter_by(id=timer_id)
        if session.get('role') == 'Administrator'
        else Timer.query.filter_by(id=timer_id, user_id=session['uid'])
    )
    entry = query.first_or_404()
    new_date = _v10_date(request.form.get('entry_date'))
    if new_date:
        duration = float(entry.worked_hours or 0)
        entry.start_time = new_date
        entry.stop_time = new_date + timedelta(hours=duration)
    entry.services = request.form.get('services', entry.services or '').strip()
    entry.billable_hours = (
        max(0, fnum(request.form.get('billable_hours'), entry.billable_hours or 0))
        if entry.billable else 0
    )
    entry.amount = round(
        float(entry.billable_hours or 0) * float(entry.applied_rate or 0), 2
    )
    db.session.commit()
    flash('Recent entry, service description and billable hours updated.')
    return redirect(url_for('timer'))


app.view_functions['timer'] = login_required(timer_v101)
app.view_functions['update_recent_entry_v10'] = login_required(update_recent_entry_v101)

