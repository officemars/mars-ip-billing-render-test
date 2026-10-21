from pathlib import Path
import py_compile, shutil, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
if not p.exists(): raise SystemExit(f'Missing {p}')
marker='# ==================== MATTER ARCHIVE FINAL PATCH ===================='
s=p.read_text()
if marker in s:
 print('Matter archive patch already installed.'); raise SystemExit(0)
shutil.copy2(p,p.with_suffix('.py.before_matter_archive.bak'))
patch=r"""

# ==================== MATTER ARCHIVE FINAL PATCH ====================
def matter_active_final(matter):
    return fact_data('matter', matter.id).get('active', True) not in (False,0,'0','false','False','no','No')

# Add status to Matter Record and payload/save logic without changing historical records.
MASTER_CONSOLIDATED = MASTER_CONSOLIDATED.replace(
    '<label>Notes (optional)<textarea name="billing_notes"></textarea></label></div><button>Save Matter Record</button>',
    '<label>Status<select name="active"><option value="1">Active</option><option value="0">Not active / archive</option></select></label><label>Notes (optional)<textarea name="billing_notes"></textarea></label></div><button>Save Matter Record</button>'
)
_old_payload_matter_final = payload_matter
def payload_matter(x):
    out=_old_payload_matter_final(x);out['active']='1' if matter_active_final(x) else '0';return out

_old_save_matter_final=save_matter_consolidated
def save_matter_consolidated_final():
    result=_old_save_matter_final()
    # original function has already committed; persist archive state in a second short transaction.
    try:
        mid=request.form.get('id',type=int)
        if mid:
            save_fact('matter',mid,{'active':request.form.get('active','1')=='1'});db.session.commit()
        else:
            newest=Matter.query.order_by(Matter.id.desc()).first()
            if newest:
                save_fact('matter',newest.id,{'active':request.form.get('active','1')=='1'});db.session.commit()
    except Exception:
        db.session.rollback();app.logger.exception('Matter status save failed')
    return result
app.view_functions['matter_fact_v7']=login_required(admin_required(save_matter_consolidated_final))

# Only active matters are available for new timers and invoice/matter selections.
def matters_consolidated_active(client_id):
    rows=Matter.query.filter_by(client_id=client_id).order_by(db.func.lower(Matter.name),Matter.id).all()
    return jsonify([{'id':m.id,'reference':m.reference or '','name':m.name} for m in rows if matter_active_final(m)])
app.view_functions['matters']=login_required(matters_consolidated_active)

_old_invoice_context_final=invoice_context_consolidated
def invoice_context_consolidated_active(client_id):
    response=_old_invoice_context_final(client_id)
    data=response.get_json()
    active_ids={m.id for m in Matter.query.filter_by(client_id=client_id).all() if matter_active_final(m)}
    data['matters']=[m for m in data.get('matters',[]) if m.get('id') in active_ids]
    return jsonify(data)
app.view_functions['invoice_context']=login_required(admin_required(invoice_context_consolidated_active))

MATTER_ARCHIVE_PAGE=r'''{% extends "base.html" %}{% block content %}<h1>Archived matters</h1><p><a href="{{url_for('admin')}}">Back to Administration</a></p><section class="card wide"><table><tr><th>Matter</th><th>Reference</th><th>Client</th><th>Historical entries</th><th></th></tr>{% for m in matters %}<tr><td>{{m.name}}</td><td>{{m.reference or ''}}</td><td>{{client_names.get(m.client_id,'')}}</td><td>{{counts.get(m.id,0)}}</td><td><form method="post" action="{{url_for('unarchive_matter_final',matter_id=m.id)}}"><button>Unarchive</button></form></td></tr>{% else %}<tr><td colspan="5">No archived matters.</td></tr>{% endfor %}</table></section>{% endblock %}'''
@app.get('/admin/archive/matters')
@login_required
@admin_required
def archived_matters_final():
    rows=[m for m in alpha_matters() if not matter_active_final(m)]
    return render_template_string(MATTER_ARCHIVE_PAGE,matters=rows,client_names={c.id:c.name for c in alpha_clients()},counts={m.id:Timer.query.filter_by(matter_id=m.id).count() for m in rows})
@app.post('/admin/archive/matters/<int:matter_id>/unarchive')
@login_required
@admin_required
def unarchive_matter_final(matter_id):
    matter=db.session.get(Matter,matter_id)
    if matter: save_fact('matter',matter.id,{'active':True});db.session.commit();flash('Matter unarchived.')
    return redirect(url_for('archived_matters_final'))

# Add an explicit Matter archive card/link to the Administration response.
@app.after_request
def inject_matter_archive_link_final(response):
    if request.endpoint=='admin' and response.status_code==200 and 'text/html' in response.headers.get('Content-Type',''):
        html=response.get_data(as_text=True)
        if '/admin/archive/matters' not in html:
            card='<section class="card"><h2>Archived matters</h2><a href="/admin/archive/matters">Open archived matters</a></section>'
            html=html.replace('</div><section class="card"><h2>Invoice numbering</h2>',card+'</div><section class="card"><h2>Invoice numbering</h2>')
            response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
"""
p.write_text(s.rstrip()+patch+'\n')
try: py_compile.compile(str(p),doraise=True)
except Exception:
 shutil.copy2(p.with_suffix('.py.before_matter_archive.bak'),p);raise
print('Matter archive patch installed; syntax validation passed.')
