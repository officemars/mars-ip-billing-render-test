from pathlib import Path
import py_compile, shutil, sys

p = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
if not p.exists():
    raise SystemExit(f'Missing {p}')
source = p.read_text(encoding='utf-8')
marker = '# ==================== MATTER NUMBER + LOCALIZED PAYMENT TERMS FINAL ===================='
if marker in source:
    print('Patch already installed.')
    raise SystemExit(0)
backup = p.with_name(p.name + '.before_matter_number_payment_terms.bak')
shutil.copy2(p, backup)

patch = '''

# ==================== MATTER NUMBER + LOCALIZED PAYMENT TERMS FINAL ====================
PAYMENT_TERMS_BY_LANGUAGE_FINAL = {
    'English': [
        ('8 days', '8 days'),
        ('30 days', '30 days'),
        ('30 days end of month', '30 days end of month'),
    ],
    'German': [
        ('8 days', '8 Tage'),
        ('30 days', '30 Tage'),
        ('30 days end of month', '30 Tage zum Monatsende'),
    ],
    'French': [
        ('8 days', '8 jours'),
        ('30 days', '30 jours'),
        ('30 days end of month', '30 jours fin de mois'),
    ],
}


def matter_reference_suggestion_final():
    numeric_refs = []
    for value, in db.session.query(Matter.reference).all():
        text = str(value or '').strip()
        if text.isdigit():
            numeric_refs.append(int(text))
    setting_row = db.session.get(AppSetting, 'matter_next')
    configured = int(setting_row.value) if setting_row and str(setting_row.value or '').isdigit() else 133
    return str(max([132, configured - 1] + numeric_refs) + 1)


@app.get('/api/matter-reference-suggestion-final')
@login_required
def matter_reference_suggestion_api_final():
    return jsonify({'reference': matter_reference_suggestion_final()})


# UI-only transformations. Existing save handlers, links, archive logic and invoice logic stay unchanged.
MASTER_CONSOLIDATED = MASTER_CONSOLIDATED.replace(
    '<label>Reference number<input id="matter-reference" name="reference" placeholder="133"></label>',
    '<label>Reference number<input id="matter-reference" name="reference" placeholder="133"></label>',
    1,
).replace(
    '<label>Reference number<input name="reference" placeholder="Automatically starts at 131"></label>',
    '<label>Reference number<input id="matter-reference" name="reference" placeholder="133"></label>',
    1,
).replace(
    '<label>Payment terms<input name="payment_terms"></label>',
    '<label>Payment terms<select id="client-payment-terms-final" name="payment_terms"></select></label>',
    1,
)

_PAYMENT_SCRIPT_FINAL = r"""
<script id="matter-payment-localization-final">
(function(){
 const terms = {
  English: [['8 days','8 days'],['30 days','30 days'],['30 days end of month','30 days end of month']],
  German: [['8 days','8 Tage'],['30 days','30 Tage'],['30 days end of month','30 Tage zum Monatsende']],
  French: [['8 days','8 jours'],['30 days','30 jours'],['30 days end of month','30 jours fin de mois']]
 };
 const clientRecord=document.getElementById('client-record');
 const clientForm=clientRecord && clientRecord.closest('form');
 const lang=clientForm && clientForm.querySelector('[name="language"]');
 const payment=document.getElementById('client-payment-terms-final');
 function renderTerms(selected){
   if(!payment || !lang) return;
   const rows=terms[lang.value] || terms.English;
   payment.innerHTML=rows.map(x=>`<option value="${x[0]}">${x[1]}</option>`).join('');
   if(selected && rows.some(x=>x[0]===selected)) payment.value=selected;
 }
 if(clientRecord){
   clientRecord.addEventListener('change',()=>{
     const row=(window.__clientPayloadFinal||{})[clientRecord.value]||{};
     renderTerms(row.payment_terms||'');
   });
 }
 if(lang) lang.addEventListener('change',()=>renderTerms(payment ? payment.value : ''));
 renderTerms('');
 const matterRecord=document.getElementById('matter-record');
 const ref=document.getElementById('matter-reference');
 async function suggest(){
   if(!ref || (matterRecord && matterRecord.value)) return;
   try {
     const r=await fetch('/api/matter-reference-suggestion-final');
     const d=await r.json();
     ref.placeholder=d.reference;
     if(!ref.value) ref.value=d.reference;
   } catch(e) {}
 }
 if(matterRecord) matterRecord.addEventListener('change',()=>{if(!matterRecord.value){ref.value='';suggest();}});
 suggest();
})();
</script>
"""

# Re-render master page with a tiny payload bridge. No database writes happen here.
def master_number_payment_final():
    clients=alpha_clients(); contacts=alpha_contacts(); matters=alpha_matters()
    client_payload={str(x.id):payload_client(x) for x in clients}
    html=render_template_string(
        MASTER_CONSOLIDATED,
        clients=clients, contacts=contacts, matters=matters, languages=LANGUAGES,
        payment_terms=PAYMENT_TERMS_BY_LANGUAGE_FINAL,
        client_payload=client_payload,
        contact_payload={str(x.id):payload_contact(x) for x in contacts},
        matter_payload={str(x.id):payload_matter(x) for x in matters},
    )
    bridge='<script>window.__clientPayloadFinal=' + json.dumps(client_payload, ensure_ascii=False) + ';</script>'
    return html.replace('</body>', bridge + _PAYMENT_SCRIPT_FINAL + '</body>') if '</body>' in html else html + bridge + _PAYMENT_SCRIPT_FINAL
app.view_functions['master_v4']=login_required(admin_required(master_number_payment_final))
'''

p.write_text(source.rstrip() + patch + '\n', encoding='utf-8')
try:
    py_compile.compile(str(p), doraise=True)
except Exception:
    shutil.copy2(backup, p)
    raise
print('Matter-number and localized payment-term patch installed.')
print('Backup:', backup)
print('Syntax validation passed.')
