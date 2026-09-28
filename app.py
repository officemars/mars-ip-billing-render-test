from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timezone, date
from pathlib import Path
from openpyxl import load_workbook
from docx import Document
from copy import deepcopy
import os, re, io

BASE=Path(__file__).resolve().parent
ASSETS=BASE/'assets'
app=Flask(__name__)
app.secret_key=os.environ.get('SECRET_KEY','dev-only-change-me')
db_url = os.environ["DATABASE_URL"]
if db_url.startswith("postgresql://"):db_url = db_url.replace("postgresql://","postgresql+psycopg://",1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS']=False
app.config['SESSION_COOKIE_HTTPONLY']=True
app.config['SESSION_COOKIE_SAMESITE']='Lax'
app.config['SESSION_COOKIE_SECURE']=os.environ.get('COOKIE_SECURE','0')=='1'
db=SQLAlchemy(app)

class User(db.Model):
 id=db.Column(db.Integer,primary_key=True);email=db.Column(db.String(255),unique=True,nullable=False,index=True);password_hash=db.Column(db.Text,nullable=False);display_name=db.Column(db.String(255),nullable=False);role=db.Column(db.String(80),default='Timekeeper');system_role=db.Column(db.String(40),default='Timekeeper',index=True);active=db.Column(db.Boolean,default=True);default_hourly_rate=db.Column(db.Numeric(12,2),default=0);default_daily_rate=db.Column(db.Numeric(12,2),default=0)
class Client(db.Model):
 id=db.Column(db.Integer,primary_key=True);external_id=db.Column(db.String(100),unique=True,index=True);name=db.Column(db.String(255),nullable=False,index=True);billing_name=db.Column(db.String(255));email=db.Column(db.String(255));phone=db.Column(db.String(100));address=db.Column(db.Text);country=db.Column(db.String(100));vat_number=db.Column(db.String(100));language=db.Column(db.String(40),default='English');currency=db.Column(db.String(10),default='EUR');payment_terms=db.Column(db.String(255));vat_treatment=db.Column(db.String(255));billing_email=db.Column(db.String(255));client_hourly_rate=db.Column(db.Numeric(12,2))
class Contact(db.Model):
 id=db.Column(db.Integer,primary_key=True);external_id=db.Column(db.String(100),unique=True);display_name=db.Column(db.String(255),nullable=False);email=db.Column(db.String(255));phone=db.Column(db.String(100));salutation=db.Column(db.String(100));language=db.Column(db.String(40));organisation=db.Column(db.String(255));client_id=db.Column(db.Integer,db.ForeignKey('client.id'))
class Matter(db.Model):
 id=db.Column(db.Integer,primary_key=True);external_id=db.Column(db.String(100),unique=True,index=True);reference=db.Column(db.String(100));name=db.Column(db.String(500),nullable=False,index=True);nature=db.Column(db.String(255));client_id=db.Column(db.Integer,db.ForeignKey('client.id'),nullable=False,index=True);primary_contact_id=db.Column(db.Integer,db.ForeignKey('contact.id'));language=db.Column(db.String(40));billing_mode=db.Column(db.String(100),default='Hourly rate');matter_hourly_rate=db.Column(db.Numeric(12,2));matter_daily_rate=db.Column(db.Numeric(12,2));fixed_fee=db.Column(db.Numeric(12,2));currency=db.Column(db.String(10),default='EUR');payment_terms=db.Column(db.String(255));vat_treatment=db.Column(db.String(255));billable=db.Column(db.Boolean,default=True);closing_date=db.Column(db.String(40));notes=db.Column(db.Text)
class Timer(db.Model):
 id=db.Column(db.Integer,primary_key=True);user_id=db.Column(db.Integer,db.ForeignKey('user.id'),nullable=False,index=True);client_id=db.Column(db.Integer,db.ForeignKey('client.id'),nullable=False,index=True);matter_id=db.Column(db.Integer,db.ForeignKey('matter.id'),nullable=False,index=True);category=db.Column(db.String(100));services=db.Column(db.Text,nullable=False);notes=db.Column(db.Text);start_time=db.Column(db.DateTime(timezone=True),nullable=False);stop_time=db.Column(db.DateTime(timezone=True));status=db.Column(db.String(20),default='Running',index=True);paused_at=db.Column(db.DateTime(timezone=True));paused_seconds=db.Column(db.Float,default=0);worked_hours=db.Column(db.Float);billable_hours=db.Column(db.Float);timekeeper_rate=db.Column(db.Numeric(12,2));client_rate=db.Column(db.Numeric(12,2));matter_rate=db.Column(db.Numeric(12,2));applied_rate=db.Column(db.Numeric(12,2));billable=db.Column(db.Boolean,default=True);amount=db.Column(db.Numeric(14,2));invoiced_status=db.Column(db.String(30),default='Unbilled',index=True);invoice_number=db.Column(db.String(100));user=db.relationship(User);client=db.relationship(Client);matter=db.relationship(Matter)
class Pause(db.Model):
 id=db.Column(db.Integer,primary_key=True);timer_id=db.Column(db.Integer,db.ForeignKey('timer.id',ondelete='CASCADE'),nullable=False,index=True);paused_at=db.Column(db.DateTime(timezone=True),nullable=False);resumed_at=db.Column(db.DateTime(timezone=True));seconds=db.Column(db.Float);timer=db.relationship(Timer)
class Invoice(db.Model):
 id=db.Column(db.Integer,primary_key=True);number=db.Column(db.String(100),unique=True);status=db.Column(db.String(30),default='Draft');client_id=db.Column(db.Integer,db.ForeignKey('client.id'));matter_id=db.Column(db.Integer,db.ForeignKey('matter.id'));language=db.Column(db.String(30),default='English');currency=db.Column(db.String(10),default='EUR');vat_rate=db.Column(db.Float,default=0);net_total=db.Column(db.Numeric(14,2),default=0);vat_amount=db.Column(db.Numeric(14,2),default=0);amount_due=db.Column(db.Numeric(14,2),default=0);created_by=db.Column(db.Integer,db.ForeignKey('user.id'));created_at=db.Column(db.DateTime(timezone=True),default=lambda:datetime.now(timezone.utc));client=db.relationship(Client);matter=db.relationship(Matter)
class InvoiceLine(db.Model):
 id=db.Column(db.Integer,primary_key=True);invoice_id=db.Column(db.Integer,db.ForeignKey('invoice.id',ondelete='CASCADE'),nullable=False);sort_order=db.Column(db.Integer,default=0);line_type=db.Column(db.String(100));description=db.Column(db.Text,nullable=False);quantity=db.Column(db.Float,default=1);rate=db.Column(db.Numeric(12,2),default=0);net_amount=db.Column(db.Numeric(14,2),default=0);source_timer_id=db.Column(db.Integer,db.ForeignKey('timer.id'));invoice=db.relationship(Invoice,backref=db.backref('lines',cascade='all, delete-orphan'));timer=db.relationship(Timer)

def utcnow():return datetime.now(timezone.utc)
def n(v):
 try:return float(v)
 except:return None
def clean(v):return '' if v is None else str(v).strip()
def key(v):return re.sub(r'[^a-z0-9]','',clean(v).lower())
def header(ws,needle):
 for rn in range(1,min(ws.max_row,25)+1):
  vals=[clean(c.value) for c in ws[rn]]
  if needle in vals:return rn,{x:i for i,x in enumerate(vals) if x}
 return None,{}
def import_master():
 p=ASSETS/'MARS-IP_Unified_Billing_System_FINAL 1.xlsx'
 if not p.exists():return False
 wb=load_workbook(p,data_only=True,read_only=True)
 if 'Clients' in wb.sheetnames:
  ws=wb['Clients'];rn,h=header(ws,'Client Name')
  for vals in ws.iter_rows(min_row=rn+1,values_only=True):
   g=lambda x:clean(vals[h[x]]) if x in h and h[x]<len(vals) else ''
   if not g('Client Name'):continue
   c=Client.query.filter_by(external_id=g('Client ID')).first() or Client(external_id=g('Client ID'))
   c.name=g('Client Name');c.billing_name=g('Billing Name') or c.name;c.email=g('Email');c.phone=g('Phone');c.address=g('Address');c.vat_number=g('VAT Number');c.language=g('Language') or 'English';c.currency=g('Currency') or 'EUR';c.payment_terms=g('Payment Terms');c.vat_treatment=g('VAT Treatment');c.billing_email=g('Billing Email');c.client_hourly_rate=n(g('Client Hourly Rate'));db.session.add(c)
  db.session.flush()
 clients={key(c.name):c.id for c in Client.query.all()}
 if 'Contacts' in wb.sheetnames:
  ws=wb['Contacts'];rn,h=header(ws,'Display Name')
  for vals in ws.iter_rows(min_row=rn+1,values_only=True):
   g=lambda x:clean(vals[h[x]]) if x in h and h[x]<len(vals) else ''
   if not g('Display Name'):continue
   c=Contact.query.filter_by(external_id=g('Contact ID')).first() or Contact(external_id=g('Contact ID'))
   c.display_name=g('Display Name');c.email=g('Email');c.phone=g('Phone');c.salutation=g('Exact Salutation');c.language=g('Preferred Language');c.organisation=g('Linked Organisation');c.client_id=clients.get(key(c.organisation));db.session.add(c)
 if 'Matters' in wb.sheetnames:
  ws=wb['Matters'];rn,h=header(ws,'Matter Name')
  for vals in ws.iter_rows(min_row=rn+1,values_only=True):
   g=lambda x:clean(vals[h[x]]) if x in h and h[x]<len(vals) else ''
   cid=clients.get(key(g('Client Name')))
   if not g('Matter Name') or not cid:continue
   m=Matter.query.filter_by(external_id=g('Matter ID')).first() or Matter(external_id=g('Matter ID'))
   m.reference=g('Matter Reference');m.name=g('Matter Name');m.nature=g('Nature');m.client_id=cid;m.language=g('Language');m.billing_mode=g('Billing Mode') or 'Hourly rate';m.matter_hourly_rate=n(g('Matter Hourly Rate'));m.matter_daily_rate=n(g('Matter Daily Rate'));m.fixed_fee=n(g('Fixed Fee'));m.currency=g('Currency') or 'EUR';m.payment_terms=g('Payment Terms');m.vat_treatment=g('VAT Treatment');m.billable=g('Billable?')!='No';m.closing_date=g('Closing Date');m.notes=g('Notes');db.session.add(m)
 if 'Users & Timekeepers' in wb.sheetnames:
  ws=wb['Users & Timekeepers'];rn,h=header(ws,'Display Name')
  for vals in ws.iter_rows(min_row=rn+1,values_only=True):
   g=lambda x:clean(vals[h[x]]) if x in h and h[x]<len(vals) else ''
   if not g('Display Name') or not g('Email'):continue
   u=User.query.filter(db.func.lower(User.email)==g('Email').lower()).first()
   if not u:u=User(email=g('Email').lower(),password_hash=generate_password_hash('ChangeMe123!'))
   u.display_name=g('Display Name');u.role=g('Role');u.system_role=g('System Role') or 'Timekeeper';u.active=g('Active?')!='No';u.default_hourly_rate=n(g('Default Hourly Rate')) or 0;u.default_daily_rate=n(g('Default Daily Rate')) or 0;db.session.add(u)
 db.session.commit();return True

def bootstrap():
 db.create_all();email=os.environ.get('ADMIN_EMAIL','admin@mars-ip.eu').lower();u=User.query.filter(db.func.lower(User.email)==email).first()
 if not u:
  u=User(email=email,password_hash=generate_password_hash(os.environ.get('ADMIN_PASSWORD','ChangeMe123!')),display_name='MARS-IP Administrator',role='Administrator',system_role='Administrator',active=True);db.session.add(u);db.session.commit()
 if Client.query.count()==0:import_master()

def login_required(f):
 @wraps(f)
 def w(*a,**k):return f(*a,**k) if session.get('uid') else redirect(url_for('login'))
 return w
def admin_required(f):
 @wraps(f)
 def w(*a,**k):return f(*a,**k) if session.get('role')=='Administrator' else ('Forbidden',403)
 return w
def elapsed(t,at=None):
 at=at or utcnow();start=t.start_time
 if start.tzinfo is None:start=start.replace(tzinfo=timezone.utc)
 sec=(at-start).total_seconds()-float(t.paused_seconds or 0)
 if t.status=='Paused' and t.paused_at:
  p=t.paused_at if t.paused_at.tzinfo else t.paused_at.replace(tzinfo=timezone.utc);sec-=(at-p).total_seconds()
 return max(0,sec)

@app.before_request
def ensure_db():
 if not getattr(app,'_booted',False):bootstrap();app._booted=True
@app.get('/health')
def health():return {'status':'ok'}
@app.route('/',methods=['GET','POST'])
def login():
 if request.method=='POST':
  u=User.query.filter(db.func.lower(User.email)==request.form['email'].strip().lower(),User.active.is_(True)).first()
  if u and check_password_hash(u.password_hash,request.form['password']):session.clear();session.update(uid=u.id,name=u.display_name,role=u.system_role);return redirect(url_for('timer'))
  flash('Invalid login details.')
 return render_template('login.html')
@app.route('/register',methods=['GET','POST'])
def register():
 if request.method=='POST':
  f=request.form;email=f['email'].strip().lower()
  if User.query.filter(db.func.lower(User.email)==email).first():flash('Account already exists.')
  elif len(f['password'])<10 or f['password']!=f['confirm']:flash('Use matching passwords with at least 10 characters.')
  else:db.session.add(User(email=email,password_hash=generate_password_hash(f['password']),display_name=f['name'].strip(),role='Timekeeper',system_role='Timekeeper',active=True,default_hourly_rate=0));db.session.commit();flash('Account created.');return redirect(url_for('login'))
 return render_template('register.html')
@app.get('/logout')
def logout():session.clear();return redirect(url_for('login'))
@app.get('/api/matters/<int:cid>')
@login_required
def matters(cid):return jsonify([{'id':m.id,'reference':m.reference,'name':m.name} for m in Matter.query.filter_by(client_id=cid).order_by(Matter.name).all()])
@app.get('/timer')
@login_required
def timer():
 active=Timer.query.filter(Timer.user_id==session['uid'],Timer.status.in_(['Running','Paused'])).order_by(Timer.id.desc()).all()
 for t in active:t.elapsed_seconds=elapsed(t)
 recent=Timer.query.filter_by(user_id=session['uid'],status='Stopped').order_by(Timer.id.desc()).limit(20).all()
 return render_template('timer.html',clients=Client.query.order_by(Client.name).all(),active=active,recent=recent)
@app.post('/timer/start')
@login_required
def start_timer():
 u=db.session.get(User,session['uid']);c=db.session.get(Client,int(request.form['client_id']));m=db.session.get(Matter,int(request.form['matter_id']))
 rate=m.matter_hourly_rate if m.matter_hourly_rate is not None else c.client_hourly_rate if c.client_hourly_rate is not None else u.default_hourly_rate
 t=Timer(user=u,client=c,matter=m,category=request.form.get('category'),services=request.form['services'].strip(),notes=request.form.get('notes',''),start_time=utcnow(),status='Running',paused_seconds=0,timekeeper_rate=u.default_hourly_rate,client_rate=c.client_hourly_rate,matter_rate=m.matter_hourly_rate,applied_rate=rate or 0,billable=m.billable and request.form.get('billable','1')=='1',invoiced_status='Unbilled');db.session.add(t);db.session.commit();return redirect(url_for('timer'))
def owned(tid):return Timer.query.filter_by(id=tid,user_id=session['uid']).first_or_404()
@app.post('/timer/<int:tid>/pause')
@login_required
def pause_timer(tid):
 t=owned(tid)
 if t.status=='Running':t.status='Paused';t.paused_at=utcnow();db.session.add(Pause(timer=t,paused_at=t.paused_at));db.session.commit()
 return redirect(url_for('timer'))
@app.post('/timer/<int:tid>/resume')
@login_required
def resume_timer(tid):
 t=owned(tid)
 if t.status=='Paused' and t.paused_at:
  p=t.paused_at if t.paused_at.tzinfo else t.paused_at.replace(tzinfo=timezone.utc);sec=max(0,(utcnow()-p).total_seconds());t.paused_seconds=(t.paused_seconds or 0)+sec;t.status='Running';t.paused_at=None;pause=Pause.query.filter_by(timer_id=t.id,resumed_at=None).order_by(Pause.id.desc()).first()
  if pause:pause.resumed_at=utcnow();pause.seconds=sec
  db.session.commit()
 return redirect(url_for('timer'))
@app.post('/timer/<int:tid>/stop')
@login_required
def stop_timer(tid):
 t=owned(tid);at=utcnow();hours=round(elapsed(t,at)/3600,4);billable=float(request.form.get('billable_hours') or hours) if t.billable else 0;t.stop_time=at;t.status='Stopped';t.paused_at=None;t.worked_hours=hours;t.billable_hours=billable;t.amount=round(billable*float(t.applied_rate or 0),2);t.notes=request.form.get('notes',t.notes);db.session.commit();return redirect(url_for('timer'))
@app.get('/entries')
@login_required
def entries():
 q=Timer.query.order_by(Timer.id.desc()) if session['role']=='Administrator' else Timer.query.filter_by(user_id=session['uid']).order_by(Timer.id.desc())
 return render_template('entries.html',rows=q.all())
@app.route('/admin',methods=['GET'])
@login_required
@admin_required
def admin():return render_template('admin.html',users=User.query.order_by(User.display_name).all(),entries=Timer.query.order_by(Timer.id.desc()).all(),clients=Client.query.count(),matters=Matter.query.count())
@app.post('/admin/entry/<int:tid>')
@login_required
@admin_required
def edit_entry(tid):
 t=db.session.get(Timer,tid);f=request.form;t.services=f['services'];t.billable_hours=float(f['billable_hours']);t.applied_rate=float(f['applied_rate']);t.amount=round(t.billable_hours*float(t.applied_rate),2);db.session.commit();return redirect(url_for('admin'))
@app.post('/admin/reload')
@login_required
@admin_required
def reload_data():flash('Master data reloaded.' if import_master() else 'Workbook not found in assets/.');return redirect(url_for('admin'))
@app.route('/invoices/new',methods=['GET','POST'])
@login_required
@admin_required
def invoice_new():
 if request.method=='GET':return render_template('invoice.html',entries=Timer.query.filter_by(status='Stopped',billable=True,invoiced_status='Unbilled').order_by(Timer.id).all())
 f=request.form;ids=[int(x) for x in f.getlist('entry_ids')];selected=Timer.query.filter(Timer.id.in_(ids)).all();net=sum(float(t.amount or 0) for t in selected);vat_rate=float(f.get('vat_rate') or 0);vat=round(net*vat_rate/100,2);inv=Invoice(number=f'MARS-INV-{date.today().year}-{Invoice.query.count()+1:04d}',status='Issued',client_id=selected[0].client_id if selected else None,matter_id=selected[0].matter_id if selected else None,language=f['language'],currency=f.get('currency') or 'EUR',vat_rate=vat_rate,net_total=net,vat_amount=vat,amount_due=net+vat,created_by=session['uid']);db.session.add(inv);db.session.flush()
 for i,t in enumerate(selected,1):db.session.add(InvoiceLine(invoice=inv,sort_order=i,line_type='Hourly fees',description=t.services,quantity=t.billable_hours,rate=t.applied_rate,net_amount=t.amount,source_timer_id=t.id));t.invoiced_status='Invoiced';t.invoice_number=inv.number
 db.session.commit();return render_template('invoice_result.html',invoice=inv,lines=inv.lines)

with app.app_context():bootstrap();app._booted=True
