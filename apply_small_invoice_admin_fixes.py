from pathlib import Path
import py_compile, shutil, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
if not p.exists(): raise SystemExit(f'Missing {p}')
s=p.read_text(); marker='# ==================== SMALL ADMIN AND INVOICE UX FIXES ===================='
if marker in s: print('Already installed.'); raise SystemExit(0)
backup=p.with_name(p.name+'.before_small_invoice_admin_fixes.bak');shutil.copy2(p,backup)
patch=r"""\n\n# ==================== SMALL ADMIN AND INVOICE UX FIXES ====================
# Keep existing capabilities; adjust presentation/filtering only.

# 1+3. Consolidate Matter archive into the existing Archive page and hide redundant Users/rates card in Data Management.
@app.after_request
def small_admin_cleanup_final(response):
    if response.status_code==200 and 'text/html' in response.headers.get('Content-Type',''):
        html=response.get_data(as_text=True)
        if request.endpoint=='admin':
            # Remove the separate Archived matters card injected by the earlier matter patch.
            import re as _re
            html=_re.sub(r'<section class="card"><h2>Archived matters</h2><a href="/admin/archive/matters">Open archived matters</a></section>','',html)
            # Put matter archive inside the existing Archive card/link area.
            if 'Open archived matters' not in html:
                html=html.replace('Open Archive</a>','Open Archive</a><br><a href="/admin/archive/matters">Archived matters</a>')
        if request.endpoint=='master_v4':
            import re as _re
            html=_re.sub(r'<section class="card"><a href="[^\"]*users[^\"]*">Users and timekeepers</a></section>','',html)
        response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response

# 2. Invoice context filters time entries by selected matter as soon as matter_id is supplied.
def invoice_context_filter_matter_final(client_id):
    mid=request.args.get('matter_id',type=int)
    matters=[m for m in Matter.query.filter_by(client_id=client_id).order_by(db.func.lower(Matter.name),Matter.id).all() if 'matter_active_final' not in globals() or matter_active_final(m)]
    contacts=contacts_for_context(client_id,mid)
    tq=Timer.query.filter_by(client_id=client_id,status='Stopped',billable=True,invoiced_status='Unbilled')
    if mid: tq=tq.filter(Timer.matter_id==mid)
    timers=tq.order_by(Timer.start_time).all()
    return jsonify({'matters':[{'id':m.id,'reference':m.reference,'name':m.name} for m in matters],
      'contacts':[{'id':c.id,'display_name':c.display_name} for c in contacts],
      'timers':[{'id':t.id,'date':t.start_time.date().isoformat(),'timekeeper':t.user.display_name,'matter':t.matter.name,'services':t.services,'billable_hours':t.billable_hours,'amount':float(t.amount or 0)} for t in timers]})
app.view_functions['invoice_context']=login_required(admin_required(invoice_context_filter_matter_final))

# 4. Portal invoice defaults and editor responsiveness.
@app.after_request
def invoice_portal_ux_final(response):
    if response.status_code==200 and 'text/html' in response.headers.get('Content-Type',''):
        html=response.get_data(as_text=True)
        if request.endpoint in ('edit_fee_note','edit_fee_note_original_v4'):
            # Portal default should agree with generated invoice.
            html=html.replace('value="20"','value="19"').replace('>20%</option>','>19%</option>').replace('>20 %</option>','>19 %</option>')
            # Keep Payment terms; remove duplicate Payment deadline input/label when present.
            import re as _re
            html=_re.sub(r'<label>Payment deadline.*?</label>','',html,flags=_re.S|_re.I)
            # Empty generated sentence should not display in embedded editor.
            html=html.replace('Payment due within  from receipt of this Fee Note.','')
            # Issued/downloaded draft should remain downloadable after edits.
            if 'Download invoice' not in html and 'issue_fee_note' in app.view_functions:
                did=request.view_args.get('draft_id') if request.view_args else None
                if did:
                    button=f'<form method="post" action="{url_for("issue_fee_note",draft_id=did)}"><button>Download invoice</button></form>'
                    html=html.replace('</main>',button+'</main>') if '</main>' in html else html+button
            # Recalculate visible selected total client-side when line checkboxes change.
            script='''<script id="invoice-live-total-final">(function(){function recalc(){let total=0;document.querySelectorAll('input[type=checkbox][name]').forEach(cb=>{if(!cb.checked)return;const row=cb.closest('tr');if(!row)return;const amount=row.querySelector('input[name*=amount],input[name*=total],[data-amount]');if(amount){const v=parseFloat(amount.value||amount.dataset.amount||0);if(Number.isFinite(v))total+=v;}});const target=document.querySelector('[data-selected-total],#selected-total,.selected-total');if(target)target.textContent=total.toFixed(2);}document.addEventListener('change',e=>{if(e.target.matches('input[type=checkbox]'))recalc()});recalc();})();</script>'''
            if 'invoice-live-total-final' not in html: html=html.replace('</body>',script+'</body>') if '</body>' in html else html+script
        response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response

# Ensure included flags and totals are persisted before an invoice is generated.
_old_issue_small_final=app.view_functions.get('issue_fee_note')
def issue_fee_note_selection_final(draft_id):
    draft=db.session.get(FeeNoteDraft,draft_id)
    if draft:
        # Existing editor save endpoints remain authoritative. Recalculate from persisted included=True lines only.
        included=FeeNoteLine.query.filter_by(draft_id=draft_id,included=True).all()
        # Force portal default VAT to 19 for taxable invoices.
        if draft.vat_treatment not in ('Reverse charge','Other exemption'):
            draft.vat_rate=19
            for line in included: line.vat_rate=19
        recalculate_v5(draft);db.session.commit()
    return _old_issue_small_final(draft_id)
app.view_functions['issue_fee_note']=login_required(admin_required(issue_fee_note_selection_final))
"""
p.write_text(s.rstrip()+patch+'\n')
try:py_compile.compile(str(p),doraise=True)
except Exception:shutil.copy2(backup,p);raise
print('Small admin/invoice fixes installed; syntax validation passed.')
