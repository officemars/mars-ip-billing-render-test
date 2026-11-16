"""MARS-IP V29 - invoice defaults and billing-safety patch.
Run with: gunicorn change29:app ...
Builds on change28 and does not modify historical database rows at import time.
"""
from datetime import date, datetime, timezone
import re
from flask import request
import change28 as previous
import app as core

app = previous.app
db = core.db
Client = core.Client
Contact = core.Contact
Matter = core.Matter
Timer = core.Timer
FeeNoteDraft = core.FeeNoteDraft
FeeNoteLine = core.FeeNoteLine


def _text(*values):
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ''


def _facts(kind, entity_id):
    try:
        return core.fact_data(kind, entity_id) if entity_id else {}
    except Exception:
        return {}


def _is_exempt(value):
    value = (value or '').strip().lower()
    return ('reverse' in value or 'exempt' in value or '0%' in value or value == '0')


def _vat_defaults(client, matter):
    cf = _facts('client', client.id if client else None)
    mf = _facts('matter', matter.id if matter else None)
    treatment = _text(
        getattr(matter, 'vat_treatment', None) if matter else None,
        mf.get('vat_treatment'),
        getattr(client, 'vat_treatment', None) if client else None,
        cf.get('vat_treatment'),
        'Standard VAT',
    )
    if _is_exempt(treatment):
        return ('Reverse charge' if 'reverse' in treatment.lower() else 'Other exemption', 0.0)
    return ('Standard VAT', 19.0)


def _apply_master_defaults(draft, overwrite=True):
    client = db.session.get(Client, draft.client_id) if draft.client_id else None
    matter = db.session.get(Matter, draft.matter_id) if draft.matter_id else None
    if not client:
        return
    mf = _facts('matter', matter.id if matter else None)
    cf = _facts('client', client.id)
    contact_id = (getattr(matter, 'primary_contact_id', None) if matter else None) or draft.contact_id
    contact = db.session.get(Contact, contact_id) if contact_id else None
    values = {
        'client_name': client.name,
        'billing_name': client.billing_name or client.name,
        'billing_address': client.address or '',
        'country': client.country or '',
        'vat_number': client.vat_number or '',
        'language': _text(getattr(matter, 'language', None) if matter else None, client.language, 'English'),
        'currency': _text(getattr(matter, 'currency', None) if matter else None, client.currency, 'EUR'),
        'payment_terms': _text(getattr(matter, 'payment_terms', None) if matter else None, mf.get('payment_terms'), client.payment_terms, cf.get('payment_terms')),
        'matter_text': matter.name if matter else (draft.matter_text or ''),
        'contact_name': contact.display_name if contact else (draft.contact_name or ''),
        'engagement_date': _text(mf.get('engagement_letter_date'), mf.get('engagement_date'), mf.get('date_of_engagement_letter')),
    }
    for field, value in values.items():
        if hasattr(draft, field) and value and (overwrite or not _text(getattr(draft, field, None))):
            setattr(draft, field, value)
    if contact:
        draft.contact_id = contact.id
    draft.vat_treatment, draft.vat_rate = _vat_defaults(client, matter)
    for line in FeeNoteLine.query.filter_by(draft_id=draft.id).all():
        line.vat_treatment = draft.vat_treatment
        line.vat_rate = draft.vat_rate


def _recalc(draft):
    fn = getattr(core, 'recalculate_v5', None) or getattr(core, 'recalculate', None)
    if fn:
        fn(draft)


# Create through the proven route, then persist authoritative client/matter defaults.
_original_invoice_new = app.view_functions['invoice_new']
def invoice_new_v29():
    if request.method != 'POST':
        return _original_invoice_new()
    before = db.session.query(db.func.max(FeeNoteDraft.id)).scalar() or 0
    response = _original_invoice_new()
    draft = FeeNoteDraft.query.filter(FeeNoteDraft.id > before).order_by(FeeNoteDraft.id.desc()).first()
    if draft:
        _apply_master_defaults(draft, overwrite=True)
        _recalc(draft)
        db.session.commit()
    return response
app.view_functions['invoice_new'] = invoice_new_v29


