"""MARS-IP change31 - final reviewed invoice localization patch.
Builds on change30:app. Keeps change30 and app.py unchanged.

Implemented:
- Matter engagement-letter date registration and invoice prefill
- Client language default (French/German/English), editable in draft
- Standard VAT 19%; Reverse charge/Other exemption 0%
- Localized generated Word values and labels
- Visible VAT amount in Word invoice
- Payment due date calculated from payment terms
- Second-page activity table font 11 pt
- Second-page activity table adds localized Rate/Taux/Stundensatz column
"""
import io, json, re
from datetime import datetime, date, timedelta
from calendar import monthrange
from decimal import Decimal
from flask import request, session, flash, Response
from docx import Document
from docx.shared import Pt
import app as core
import change30 as previous

app = previous.app

# ---------- Matter engagement date ----------
_old_matter_save = app.view_functions['matter_fact_v7']
def matter_save_v31():
    new_record = not request.form.get('id')
    before = {x.id for x in core.Matter.query.with_entities(core.Matter.id).all()} if new_record else set()
    response = _old_matter_save()
    raw = (request.form.get('engagement_letter_date') or '').strip()
    if raw:
        try:
            datetime.strptime(raw, '%Y-%m-%d')
            matter_id = request.form.get('id', type=int)
            if not matter_id:
                client_id = request.form.get('client_id', type=int)
                name = (request.form.get('name') or '').strip()
                created = [x for x in core.Matter.query.filter_by(client_id=client_id, name=name).all() if x.id not in before]
                matter_id = created[0].id if len(created) == 1 else None
            if matter_id:
                facts = core.fact_data('matter', matter_id) or {}
                facts['engagement_letter_date'] = raw
                core.save_fact('matter', matter_id, facts)
                core.db.session.commit()
        except Exception:
            core.db.session.rollback()
            app.logger.exception('change31 engagement-date save failed')
            flash('The matter was saved, but the engagement date could not be saved.')
    return response
app.view_functions['matter_fact_v7'] = matter_save_v31

_old_payload = core.payload_matter
def payload_matter_v31(matter):
    out = _old_payload(matter)
    out['engagement_letter_date'] = (core.fact_data('matter', matter.id) or {}).get('engagement_letter_date', '')
    return out
core.payload_matter = payload_matter_v31

_old_master = app.view_functions['master_v4']
def master_v31():
    marker = '<label>Notes (optional)<textarea name="billing_notes"></textarea></label>'
    field = '<label>Engagement letter date<input type="date" name="engagement_letter_date"></label>'
    if marker in core.MASTER_CONSOLIDATED and 'name="engagement_letter_date"' not in core.MASTER_CONSOLIDATED:
        core.MASTER_CONSOLIDATED = core.MASTER_CONSOLIDATED.replace(marker, field + marker, 1)
    return _old_master()
app.view_functions['master_v4'] = master_v31

# ---------- New invoice defaults ----------
_old_invoice_new = app.view_functions['invoice_new']
def invoice_new_v31():
    before = {x.id for x in core.FeeNoteDraft.query.with_entities(core.FeeNoteDraft.id).all()} if request.method == 'POST' else set()
    response = _old_invoice_new()
    if request.method == 'POST':
        try:
            created = [x for x in core.FeeNoteDraft.query.all() if x.id not in before]
            draft = created[0] if len(created) == 1 else None
            if draft:
                client = core.db.session.get(core.Client, draft.client_id)
                matter = core.db.session.get(core.Matter, draft.matter_id) if draft.matter_id else None
                if client:
                    draft.language = (client.language or 'English').strip()
                if matter:
                    facts = core.fact_data('matter', matter.id) or {}
                    engagement = facts.get('engagement_letter_date', '')
                    if engagement:
                        draft.engagement_date = engagement
                        draft.show_engagement = True
                core.db.session.commit()
        except Exception:
            core.db.session.rollback()
            app.logger.exception('change31 invoice defaults failed')
    return response
app.view_functions['invoice_new'] = invoice_new_v31

# ---------- Localization helpers ----------
def lang_of(draft):
    language = (draft.language or 'English').strip().lower()
    return 'fr' if language.startswith('fr') else 'de' if language.startswith('ger') or language.startswith('de') else 'en'

