"""MARS-IP V28: non-destructive invoice draft prefill.
Import the current working startup module, preserve its routes and templates.
Gunicorn: change28:app
"""
import os
import json
import html
from flask import request, session

# Set MARSIP_BASE_MODULE only if the current startup module differs.
import importlib
base = importlib.import_module(os.environ.get('MARSIP_BASE_MODULE', 'change27'))
app = base.app
core = importlib.import_module('app')

def _get(obj, name):
    return getattr(obj, name, None) if obj is not None else None

def _first(*values):
    return next((str(v) for v in values if v is not None and str(v).strip()), '')

def _facts(kind, ident):
    fn = getattr(core, 'fact_data', None)
    if not fn or ident is None:
        return {}
    try:
        return fn(kind, ident) or {}
    except Exception:
        app.logger.exception('V28 facts unavailable: %s %s', kind, ident)
        return {}

@app.after_request
def prefill_invoice_draft_v28(response):
    if request.method != 'GET' or response.status_code != 200 or 'text/html' not in response.content_type:
        return response
    if session.get('role') != 'Administrator':
        return response
    if request.endpoint not in ('edit_fee_note_original_v4', 'edit_fee_note'):
        return response
    draft_id = (request.view_args or {}).get('draft_id')
    if not draft_id:
        return response
    draft_model = getattr(core, 'FeeNoteDraft', None)
    if draft_model is None:
        return response
    draft = core.db.session.get(draft_model, draft_id)
    if draft is None or _get(draft, 'status') in ('Issued', 'Cancelled'):
        return response
    client = core.db.session.get(core.Client, draft.client_id) if draft.client_id else None
    matter = core.db.session.get(core.Matter, draft.matter_id) if draft.matter_id else None
    contact_id = _get(matter, 'contact_id') or _get(draft, 'contact_id')
    contact = core.db.session.get(core.Contact, contact_id) if contact_id and hasattr(core, 'Contact') else None
    cf = _facts('client', _get(client, 'id'))
    mf = _facts('matter', _get(matter, 'id'))
    payload = {
        'country': _first(_get(client, 'country'), cf.get('country')),
        'contact_name': _first(_get(contact, 'display_name'), _get(contact, 'name'), _get(draft, 'contact_name')),
        'payment_terms': _first(_get(matter, 'payment_terms'), mf.get('payment_terms'), _get(client, 'payment_terms'), cf.get('payment_terms')),
        'engagement_letter_date': _first(_get(matter, 'engagement_letter_date'), mf.get('engagement_letter_date'), mf.get('engagement_date')),
        'billing_name': _first(_get(client, 'billing_name'), _get(client, 'name')),
        'client_name': _first(_get(client, 'name')),
        'billing_address': _first(_get(client, 'address')),
        'vat_number': _first(_get(client, 'vat_number')),
        'currency': _first(_get(matter, 'currency'), _get(client, 'currency')),
        'language': _first(_get(matter, 'language'), _get(client, 'language')),
        'vat_treatment': _first(_get(matter, 'vat_treatment'), mf.get('vat_treatment'), _get(client, 'vat_treatment'), cf.get('vat_treatment')),
    }
    # Only fill blanks in the browser. Existing draft values and manual edits win.
    script = '''<script id="marsip-v28-prefill">(function(){
const data=PAYLOAD;
function fill(){
 const aliases={contact_name:['contact_name','contact_person'],engagement_letter_date:['engagement_letter_date','engagement_date'],client_name:['client_name'],billing_address:['billing_address'],billing_name:['billing_name'],country:['country'],payment_terms:['payment_terms'],vat_number:['vat_number'],currency:['currency'],language:['language'],vat_treatment:['vat_treatment']};
 Object.keys(data).forEach(function(key){
   const val=data[key]; if(!val)return;
   (aliases[key]||[key]).forEach(function(name){
     const el=document.querySelector('[name="'+name+'"]');
     if(!el || el.value.trim() || el.dataset.marsipV28Filled)return;
     if(el.tagName==='SELECT'){
       const option=Array.from(el.options).find(o=>o.value.toLowerCase()===val.toLowerCase()||o.text.toLowerCase()===val.toLowerCase());
       if(!option)return; el.value=option.value;
     }else if(el.type==='date'){
       const m=val.match(/^(\\d{4})-(\\d{2})-(\\d{2})$/); if(!m)return;el.value=val;
     }else{el.value=val;}
     el.dataset.marsipV28Filled='1';
   });
 });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',fill);else fill();
})();</script>'''.replace('PAYLOAD', json.dumps(payload).replace('<', '\\u003c'))
    text = response.get_data(as_text=True)
    if 'marsip-v28-prefill' not in text:
        text = text.replace('</body>', script + '</body>', 1) if '</body>' in text else text + script
        response.set_data(text)
        response.headers['Content-Length'] = len(response.get_data())
    return response
