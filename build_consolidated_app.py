from pathlib import Path
import base64, py_compile, shutil, sys

ROOT = Path('.')
required = [ROOT/'app.py', ROOT/'change2.py', ROOT/'change3.py']
for p in required:
    if not p.exists(): raise SystemExit(f'Missing {p}')

backup = ROOT/'pre_consolidation_backup'
backup.mkdir(exist_ok=True)
for p in required:
    shutil.copy2(p, backup/p.name)

base = base64.b64encode((ROOT/'app.py').read_bytes()).decode()
c2 = base64.b64encode((ROOT/'change2.py').read_bytes()).decode()
c3 = base64.b64encode((ROOT/'change3.py').read_bytes()).decode()

patch = r"""
# ==================== CONSOLIDATED FINAL FIXES ====================
# Final endpoint implementations. Earlier behavior remains available, but these are the
# authoritative views for master-data relationships, reset workflow, and selections.

from sqlalchemy.exc import SQLAlchemyError, IntegrityError

LANGUAGES = ('English','French','German')

def alpha_clients(active_only=False):
    rows = Client.query.order_by(db.func.lower(Client.name), Client.id).all()
    if active_only:
        rows = [x for x in rows if fact_data('client',x.id).get('active',True) not in (False,0,'0','false','False','no','No')]
    return rows

def alpha_contacts(active_only=False):
    rows = Contact.query.order_by(db.func.lower(Contact.display_name), Contact.id).all()
    if active_only:
        rows = [x for x in rows if fact_data('contact',x.id).get('active',True) not in (False,0,'0','false','False','no','No')]
    return rows

def alpha_matters():
    return Matter.query.order_by(db.func.lower(Matter.name), Matter.id).all()

def linked_client_ids(contact):
    ids = [r.client_id for r in ContactClientLinkV10.query.filter_by(contact_id=contact.id).all()]
    if contact.client_id and contact.client_id not in ids: ids.append(contact.client_id)
    return sorted(set(ids))

def contacts_for_context(client_id=None, matter_id=None):
    active = alpha_contacts(True)
    if matter_id:
        matter = db.session.get(Matter, matter_id)
        if matter and matter.primary_contact_id:
            selected = [x for x in active if x.id == matter.primary_contact_id]
            if selected: return selected
    if client_id:
        selected = [x for x in active if client_id in linked_client_ids(x)]
        if selected: return selected
    return active

def next_matter_reference():
    row = db.session.get(AppSetting, 'matter_next')
    if row is None:
        used=[]
        for value, in db.session.query(Matter.reference).all():
            text=str(value or '').strip()
            if text.isdigit() and int(text)>=131: used.append(int(text))
        number=max([130]+used)+1
        row=AppSetting(key='matter_next',value=str(number+1));db.session.add(row)
        return str(number)
    number=max(131,int(row.value or 131));row.value=str(number+1);return str(number)

@app.get('/api/contacts-context')
@login_required
def contacts_context_consolidated():
    cid=request.args.get('client_id',type=int);mid=request.args.get('matter_id',type=int)
    return jsonify([{'id':x.id,'display_name':x.display_name,'email':x.email or ''} for x in contacts_for_context(cid,mid)])

def matters_consolidated(client_id):
    return jsonify([{'id':m.id,'reference':m.reference or '','name':m.name} for m in Matter.query.filter_by(client_id=client_id).order_by(db.func.lower(Matter.name),Matter.id).all()])
app.view_functions['matters']=login_required(matters_consolidated)

def invoice_context_consolidated(client_id):
    mid=request.args.get('matter_id',type=int)
    matters=Matter.query.filter_by(client_id=client_id).order_by(db.func.lower(Matter.name),Matter.id).all()
    contacts=contacts_for_context(client_id,mid)
    timers=Timer.query.filter_by(client_id=client_id,status='Stopped',billable=True,invoiced_status='Unbilled').order_by(Timer.start_time).all()
    return jsonify({'matters':[{'id':m.id,'reference':m.reference,'name':m.name} for m in matters],
      'contacts':[{'id':c.id,'display_name':c.display_name} for c in contacts],
      'timers':[{'id':t.id,'date':t.start_time.date().isoformat(),'timekeeper':t.user.display_name,'matter':t.matter.name,'services':t.services,'billable_hours':t.billable_hours,'amount':float(t.amount or 0)} for t in timers]})
app.view_functions['invoice_context']=login_required(admin_required(invoice_context_consolidated))

MASTER_CONSOLIDATED=r'''{% extends "base.html" %}{% block content %}
<h1>Data management</h1><p>Clients, contacts and matters are loaded directly from the database and sorted alphabetically.</p>
<div class="cards"><section class="card"><a href="{{url_for('users_v8')}}">Users and timekeepers</a></section><section class="card"><a href="{{url_for('contact_links_v8')}}">Contact-client links</a></section></div>
<section class="card"><h2>Client Record</h2><form method="post" action="{{url_for('client_fact_v7')}}"><div class="grid">
<label>Existing<select id="client-record" name="id"><option value="">New client</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label>
<label>Name<input name="name" required></label><label>Billing name<input name="billing_name"></label><label>Address<textarea name="address"></textarea></label><label>Country<input name="country"></label><label>VAT number<input name="vat_number"></label>
<label>Language<select name="language">{% for x in languages %}<option>{{x}}</option>{% endfor %}</select></label><label>Currency<input name="currency" value="EUR"></label><label>Payment terms<input name="payment_terms"></label><label>VAT treatment<input name="vat_treatment"></label><label>Billing email<input type="email" name="billing_email"></label><label>Optional client rate<input type="number" step="0.01" name="rate"></label>
<label>Status<select name="active"><option value="1">Active</option><option value="0">Not active / archive</option></select></label><label>Notes (optional)<textarea name="notes"></textarea></label></div><button>Save Client Record</button></form></section>
<section class="card"><h2>Contact Record</h2><form method="post" action="{{url_for('contact_fact_v7')}}"><div class="grid">
<label>Existing<select id="contact-record" name="id"><option value="">New contact</option>{% for x in contacts %}<option value="{{x.id}}">{{x.display_name}} | {{x.email or 'no email'}} | #{{x.id}}</option>{% endfor %}</select></label>
<label>Name<input name="name" required></label><label>Title<input name="title"></label><label>Exact salutation<select name="salutation"><option>Tu</option><option>Vous</option><option>Sie</option><option>Du</option></select></label><label>Preferred language<select name="preferred_language">{% for x in languages %}<option>{{x}}</option>{% endfor %}</select></label><label>Email<input type="email" name="email"></label><label>Phone<input name="phone"></label><label>Address<textarea name="address"></textarea></label><label>Primary linked client<select name="client_id"><option value="">None</option>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label>
<label>Status<select name="active"><option value="1">Active</option><option value="0">Not active / archive</option></select></label><label>Notes (optional)<textarea name="notes"></textarea></label></div><button>Save Contact Record</button></form></section>
<section class="card"><h2>Matter Record</h2><form method="post" action="{{url_for('matter_fact_v7')}}"><div class="grid">
<label>Existing<select id="matter-record" name="id"><option value="">New matter</option>{% for m in matters %}<option value="{{m.id}}">{{m.name}} | {{m.reference or ''}}</option>{% endfor %}</select></label><label>Matter name<input name="name" required></label><label>Nature<input name="nature"></label><label>Reference number<input name="reference" placeholder="Automatically starts at 131"></label>
<label>Language<select name="language">{% for x in languages %}<option>{{x}}</option>{% endfor %}</select></label><label>Linked client<select id="matter-client" name="client_id" required>{% for c in clients %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select></label><label>Linked contact<select id="matter-contact" name="contact_id"><option value="">None</option></select></label><label>Billing method<input name="billing_method"></label><label>Hourly rate<input type="number" step="0.01" name="hourly_rate"></label><label>Daily rate<input type="number" step="0.01" name="daily_rate"></label><label>Currency<input name="currency" value="EUR"></label><label>Payment terms<input name="payment_terms"></label><label>Notes (optional)<textarea name="billing_notes"></textarea></label></div><button>Save Matter Record</button></form></section>
<script>
const clients={{client_payload|tojson}},contacts={{contact_payload|tojson}},matters={{matter_payload|tojson}};
function fill(id,data){const s=document.getElementById(id),f=s.closest('form');s.addEventListener('change',async()=>{const row=data[s.value]||{};for(const el of f.elements){if(!el.name||el.name==='id')continue;let v=row[el.name];if(v===undefined||v===null)v='';el.value=v;}if(id==='matter-record'){await loadContacts(row.client_id,row.contact_id);}})}
async function loadContacts(cid,selected=''){const s=document.getElementById('matter-contact');if(!cid){s.innerHTML='<option value="">None</option>';return;}const d=await(await fetch('/api/contacts-context?client_id='+cid)).json();s.innerHTML='<option value="">None</option>'+d.map(x=>`<option value="${x.id}">${x.display_name}${x.email?' | '+x.email:''}</option>`).join('');s.value=selected||'';}
fill('client-record',clients);fill('contact-record',contacts);fill('matter-record',matters);document.getElementById('matter-client').addEventListener('change',e=>loadContacts(e.target.value));loadContacts(document.getElementById('matter-client').value);
</script>{% endblock %}'''

def payload_client(x):
    f=fact_data('client',x.id);return {'name':x.name or '','billing_name':x.billing_name or '','address':x.address or '','country':x.country or '','vat_number':x.vat_number or '','language':x.language or 'English','currency':x.currency or 'EUR','payment_terms':f.get('payment_terms',''),'vat_treatment':f.get('vat_treatment',''),'billing_email':f.get('billing_email',''),'rate':float(x.client_hourly_rate) if x.client_hourly_rate is not None else '','active':'1' if f.get('active',True) else '0','notes':f.get('notes','')}
def payload_contact(x):
    f=fact_data('contact',x.id);return {'name':x.display_name or '','title':f.get('title',''),'salutation':f.get('salutation','Vous'),'preferred_language':f.get('preferred_language') or x.language or 'English','email':x.email or '','phone':x.phone or '','address':f.get('address',''),'client_id':x.client_id or '','active':'1' if f.get('active',True) else '0','notes':f.get('notes','')}
def payload_matter(x):
    f=fact_data('matter',x.id);return {'name':x.name or '','nature':x.nature or f.get('nature',''),'reference':x.reference or '','language':x.language or 'English','client_id':x.client_id,'contact_id':x.primary_contact_id or '','billing_method':x.billing_mode or f.get('billing_method',''),'hourly_rate':float(x.matter_hourly_rate) if x.matter_hourly_rate is not None else '','daily_rate':float(x.matter_daily_rate) if x.matter_daily_rate is not None else '','currency':x.currency or 'EUR','payment_terms':x.payment_terms or '','billing_notes':x.notes or f.get('billing_notes','')}

def master_consolidated():
    clients=alpha_clients();contacts=alpha_contacts();matters=alpha_matters()
    return render_template_string(MASTER_CONSOLIDATED,clients=clients,contacts=contacts,matters=matters,languages=LANGUAGES,client_payload={str(x.id):payload_client(x) for x in clients},contact_payload={str(x.id):payload_contact(x) for x in contacts},matter_payload={str(x.id):payload_matter(x) for x in matters})
app.view_functions['master_v4']=login_required(admin_required(master_consolidated))

def save_client_consolidated():
    try:
        x=db.session.get(Client,int(request.form['id'])) if request.form.get('id') else Client(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
        x.name=request.form['name'].strip();x.billing_name=request.form.get('billing_name','').strip() or x.name;x.address=request.form.get('address','');x.country=request.form.get('country','');x.vat_number=request.form.get('vat_number','');x.language=request.form.get('language') or 'English';x.currency=request.form.get('currency') or 'EUR';x.client_hourly_rate=fnum(request.form.get('rate'),None);db.session.add(x);db.session.flush();save_fact('client',x.id,{'payment_terms':request.form.get('payment_terms',''),'vat_treatment':request.form.get('vat_treatment',''),'billing_email':request.form.get('billing_email',''),'active':request.form.get('active','1')=='1','notes':request.form.get('notes','')});db.session.commit();flash('Client saved.')
    except Exception: db.session.rollback();app.logger.exception('Client save failed');flash('Client could not be saved. See server log.')
    return redirect(url_for('master_v4'))
app.view_functions['client_fact_v7']=login_required(admin_required(save_client_consolidated))

def save_contact_consolidated():
    try:
        x=db.session.get(Contact,int(request.form['id'])) if request.form.get('id') else Contact(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
        x.display_name=request.form['name'].strip();x.email=(request.form.get('email') or '').strip() or None;x.phone=request.form.get('phone','');x.client_id=request.form.get('client_id',type=int);x.language=request.form.get('preferred_language') or 'English';db.session.add(x);db.session.flush();save_fact('contact',x.id,{'title':request.form.get('title',''),'salutation':request.form.get('salutation',''),'preferred_language':x.language,'address':request.form.get('address',''),'active':request.form.get('active','1')=='1','notes':request.form.get('notes','')});
        if x.client_id and not ContactClientLinkV10.query.filter_by(contact_id=x.id,client_id=x.client_id).first(): db.session.add(ContactClientLinkV10(contact_id=x.id,client_id=x.client_id))
        db.session.commit();flash('Contact saved.')
    except Exception: db.session.rollback();app.logger.exception('Contact save failed');flash('Contact could not be saved. See server log.')
    return redirect(url_for('master_v4'))
app.view_functions['contact_fact_v7']=login_required(admin_required(save_contact_consolidated))

def save_matter_consolidated():
    try:
        new=not request.form.get('id');x=db.session.get(Matter,int(request.form['id'])) if not new else Matter(external_id='USR-'+datetime.now().strftime('%Y%m%d%H%M%S%f'))
        x.name=request.form['name'].strip();x.reference=(request.form.get('reference') or '').strip() or (next_matter_reference() if new else x.reference);x.nature=request.form.get('nature','');x.language=request.form.get('language') or 'English';x.client_id=int(request.form['client_id']);x.primary_contact_id=request.form.get('contact_id',type=int);x.billing_mode=request.form.get('billing_method','');x.matter_hourly_rate=fnum(request.form.get('hourly_rate'),None);x.matter_daily_rate=fnum(request.form.get('daily_rate'),None);x.currency=request.form.get('currency') or 'EUR';x.payment_terms=request.form.get('payment_terms','');x.notes=request.form.get('billing_notes','');x.billable=True;db.session.add(x);db.session.flush();save_fact('matter',x.id,{'linked_client_ids':[x.client_id],'linked_contact_ids':[x.primary_contact_id] if x.primary_contact_id else []});db.session.commit();flash('Matter saved.')
    except Exception: db.session.rollback();app.logger.exception('Matter save failed');flash('Matter could not be saved. See server log.')
    return redirect(url_for('master_v4'))
app.view_functions['matter_fact_v7']=login_required(admin_required(save_matter_consolidated))

RESET_ADMIN=r'''{% extends "base.html" %}{% block content %}<h1>Password reset requests</h1><table><tr><th>Email</th><th>Requested</th><th>Temporary password</th><th></th></tr>{% for r in requests %}<tr><form method="post" action="{{url_for('resolve_reset_consolidated',request_id=r.id)}}"><td>{{r.email}}</td><td>{{r.requested_at}}</td><td><input type="password" name="password" minlength="10" required></td><td><button>Set temporary password and resolve</button></td></form></tr>{% else %}<tr><td colspan="4">No open requests.</td></tr>{% endfor %}</table>{% endblock %}'''
def password_resets_consolidated(): return render_template_string(RESET_ADMIN,requests=PasswordResetRequest.query.filter_by(resolved_at=None).order_by(PasswordResetRequest.requested_at).all())
app.view_functions['password_resets_compat_v15']=login_required(admin_required(password_resets_consolidated))
if 'password_resets_v12' in app.view_functions: app.view_functions['password_resets_v12']=login_required(admin_required(password_resets_consolidated))
@app.post('/admin/password-reset/<int:request_id>/resolve-consolidated')
@login_required
@admin_required
def resolve_reset_consolidated(request_id):
    row=db.session.get(PasswordResetRequest,request_id);password=request.form.get('password','')
    if not row or len(password)<10: flash('Use a temporary password of at least 10 characters.');return redirect(request.referrer or url_for('admin'))
    user=User.query.filter(db.func.lower(User.email)==row.email.lower()).first()
    if not user: flash('User account not found.');return redirect(request.referrer or url_for('admin'))
    user.password_hash=generate_password_hash(password);user.active=True;row.resolved_at=datetime.now(timezone.utc);row.resolved_by=session['uid'];db.session.commit();flash('Temporary password set. Ask the user to sign in and change it immediately.');return redirect(request.referrer or url_for('admin'))

# Keep connection reliability in the consolidated application.
app.config['SQLALCHEMY_ENGINE_OPTIONS']={'pool_pre_ping':True,'pool_recycle':120,'pool_size':5,'max_overflow':5,'pool_timeout':30}
"""

out = f'''# Auto-generated monolithic MARS-IP application.\n# Built from the exact production app.py, change2.py and change3.py.\nimport base64, sys, types\n_BASE=base64.b64decode({base!r}).decode('utf-8')\n_C2=base64.b64decode({c2!r}).decode('utf-8')\n_C3=base64.b64decode({c3!r}).decode('utf-8')\nexec(compile(_BASE,'app-base.py','exec'),globals())\ncore_mod=sys.modules[__name__]\nsys.modules['app']=core_mod\nchange2_mod=types.ModuleType('change2');change2_mod.__dict__['__file__']=str(__file__)\nsys.modules['change2']=change2_mod\nexec(compile(_C2,'change2-embedded.py','exec'),change2_mod.__dict__)\nexec(compile(_C3,'change3-embedded.py','exec'),globals())\n{patch}\n'''

new = ROOT/'app_consolidated.py'
new.write_text(out,encoding='utf-8')
py_compile.compile(str(new),doraise=True)
print('Created app_consolidated.py')
print('Backups are in pre_consolidation_backup/')
print('Syntax compilation passed.')