def parse_date(value):
    value = str(value or '').strip()
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%m/%d/%Y'):
        try: return datetime.strptime(value, fmt).date()
        except ValueError: pass
    return date.today()

def date_text(value, lang):
    d = parse_date(value)
    return d.strftime('%d.%m.%Y') if lang in ('fr','de') else d.strftime('%Y-%m-%d')

def number_text(value, lang, decimals=2):
    text = f'{float(value or 0):,.{decimals}f}'
    if lang in ('fr','de'):
        text = text.replace(',', 'X').replace('.', ',').replace('X', '.')
    return text

def money_text(value, draft, lang):
    return f'{number_text(value, lang, 2)} {draft.currency or "EUR"}'

def due_date(draft):
    start = parse_date(draft.document_date)
    term = (draft.payment_terms or '').lower()
    days = 30
    match = re.search(r'(\d+)', term)
    if match: days = int(match.group(1))
    target = start + timedelta(days=days)
    if 'end of month' in term or 'fin de mois' in term or 'monatsende' in term:
        target = target.replace(day=monthrange(target.year, target.month)[1])
    return target

def set_cell_text(cell, text, size=11, bold=False):
    cell.text = str(text)
    for p in cell.paragraphs:
        for r in p.runs:
            r.font.size = Pt(size); r.bold = bold

def set_table_font(table, size=11):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs: r.font.size = Pt(size)

def replace_text(document, old, new):
    if not old: return
    replacements = {old: new}
    core.replace_all(document, replacements)

def localize_word_document(document, draft):
    lang = lang_of(draft)
    # Localize dates wherever the raw draft dates appear.
    replace_text(document, str(draft.document_date or ''), date_text(draft.document_date, lang))
    replace_text(document, str(draft.engagement_date or ''), date_text(draft.engagement_date, lang))

    reverse = draft.vat_treatment in ('Reverse charge', 'Other exemption')
    vat_rate = 0 if reverse else 19
    vat_amount = 0 if reverse else float(draft.vat_amount or 0)
    labels = {
      'fr': {'rate':'Taux', 'amount':'Montant HT', 'vat':('Reverse charge / exonération de TVA\n(TVA autoliquidée par le preneur, art. 196 de la directive TVA 2006/112/CE)' if reverse else 'TVA 19 %'), 'due':'Date limite de paiement'},
      'de': {'rate':'Stundensatz', 'amount':'Betrag netto', 'vat':('Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)' if reverse else 'MwSt. 19 %'), 'due':'Zahlungsfrist'},
      'en': {'rate':'Rate', 'amount':'Amount net', 'vat':('Reverse charge' if reverse else 'VAT 19%'), 'due':'Payment due'},
    }[lang]

    # Ensure a VAT row contains both the label/statement and the actual amount.
    for table in document.tables:
        for row in table.rows:
            joined = ' '.join(c.text for c in row.cells).lower()
            if any(x in joined for x in ('tva', 'mwst', 'vat ', 'autoliquid', 'reverse charge')) and len(row.cells) >= 2:
                set_cell_text(row.cells[0], labels['vat'], 11)
                set_cell_text(row.cells[-1], money_text(vat_amount, draft, lang), 11)

    # Activity schedule: add Rate before Amount, populate values, use 11 pt.
    detail = core.find_detail_table(document)
    lines = [x for x in sorted(draft.fee_lines, key=lambda x:(x.sort_order,x.id)) if x.included and x.include_in_detail]
    if detail:
        if len(detail.columns) == 5:
            detail.add_column(Pt(65))
        header = detail.rows[0]
        set_cell_text(header.cells[4], labels['rate'], 11, True)
        set_cell_text(header.cells[5], labels['amount'], 11, True)
        total_row = next((r for r in detail.rows if 'total' in ' '.join(c.text for c in r.cells).lower() or 'gesamt' in ' '.join(c.text for c in r.cells).lower()), detail.rows[-1])
        body = [r for r in detail.rows[1:] if r is not total_row and any(c.text.strip() for c in r.cells)]
        for row, line in zip(body, lines):
            set_cell_text(row.cells[0], date_text(line.service_date, lang), 11)
            set_cell_text(row.cells[3], number_text(line.quantity, lang, 2), 11)
            set_cell_text(row.cells[4], money_text(line.rate, draft, lang), 11)
            set_cell_text(row.cells[5], money_text(line.amount, draft, lang), 11)
        set_cell_text(total_row.cells[4], '', 11)
        set_cell_text(total_row.cells[5], money_text(sum(float(x.amount or 0) for x in lines), draft, lang), 11, True)
        set_table_font(detail, 11)
        # Page-two nearby headings also use 11 pt.
        for p in document.paragraphs:
            txt = p.text.lower()
            if any(x in txt for x in ('détail des activités','detail der tätigkeiten','activity detail','note d’honoraires','rechnung')):
                for r in p.runs: r.font.size = Pt(11)

    # Add an explicit payment deadline after the existing payment-terms paragraph.
    due = date_text(due_date(draft), lang)
    sentence = f"{labels['due']} : {due}"
    candidates = {'fr':('paiement','jours'), 'de':('zahlung','tage'), 'en':('payment','days')}[lang]
    target = next((p for p in document.paragraphs if candidates[0] in p.text.lower() and candidates[1] in p.text.lower()), None)
    if target and sentence not in target.text:
        target.add_run('\n' + sentence).bold = True


