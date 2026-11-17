"""MARS-IP update32 - invoice prefill, multilingual fields and full manual lines.
Builds on change31:app. Keeps app.py and all earlier patches unchanged.
Gunicorn entrypoint: update32:app
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import json
from flask import request, session, flash
import app as core
import change31 as previous

app = previous.app

# ---------------- Defaults payload ----------------
def facts(kind, ident):
    try: return core.fact_data(kind, ident) or {}
    except Exception: return {}

def language_name(value):
    value=(value or 'English').strip().lower()
    if value.startswith('fr'): return 'French'
    if value.startswith('de') or value.startswith('ger'): return 'German'
    return 'English'

def client_payload():
    out={}
    for c in core.Client.query.all():
        cf=facts('client',c.id)
        out[str(c.id)]={
            'language':language_name(c.language),
            'payment_terms':(c.payment_terms or cf.get('payment_terms') or ''),
            'country':c.country or '', 'vat_number':c.vat_number or '',
            'billing_name':c.billing_name or c.name, 'client_name':c.name,
            'billing_address':c.address or '', 'currency':c.currency or 'EUR',
            'vat_treatment':c.vat_treatment or cf.get('vat_treatment') or 'Standard VAT'
        }
    return out

def matter_payload():
    out={}
    for m in core.Matter.query.all():
        mf=facts('matter',m.id)
        out[str(m.id)]={
            'client_id':str(m.client_id),
            'language':language_name(m.language) if m.language else '',
            'payment_terms':m.payment_terms or mf.get('payment_terms') or '',
            'engagement_date':mf.get('engagement_letter_date') or mf.get('engagement_date') or '',
            'currency':m.currency or '', 'vat_treatment':m.vat_treatment or mf.get('vat_treatment') or '',
            'matter_text':m.name
        }
    return out

def contact_payload():
    return {str(x.id):{'client_id':str(x.client_id or ''),'name':x.display_name,'language':language_name(x.language) if x.language else ''} for x in core.Contact.query.all()}

# ---------------- Persist authoritative defaults on newly-created draft ----------------
_old_new=app.view_functions['invoice_new']
def invoice_new_update32():
    before={x.id for x in core.FeeNoteDraft.query.with_entities(core.FeeNoteDraft.id).all()} if request.method=='POST' else set()
    response=_old_new()
    if request.method=='POST':
        try:
            created=[x for x in core.FeeNoteDraft.query.all() if x.id not in before]
            d=created[0] if len(created)==1 else None
            if d:
                c=core.db.session.get(core.Client,d.client_id)
                m=core.db.session.get(core.Matter,d.matter_id) if d.matter_id else None
                cf=facts('client',c.id) if c else {}; mf=facts('matter',m.id) if m else {}
                chosen=(request.form.get('language') or '').strip()
                d.language=language_name(chosen or (m.language if m and m.language else None) or (c.language if c else None))
                d.payment_terms=(m.payment_terms if m else None) or mf.get('payment_terms') or (c.payment_terms if c else None) or cf.get('payment_terms') or ''
                d.engagement_date=mf.get('engagement_letter_date') or mf.get('engagement_date') or ''
                d.show_engagement=bool(d.engagement_date)
                if c:
                    d.country=c.country or d.country; d.vat_number=c.vat_number or d.vat_number
                    d.billing_name=c.billing_name or c.name; d.client_name=c.name; d.billing_address=c.address or d.billing_address
                treatment=(m.vat_treatment if m else None) or mf.get('vat_treatment') or (c.vat_treatment if c else None) or cf.get('vat_treatment') or 'Standard VAT'
                d.vat_treatment=treatment
                exempt='reverse' in treatment.lower() or 'exempt' in treatment.lower()
                d.vat_rate=0 if exempt else 19
                d.vat_label={'French':'TVA','German':'MwSt.','English':'VAT'}[d.language]
                if exempt and not d.vat_wording:
                    d.vat_wording={'French':'Reverse charge / exonération de TVA\n(TVA autoliquidée par le preneur, art. 196 de la directive TVA 2006/112/CE)','German':'Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)','English':'Reverse charge'}[d.language]
                if hasattr(core,'recalculate_v5'): core.recalculate_v5(d)
                else: core.recalculate(d)
                core.db.session.commit()
        except Exception:
            core.db.session.rollback();app.logger.exception('update32 draft defaults failed')
    return response
app.view_functions['invoice_new']=invoice_new_update32

# ---------------- Full manual invoice line creates a normal central Timer ----------------
def num(name, default='0'):
    try:return max(Decimal('0'),Decimal((request.form.get(name) or default).replace(',','.')))
    except (InvalidOperation,AttributeError):raise ValueError(name+' is invalid')

_old_add=app.view_functions['invoice_line_add_v18']
def invoice_line_add_update32(draft_id):
    before={x.id for x in core.FeeNoteLine.query.filter_by(draft_id=draft_id).all()}
    response=_old_add(draft_id)
    try:
        created=[x for x in core.FeeNoteLine.query.filter_by(draft_id=draft_id).all() if x.id not in before]
        line=created[0] if len(created)==1 else None
        d=core.db.session.get(core.FeeNoteDraft,draft_id)
        employee_id=request.form.get('employee_id',type=int)
        service_date=(request.form.get('service_date') or '').strip()
        if line and d and employee_id and service_date:
            employee=core.db.session.get(core.User,employee_id)
            start_s=request.form.get('start_time') or '00:00'; end_s=request.form.get('end_time') or ''
            start=datetime.strptime(service_date+' '+start_s,'%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            worked=num('worked_hours',request.form.get('quantity') or '0'); billable=num('billable_hours',request.form.get('quantity') or '0'); rate=num('rate')
            stop=datetime.strptime(service_date+' '+end_s,'%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc) if end_s else start+timedelta(hours=float(worked))
            timer=core.Timer(user_id=employee_id,client_id=d.client_id,matter_id=d.matter_id,category=request.form.get('line_type') or 'Time-based fees',services=(request.form.get('description') or '').strip(),notes=(request.form.get('notes') or '').strip(),start_time=start,stop_time=stop,status='Stopped',worked_hours=float(worked),billable_hours=float(billable),timekeeper_rate=employee.default_hourly_rate if employee else rate,applied_rate=rate,billable=True,amount=(billable*rate))
            core.db.session.add(timer);core.db.session.flush();line.source_timer_id=timer.id;line.timekeeper=employee.display_name if employee else line.timekeeper;line.quantity=billable;line.rate=rate;line.amount=core._v18_money(billable*rate)
            if hasattr(core,'_v18_state'):core._v18_state(timer.id,create=True)
            core._v18_recalc(d);core.db.session.commit()
    except Exception:
        core.db.session.rollback();app.logger.exception('update32 central manual line creation failed');flash('The invoice line was added, but creation of its central time entry failed. Please review it in Billing Control.')
    return response
app.view_functions['invoice_line_add_v18']=invoice_line_add_update32

# ---------------- Immediate browser prefill and extended line form ----------------
@app.after_request
def ui_update32(response):
    if response.status_code!=200 or 'text/html' not in response.content_type or session.get('role')!='Administrator':return response
    html=response.get_data(as_text=True);changed=False
    if request.endpoint in ('invoice_new','edit_fee_note_original_v4','edit_fee_note','invoice_control_edit_v18') and 'marsip-update32-defaults' not in html:
        payload=json.dumps({'clients':client_payload(),'matters':matter_payload(),'contacts':contact_payload()}).replace('<','\\u003c')
        script=r'''<script id="marsip-update32-defaults">(function(){const P=PAYLOAD;
function set(name,val,force){const e=document.querySelector('[name="'+name+'"]');if(!e||val==null||val==='')return;if(!force&&e.value)return;if(e.tagName==='SELECT'){const o=[...e.options].find(x=>x.value.toLowerCase()===String(val).toLowerCase()||x.text.toLowerCase()===String(val).toLowerCase());if(o)e.value=o.value}else e.value=val;e.dispatchEvent(new Event('change',{bubbles:true}));}
function vat(lang,treatment){const exempt=/reverse|exempt/i.test(treatment||'');set('vat_rate',exempt?'0':'19',true);set('vat_label',lang==='French'?'TVA':lang==='German'?'MwSt.':'VAT',true);if(exempt)set('vat_wording',lang==='French'?'Reverse charge / exonération de TVA\n(TVA autoliquidée par le preneur, art. 196 de la directive TVA 2006/112/CE)':lang==='German'?'Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)':'Reverse charge',true);}
function apply(force){const c=document.querySelector('[name="client_id"]'),m=document.querySelector('[name="matter_id"]'),ct=document.querySelector('[name="contact_id"]');const C=c&&P.clients[c.value]||{},M=m&&P.matters[m.value]||{},K=ct&&P.contacts[ct.value]||{};const lang=M.language||C.language||K.language||'English';set('language',lang,force);set('payment_terms',M.payment_terms||C.payment_terms,force);set('engagement_date',M.engagement_date,force);set('engagement_letter_date',M.engagement_date,force);set('country',C.country,force);set('vat_number',C.vat_number,force);set('billing_name',C.billing_name,force);set('client_name',C.client_name,force);set('billing_address',C.billing_address,force);set('currency',M.currency||C.currency,force);set('vat_treatment',M.vat_treatment||C.vat_treatment||'Standard VAT',force);vat(lang,M.vat_treatment||C.vat_treatment||'Standard VAT');}
document.addEventListener('change',e=>{if(e.target.matches('[name="client_id"],[name="matter_id"],[name="contact_id"]'))apply(true);if(e.target.matches('[name="language"],[name="vat_treatment"]'))vat(document.querySelector('[name="language"]')?.value||'English',document.querySelector('[name="vat_treatment"]')?.value||'Standard VAT');});apply(false);})();</script>'''.replace('PAYLOAD',payload)
        html=html.replace('</body>',script+'</body>',1) if '</body>' in html else html+script;changed=True
    if request.endpoint=='invoice_control_edit_v18' and 'marsip-update32-line-fields' not in html:
        marker='<label>Date<input type="date" name="service_date" required></label>'
        extra='''<label>Start time<input type="time" name="start_time"></label><label>End time<input type="time" name="end_time"></label><label>Employee<select name="employee_id" required><option value="">Select employee</option>EMPLOYEES</select></label><label>Worked hours<input type="number" min="0" step="0.0001" name="worked_hours"></label><label>Billable hours<input type="number" min="0" step="0.0001" name="billable_hours"></label>'''
        options=''.join('<option value="%s">%s</option>'%(u.id,u.display_name) for u in core.User.query.order_by(core.User.display_name).all())
        extra=extra.replace('EMPLOYEES',options)
        if marker in html:html=html.replace(marker,marker+extra,1)
        desc='<label>Description<textarea name="description" required></textarea></label>'
        if desc in html:html=html.replace(desc,desc+'<label>Notes<textarea name="notes"></textarea></label>',1)
        html=html.replace('</body>','<script id="marsip-update32-line-fields">(function(){const q=document.querySelector(\'[name="quantity"]\'),b=document.querySelector(\'[name="billable_hours"]\'),w=document.querySelector(\'[name="worked_hours"]\');function x(){if(b&&!b.value)b.value=q.value;if(w&&!w.value)w.value=q.value}if(q)q.addEventListener(\'input\',x);})();</script></body>',1);changed=True
    if changed:response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
