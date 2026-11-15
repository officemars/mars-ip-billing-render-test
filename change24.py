"""MARS-IP V24: administrator full entry editing without changing app.py.
Run with: gunicorn change24:app ...
"""
from datetime import datetime, timezone, timedelta
from flask import request, redirect, url_for, flash, render_template_string, session
import app as core

app=core.app; db=core.db
Timer=core.Timer; User=core.User; Client=core.Client; Matter=core.Matter
FeeNoteLine=core.FeeNoteLine; FeeNoteDraft=core.FeeNoteDraft
login_required=core.login_required; admin_required=core.admin_required

EDIT=r'''{% extends "base.html" %}{% block content %}<h1>Edit time entry</h1><p><a href="{{back}}">Back to Entries</a></p><section class="card wide"><form method="post"><input type="hidden" name="back" value="{{back}}"><div class="grid"><label>Service date<input type="date" name="service_date" value="{{t.start_time.strftime('%Y-%m-%d')}}" required></label><label>Employee<select name="user_id">{% for u in users %}<option value="{{u.id}}" {% if u.id==t.user_id %}selected{% endif %}>{{u.display_name}}</option>{% endfor %}</select></label><label>Worked hours<input type="number" min="0" step="0.0001" name="worked_hours" value="{{t.worked_hours or 0}}" required></label><label>Billable hours<input type="number" min="0" step="0.0001" name="billable_hours" value="{{t.billable_hours or 0}}" required></label><label>Hourly rate<input type="number" min="0" step="0.01" name="rate" value="{{t.applied_rate or 0}}" required></label><label>Amount<input type="number" min="0" step="0.01" name="amount" value="{{t.amount or 0}}" required></label></div><label>Description<textarea name="services" rows="6" required>{{t.services or ''}}</textarea></label><label>Notes<textarea name="notes" rows="5">{{t.notes or ''}}</textarea></label><button>Save changes</button></form></section>{% endblock %}'''

ENTRIES=r'''{% extends "base.html" %}{% block content %}<h1>Entries</h1><section class="card"><form method="get"><div class="grid"><label>Client<select name="client_id"><option value="">All</option>{% for c in clients %}<option value="{{c.id}}" {% if sc==c.id %}selected{% endif %}>{{c.name}}</option>{% endfor %}</select></label><label>Matter<select name="matter_id"><option value="">All</option>{% for m in matters %}<option value="{{m.id}}" {% if sm==m.id %}selected{% endif %}>{{m.reference or ''}} | {{m.name}}</option>{% endfor %}</select></label>{% if session.get('role')=='Administrator' %}<label>Employee<select name="user_id"><option value="">All</option>{% for u in users %}<option value="{{u.id}}" {% if su==u.id %}selected{% endif %}>{{u.display_name}}</option>{% endfor %}</select></label>{% endif %}<label>From<input type="date" name="date_from" value="{{df}}"></label><label>To<input type="date" name="date_to" value="{{dt}}"></label></div><button>Apply filters</button></form></section><section class="cards"><section class="card"><b>Worked</b><p>{{'%.4f'|format(worked)}} h</p></section><section class="card"><b>Billable</b><p>{{'%.4f'|format(billable)}} h</p></section><section class="card"><b>Amount</b><p>{{'%.2f'|format(amount)}}</p></section></section><section class="card wide table-scroll"><table><tr><th>Date</th>{% if session.get('role')=='Administrator' %}<th>Employee</th>{% endif %}<th>Client</th><th>Matter</th><th>Description</th><th>Worked</th><th>Billable</th><th>Rate</th><th>Amount</th><th>Invoice</th>{% if session.get('role')=='Administrator' %}<th>Action</th>{% endif %}</tr>{% for t in rows %}<tr><td>{{t.start_time.date() if t.start_time else ''}}</td>{% if session.get('role')=='Administrator' %}<td>{{un.get(t.user_id,'')}}</td>{% endif %}<td>{{cn.get(t.client_id,'')}}</td><td>{{mn.get(t.matter_id,'')}}</td><td class="svc">{{t.services or ''}}</td><td>{{t.worked_hours or 0}}</td><td>{{t.billable_hours or 0}}</td><td>{{t.applied_rate or 0}}</td><td>{{t.amount or 0}}</td><td>{{t.invoice_number or 'Unbilled'}}</td>{% if session.get('role')=='Administrator' %}<td><a href="{{url_for('admin_entry_edit_v24',timer_id=t.id,back=request.full_path)}}">Edit</a></td>{% endif %}</tr>{% endfor %}</table></section><style>.table-scroll{overflow-x:auto}.table-scroll table{width:max-content;min-width:100%}.svc{min-width:300px;max-width:420px;white-space:pre-wrap}</style>{% endblock %}'''

