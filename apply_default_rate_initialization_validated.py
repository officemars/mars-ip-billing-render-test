from pathlib import Path
import py_compile, shutil, sys

p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
if not p.exists(): raise SystemExit(f'Missing {p}')
s=p.read_text(encoding='utf-8')
marker='# ==================== DEFAULT RATE INITIALIZATION FINAL ===================='
if marker in s:
    print('Default-rate patch already installed.'); raise SystemExit(0)
bak=p.with_name(p.name+'.before_default_rate_initialization.bak')
shutil.copy2(p,bak)
patch=r"""

# ==================== DEFAULT RATE INITIALIZATION FINAL ====================
# Default precedence: explicit user/client rate, explicit matter rate,
# client default rate, then user standard rate.
def effective_rate_default_final(user, client, matter):
    try:
        special=UserClientRateV11.query.filter_by(user_id=user.id,client_id=client.id).first()
        if special is not None:
            return float(special.hourly_rate)
    except Exception:
        pass
    if matter is not None and matter.matter_hourly_rate is not None:
        return float(matter.matter_hourly_rate)
    if client is not None and client.client_hourly_rate is not None:
        return float(client.client_hourly_rate)
    return float(user.default_hourly_rate or 0)
effective_rate_v11=effective_rate_default_final

# Existing save functions remain authoritative. These wrappers only fill a rate when
# the administrator left the rate blank; explicit values are never overwritten.
_old_client_rate_save_final=app.view_functions['client_fact_v7']
def client_rate_default_save_final():
    existing_id=request.form.get('id',type=int)
    before_max=db.session.query(db.func.max(Client.id)).scalar() or 0
    result=_old_client_rate_save_final()
    if not (request.form.get('rate') or '').strip():
        client=db.session.get(Client,existing_id) if existing_id else Client.query.filter(Client.id>before_max).order_by(Client.id.desc()).first()
        user=db.session.get(User,session['uid'])
        if client and client.client_hourly_rate is None:
            client.client_hourly_rate=float(user.default_hourly_rate or 0);db.session.commit()
    return result
app.view_functions['client_fact_v7']=login_required(admin_required(client_rate_default_save_final))

_old_matter_rate_save_final=app.view_functions['matter_fact_v7']
def matter_rate_default_save_final():
    existing_id=request.form.get('id',type=int)
    before_max=db.session.query(db.func.max(Matter.id)).scalar() or 0
    result=_old_matter_rate_save_final()
    if not (request.form.get('hourly_rate') or '').strip():
        matter=db.session.get(Matter,existing_id) if existing_id else Matter.query.filter(Matter.id>before_max).order_by(Matter.id.desc()).first()
        user=db.session.get(User,session['uid'])
        if matter and matter.matter_hourly_rate is None:
            client=db.session.get(Client,matter.client_id)
            matter.matter_hourly_rate=float(client.client_hourly_rate if client and client.client_hourly_rate is not None else (user.default_hourly_rate or 0));db.session.commit()
    return result
app.view_functions['matter_fact_v7']=login_required(admin_required(matter_rate_default_save_final))

# Prefill defaults in Data Management for clarity. Existing records retain saved rates.
@app.after_request
def default_rate_form_prefill_final(response):
    if request.endpoint=='master_v4' and response.status_code==200 and 'text/html' in response.headers.get('Content-Type',''):
        html=response.get_data(as_text=True)
        if 'rate-prefill-final' not in html:
            user=db.session.get(User,session.get('uid'))
            standard=float(user.default_hourly_rate or 0) if user else 0
            script=f'''<script id="rate-prefill-final">(function(){{
const userDefault={standard!r};
const cp=window.__clientPayloadFinal||{{}};
const clientRecord=document.getElementById('client-record');
const clientForm=clientRecord&&clientRecord.closest('form');
const clientRate=clientForm&&clientForm.querySelector('[name="rate"]');
function newClientDefault(){{if(clientRecord&&!clientRecord.value&&clientRate&&!clientRate.value)clientRate.value=userDefault;}}
if(clientRecord)clientRecord.addEventListener('change',newClientDefault);newClientDefault();
const matterRecord=document.getElementById('matter-record');
const matterForm=matterRecord&&matterRecord.closest('form');
const matterClient=matterForm&&matterForm.querySelector('[name="client_id"]');
const matterRate=matterForm&&matterForm.querySelector('[name="hourly_rate"]');
function matterDefault(){{if(!matterRecord||matterRecord.value||!matterRate||matterRate.value)return;const row=cp[matterClient.value]||{{}};matterRate.value=(row.rate!==undefined&&row.rate!=='')?row.rate:userDefault;}}
if(matterClient)matterClient.addEventListener('change',matterDefault);
if(matterRecord)matterRecord.addEventListener('change',()=>{{if(!matterRecord.value){{matterRate.value='';matterDefault();}}}});matterDefault();
}})();</script>'''
            html=html.replace('</body>',script+'</body>') if '</body>' in html else html+script
            response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
"""
p.write_text(s.rstrip()+patch+'\n',encoding='utf-8')
try:py_compile.compile(str(p),doraise=True)
except Exception:shutil.copy2(bak,p);raise
print('Default client/matter rate initialization installed.')
print('Backup:',bak)
print('Syntax validation passed.')