def response_bytes(response):
    try: return response.get_data()
    except Exception: return b''.join(response.response)

# Post-process the already-generated Word response. The existing issue route remains
# authoritative for numbering, status, links, audit and database commits.
_old_issue = app.view_functions['issue_fee_note']
def issue_fee_note_v31(draft_id):
    draft = core.db.session.get(core.FeeNoteDraft, draft_id)
    if draft:
        reverse = draft.vat_treatment in ('Reverse charge','Other exemption')
        draft.vat_rate = 0 if reverse else 19
        for line in draft.fee_lines: line.vat_rate = draft.vat_rate
        core.recalculate_v5(draft) if hasattr(core,'recalculate_v5') else core.recalculate(draft)
        core.db.session.commit()
    response = _old_issue(draft_id)
    content_type = response.headers.get('Content-Type','') if hasattr(response,'headers') else ''
    if not draft or response.status_code != 200 or 'officedocument.wordprocessingml.document' not in content_type:
        return response
    try:
        document = Document(io.BytesIO(response_bytes(response)))
        localize_word_document(document, draft)
        out = io.BytesIO(); document.save(out); data = out.getvalue()
        headers = dict(response.headers)
        headers.pop('Content-Length', None)
        return Response(data, status=200, headers=headers, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    except Exception:
        app.logger.exception('change31 Word localization failed; returning original generated invoice')
        return response
app.view_functions['issue_fee_note'] = issue_fee_note_v31

# ---------- Browser behaviour ----------
@app.after_request
def change31_ui(response):
    if response.status_code != 200 or 'text/html' not in response.content_type or session.get('role') != 'Administrator':
        return response
    html = response.get_data(as_text=True); changed = False
    if request.endpoint == 'master_v4' and 'marsip-change31-engagement' not in html:
        payload = {str(m.id):(core.fact_data('matter',m.id) or {}).get('engagement_letter_date','') for m in core.Matter.query.all()}
        script = '''<script id="marsip-change31-engagement">document.addEventListener('DOMContentLoaded',()=>{const s=document.getElementById('matter-record'),f=s&&s.closest('form'),d=f&&f.querySelector('[name="engagement_letter_date"]'),p=PAYLOAD;if(!s||!d)return;function x(){d.value=p[s.value]||''}s.addEventListener('change',x);x();});</script>'''.replace('PAYLOAD',json.dumps(payload))
        html=html.replace('</body>',script+'</body>',1);changed=True
    if request.endpoint in ('edit_fee_note_original_v4','edit_fee_note','invoice_control_edit_v18') and 'marsip-change31-vat' not in html:
        script='''<script id="marsip-change31-vat">(()=>{const s=document.querySelector('select[name="vat_treatment"]'),r=document.querySelector('input[name="vat_rate"]');if(!s||!r)return;function x(){r.value=/reverse|exempt/i.test(s.value)?'0':'19'}s.addEventListener('change',x);})();</script>'''
        html=html.replace('</body>',script+'</body>',1);changed=True
    if changed:
        response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