# Saving the full invoice header may never reintroduce a 20% rate.
_original_header = app.view_functions.get('update_fee_note')
if _original_header:
    def update_fee_note_v29(draft_id):
        response = _original_header(draft_id)
        draft = db.session.get(FeeNoteDraft, draft_id)
        if draft:
            draft.vat_rate = 0.0 if _is_exempt(draft.vat_treatment) else 19.0
            for line in FeeNoteLine.query.filter_by(draft_id=draft.id).all():
                line.vat_rate = draft.vat_rate
            _recalc(draft)
            db.session.commit()
        return response
    app.view_functions['update_fee_note'] = update_fee_note_v29


# Year is always part of newly assigned invoice numbers: 2026-BER3168.
def next_number_v29(draft):
    if draft.invoice_number:
        return draft.invoice_number
    year = date.today().year
    prefix = str(core.setting('invoice_prefix', 'BER') or 'BER').strip().strip('-')
    # Accept an old configured value such as 2025-BER or MARS-INV-2026 and reduce it to BER.
    if 'BER' in prefix.upper():
        prefix = 'BER'
    start = int(core.setting('invoice_next', '3165'))
    pattern = re.compile(rf'^{year}-{re.escape(prefix)}(\d+)$', re.I)
    used = []
    for value, in db.session.query(FeeNoteDraft.invoice_number).filter(FeeNoteDraft.invoice_number.isnot(None)).all():
        match = pattern.match(str(value or '').strip())
        if match:
            used.append(int(match.group(1)))
    number = max([start - 1] + used) + 1
    core.set_setting('invoice_next', number + 1)
    return f'{year}-{prefix}{number}'
core.next_number = next_number_v29


# Normalize again immediately before Word generation; excluded lines remain unbilled.
_original_issue = app.view_functions.get('issue_fee_note')
if _original_issue:
    def issue_fee_note_v29(draft_id):
        draft = db.session.get(FeeNoteDraft, draft_id)
        if draft:
            _apply_master_defaults(draft, overwrite=False)
            draft.vat_rate = 0.0 if _is_exempt(draft.vat_treatment) else 19.0
            for line in FeeNoteLine.query.filter_by(draft_id=draft.id).all():
                line.vat_rate = draft.vat_rate
            _recalc(draft)
            db.session.commit()
        return _original_issue(draft_id)
    app.view_functions['issue_fee_note'] = issue_fee_note_v29


@app.after_request
def invoice_ui_v29(response):
    if response.status_code != 200 or 'text/html' not in response.headers.get('Content-Type', ''):
        return response
    if request.endpoint not in ('edit_fee_note_original_v4', 'edit_fee_note', 'invoice_control_edit_v18'):
        return response
    html = response.get_data(as_text=True)
    # Same tab only, prevent repeated save/download windows.
    html = html.replace('<form ', '<form target="_self" ', 1) if 'target="_self"' not in html else html
    # Only valid MARS-IP VAT values are offered through the UI.
    html = html.replace('value="20"', 'value="19"').replace('>20<', '>19<').replace('20,0', '19,0')
    script = r'''<script id="marsip-v29-invoice-ui">(function(){
function recalc(){let n=0;document.querySelectorAll('tr').forEach(row=>{const cb=row.querySelector('input[type=checkbox][name*="included"]');if(cb&&!cb.checked)return;const q=row.querySelector('input[name*="quantity"]'),r=row.querySelector('input[name*="rate"]'),a=row.querySelector('input[name*="amount"]');if(a){const calculated=(Number(q&&q.value||0)*Number(r&&r.value||0));if(q&&r&&Number.isFinite(calculated)&&!a.dataset.manual)a.value=calculated.toFixed(2);n+=Number(a.value||0)||0;}});const vat=document.querySelector('input[name="vat_rate"]');if(vat&&Number(vat.value)!==0)vat.value='19';const targets=document.querySelectorAll('[data-selected-total],#selected-total,.selected-total');targets.forEach(x=>x.textContent=n.toFixed(2));}
document.addEventListener('input',e=>{if(e.target.matches('input[name*="amount"]'))e.target.dataset.manual='1';if(e.target.matches('input[name*="quantity"],input[name*="rate"],input[name="vat_rate"]'))recalc();});document.addEventListener('change',e=>{if(e.target.matches('input[type=checkbox],select[name="vat_treatment"]'))recalc();});recalc();
})();</script>'''
    if 'marsip-v29-invoice-ui' not in html:
        html = html.replace('</body>', script + '</body>', 1) if '</body>' in html else html + script
    response.set_data(html)
    response.headers['Content-Length'] = len(response.get_data())
    return response