@app.route('/admin/time-entry/<int:timer_id>/edit-v24',methods=['GET','POST'])
@login_required
@admin_required
def admin_entry_edit_v24(timer_id):
    t=db.session.get(Timer,timer_id)
    if not t:return ('Not found',404)
    back=(request.values.get('back') or url_for('entries_v14'))
    if not back.startswith('/'):back=url_for('entries_v14')
    if request.method=='POST':
        try:
            u=db.session.get(User,int(request.form['user_id']))
            d=datetime.strptime(request.form['service_date'],'%Y-%m-%d').date()
            old=t.start_time or datetime.now(timezone.utc); tm=old.timetz()
            t.start_time=datetime.combine(d,tm)
            if t.start_time.tzinfo is None:t.start_time=t.start_time.replace(tzinfo=timezone.utc)
            t.user_id=u.id;t.timekeeper_rate=u.default_hourly_rate
            t.worked_hours=max(0,float(request.form['worked_hours']));t.billable_hours=max(0,float(request.form['billable_hours']))
            t.applied_rate=max(0,float(request.form['rate']));t.amount=max(0,float(request.form['amount']))
            t.services=request.form['services'].strip();t.notes=request.form.get('notes','').strip()
            if t.stop_time is not None:t.stop_time=t.start_time+timedelta(hours=t.worked_hours)
            drafts=set()
            for line in FeeNoteLine.query.filter_by(source_timer_id=t.id).all():
                draft=db.session.get(FeeNoteDraft,line.draft_id)
                if not draft or draft.status in ('Issued','Cancelled'):continue
                line.service_date=t.start_time.date().isoformat();line.timekeeper=u.display_name;line.description=t.services;line.quantity=t.billable_hours;line.rate=t.applied_rate;line.amount=t.amount
                line.amount_override=abs(float(t.amount)-round(float(t.billable_hours)*float(t.applied_rate),2))>.001;drafts.add(draft.id)
            for did in drafts:
                draft=db.session.get(FeeNoteDraft,did)
                if hasattr(core,'_v18_recalc'):core._v18_recalc(draft)
                elif hasattr(core,'recalculate_v5'):core.recalculate_v5(draft)
            db.session.commit();flash('Entry updated in the central database and editable invoice drafts synchronized.');return redirect(back)
        except Exception as exc:
            db.session.rollback();app.logger.exception('V24 entry update failed');flash('Entry not saved: '+str(exc))
    return render_template_string(EDIT,t=t,users=User.query.order_by(User.display_name).all(),back=back)

def entries_v24():
    q=Timer.query;su=request.args.get('user_id',type=int);sc=request.args.get('client_id',type=int);sm=request.args.get('matter_id',type=int);df=(request.args.get('date_from') or '').strip();dt=(request.args.get('date_to') or '').strip()
    if session.get('role')!='Administrator':q=q.filter(Timer.user_id==session['uid']);su=session['uid']
    elif su:q=q.filter(Timer.user_id==su)
    if sc:q=q.filter(Timer.client_id==sc)
    if sm:q=q.filter(Timer.matter_id==sm)
    try:
        if df:q=q.filter(Timer.start_time>=datetime.strptime(df,'%Y-%m-%d').replace(tzinfo=timezone.utc))
        if dt:q=q.filter(Timer.start_time<datetime.strptime(dt,'%Y-%m-%d').replace(tzinfo=timezone.utc)+timedelta(days=1))
    except ValueError:flash('Use valid dates.')
    rows=q.order_by(Timer.start_time.desc(),Timer.id.desc()).all();users=User.query.order_by(User.display_name).all();clients=Client.query.order_by(Client.name).all();matters=Matter.query.order_by(Matter.name).all()
    return render_template_string(ENTRIES,rows=rows,users=users,clients=clients,matters=matters,su=su,sc=sc,sm=sm,df=df,dt=dt,un={u.id:u.display_name for u in users},cn={c.id:c.name for c in clients},mn={m.id:m.name for m in matters},worked=sum(float(t.worked_hours or 0) for t in rows),billable=sum(float(t.billable_hours or 0) for t in rows),amount=sum(float(t.amount or 0) for t in rows))

app.view_functions['entries_v14']=login_required(entries_v24)
