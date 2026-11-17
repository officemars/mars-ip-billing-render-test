"""MARS-IP change34 - final multilingual invoice output corrections.

Builds on update32:app directly, intentionally bypassing change33's faulty VAT
post-processing. Gunicorn entrypoint: change34:app

Fixes:
- engagement sentence enabled by default for newly created drafts, while remaining untickable;
- engagement date persists from invoice creation/editor form and prints in EN/FR/DE;
- invoice generation/editor exposes Date and Timekeeper inputs by relabelling the
  schedule fields already used by the application;
- selected payment terms are rendered as one clean EN/FR/DE sentence;
- monetary separators are English 2,000.34 and French/German 2.000,34;
- correct VAT row only: VAT/TVA/MwSt. (rate) with calculated amount;
- no duplicated VAT or TOTAL rows;
- detail schedule date remains on one line and EUR is removed from rate/amount cells.
"""
import io
import re
from datetime import datetime
from decimal import Decimal
from flask import request, Response
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
import app as core
import update32 as previous
import change31 as v31

app = previous.app

DATE_FIELDS = ('engagement_date','engagement_letter_date')
SHOW_FIELDS = ('show_engagement','show_engagement_letter','show_engagement_sentence')


def _date(value):
    value=str(value or '').strip()
    for fmt in ('%Y-%m-%d','%d.%m.%Y','%d/%m/%Y','%m/%d/%Y'):
        try:return datetime.strptime(value,fmt).date().isoformat()
        except ValueError:pass
    return ''


def _form_date():
    for name in DATE_FIELDS:
        value=_date(request.form.get(name))
        if value:return value
    return ''


def _has_any(names):
    return any(name in request.form for name in names)


def _set_if_present(obj,names,value):
    for name in names:
        if hasattr(obj,name):
            setattr(obj,name,value); return True
    return False


def _draft_snapshot():
    return {x[0] for x in core.db.session.query(core.FeeNoteDraft.id).all()}


def _latest_new(before):
    return (core.FeeNoteDraft.query.filter(~core.FeeNoteDraft.id.in_(before))
            .order_by(core.FeeNoteDraft.id.desc()).first()) if before else None

# Preserve values submitted during initial invoice generation.
_old_invoice_new=app.view_functions.get('invoice_new')
if _old_invoice_new:
    def invoice_new_v34():
        before=_draft_snapshot() if request.method=='POST' else set()
        entered=_form_date() if request.method=='POST' else ''
        response=_old_invoice_new()
        if request.method=='POST':
            try:
                draft=_latest_new(before)
                if draft:
                    if entered:_set_if_present(draft,DATE_FIELDS,entered)
                    # Creation default is ON unless the form explicitly sends a
                    # checkbox state under one of the established field names.
                    show=True
                    if _has_any(SHOW_FIELDS):
                        show=any(str(request.form.get(n,'')).lower() in ('1','true','on','yes') for n in SHOW_FIELDS)
                    _set_if_present(draft,SHOW_FIELDS,show)
                    core.db.session.commit()
            except Exception:
                core.db.session.rollback();app.logger.exception('V34 creation header save failed')
        return response
    app.view_functions['invoice_new']=invoice_new_v34

# Wrap every known invoice-header editor endpoint. This fixes Save header and
# recalculate without assuming one historical endpoint name.
def _wrap_editor(endpoint):
    old=app.view_functions.get(endpoint)
    if not old:return
    def wrapped(*args,**kwargs):
        response=old(*args,**kwargs)
        if request.method=='POST':
            draft_id=kwargs.get('draft_id') or (args[0] if args else None)
            draft=core.db.session.get(core.FeeNoteDraft,draft_id) if draft_id is not None else None
            if draft:
                try:
                    entered=_form_date()
                    if entered:_set_if_present(draft,DATE_FIELDS,entered)
                    # On the editor, absence of a successful checkbox control
                    # means unticked; presence means ticked.
                    if _has_any(DATE_FIELDS) or _has_any(SHOW_FIELDS):
                        show=any(str(request.form.get(n,'')).lower() in ('1','true','on','yes') for n in SHOW_FIELDS)
                        _set_if_present(draft,SHOW_FIELDS,show)
                    core.db.session.commit()
                except Exception:
                    core.db.session.rollback();app.logger.exception('V34 editor header save failed')
        return response
    wrapped.__name__='v34_'+endpoint
    app.view_functions[endpoint]=wrapped
for _ep in ('edit_fee_note_original_v4','edit_fee_note','invoice_edit','fee_note_edit'):
    _wrap_editor(_ep)

# Default the engagement checkbox in rendered invoice creation/editor forms.
@app.after_request
def invoice_form_defaults_v34(response):
    if response.status_code!=200 or 'text/html' not in response.headers.get('Content-Type',''):
        return response
    if request.endpoint not in ('invoice_new','edit_fee_note_original_v4','edit_fee_note','invoice_edit','fee_note_edit','invoice_control_edit_v18'):
        return response
    html=response.get_data(as_text=True)
    # Add missing Date and Timekeeper controls to the full editor's Add line form.
    # update32 already saves service_date and employee_id; this exposes those inputs.
    if request.endpoint in ('edit_fee_note_original_v4','edit_fee_note','invoice_control_edit_v18') and 'name="employee_id"' not in html:
        users=core.User.query.order_by(core.User.id).all()
        def user_label(u):
            for attr in ('display_name','full_name','username','email','initials'):
                value=getattr(u,attr,None)
                if value:return str(value)
            return 'User '+str(u.id)
        options=''.join('<option value="%s">%s</option>' % (u.id, user_label(u)) for u in users)
        controls='<label>Date<input type="date" name="service_date" required></label><label>Timekeeper<select name="employee_id" required><option value="">Select timekeeper</option>'+options+'</select></label>'
        html=html.replace('<label>Type<select name="line_type"', controls+'<label>Type<select name="line_type"',1)
    html=html.replace('<label>Employee<select name="employee_id"', '<label>Timekeeper<select name="employee_id"')
    # Default ON only on the initial creation form. Editor pages respect the
    # persisted value, so an administrator can untick it and keep it unticked.
    if request.endpoint == 'invoice_new' and request.method == 'GET':
        html=re.sub(r'(<input\b(?=[^>]*type=["\']checkbox["\'])(?=[^>]*name=["\'](?:show_engagement|show_engagement_letter|show_engagement_sentence)["\'])(?![^>]*\bchecked\b)[^>]*)(>)',r'\1 checked\2',html,flags=re.I)
    response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response


def _lang(d):
    x=str(getattr(d,'language','English') or 'English').lower()
    return 'fr' if x.startswith('fr') else ('de' if x.startswith('de') or x.startswith('ger') else 'en')

def _num(v,lang,dec=2):
    s=f'{Decimal(str(v or 0)):,.{dec}f}'
    return s.replace(',','X').replace('.',',').replace('X','.') if lang in ('fr','de') else s

def _money(v,d,lang):return f'{_num(v,lang)} {getattr(d,"currency",None) or "EUR"}'
def _display_date(v,lang):
    iso=_date(v)
    if not iso:return ''
    dt=datetime.strptime(iso,'%Y-%m-%d')
    return dt.strftime('%d.%m.%Y') if lang in ('fr','de') else iso

def _text(cell,text,bold=False):
    cell.text=str(text);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for p in cell.paragraphs:
        p.paragraph_format.space_before=Pt(0);p.paragraph_format.space_after=Pt(0)
        for r in p.runs:r.font.size=Pt(11);r.bold=bold

def _width(cell,w):
    cell.width=w;pr=cell._tc.get_or_add_tcPr();node=pr.find(qn('w:tcW'))
    if node is None:node=OxmlElement('w:tcW');pr.append(node)
    node.set(qn('w:w'),str(int(w.inches*1440)));node.set(qn('w:type'),'dxa')
def _nowrap(cell):
    pr=cell._tc.get_or_add_tcPr()
    if pr.find(qn('w:noWrap')) is None:pr.append(OxmlElement('w:noWrap'))


def _payment_term_key(value):
    """Normalize values used by the form and older Matter/Client records."""
    x=re.sub(r'\s+',' ',str(value or '').strip().lower())
    compact=re.sub(r'[^a-z0-9]+','',x)
    if ('end of month' in x or 'fin de mois' in x or 'monatsende' in x or
            compact in ('30dayseom','30daysendofmonth','30joursfindemois','30tagetomonatsende')):
        return '30_eom'
    if re.search(r'\b30\b',x) or compact in ('30days','30jours','30tage'):
        return '30'
    if re.search(r'\b8\b',x) or 'eight day' in x or 'huitaine' in x or compact in ('8days','eightdays','8jours','8tage'):
        return '8'
    return x


def _payment_sentence(d,lang):
    key=_payment_term_key(getattr(d,'payment_terms',''))
    wording={
        'en':{'8':'Payment due within 8 days.','30':'Payment due within 30 days.',
              '30_eom':'Payment due within 30 days from the end of the month.'},
        'fr':{'8':'Paiement sous huitaine.','30':'Paiement sous 30 jours.',
              '30_eom':'Paiement à 30 jours fin de mois.'},
        'de':{'8':'Zahlbar innerhalb von 8 Tagen.','30':'Zahlbar innerhalb von 30 Tagen.',
              '30_eom':'Zahlbar innerhalb von 30 Tagen zum Monatsende.'},
    }
    return wording.get(lang,wording['en']).get(key,'')


def _payment_terms(doc,d,lang):
    sentence=_payment_sentence(d,lang)
    if not sentence:return
    paragraphs=list(doc.paragraphs)+[p for t in doc.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    for p in paragraphs:
        normalized=re.sub(r'\s+',' ',p.text.replace('\u00a0',' ')).strip().lower()
        is_placeholder=('payment due date' in normalized or
                        ('paiement sous' in normalized and ('[' in normalized or '/' in normalized)) or
                        ('paiement à' in normalized and ('[' in normalized or '/' in normalized)) or
                        ('payment due within' in normalized and ('[' in normalized or '/' in normalized)) or
                        ('zahlbar innerhalb' in normalized and ('[' in normalized or '/' in normalized)))
        if is_placeholder:
            # The selected term is the whole output. Template alternatives and
            # surrounding placeholder instructions are deliberately discarded.
            p.text=sentence
            for r in p.runs:r.font.size=Pt(11);r.bold=True
            return

def _decimal_from_printed(value):
    """Read either 2,000.34 or 2.000,34 without confusing thousands/decimals."""
    x=str(value).replace('\u00a0','').replace(' ','')
    if ',' in x and '.' in x:
        decimal_sep=',' if x.rfind(',')>x.rfind('.') else '.'
        thousands='.' if decimal_sep==',' else ','
        x=x.replace(thousands,'').replace(decimal_sep,'.')
    elif ',' in x:
        tail=x.rsplit(',',1)[1]
        x=x.replace(',','.') if len(tail)==2 else x.replace(',','')
    elif '.' in x:
        tail=x.rsplit('.',1)[1]
        if len(tail)!=2:x=x.replace('.','')
    try:return Decimal(x)
    except Exception:return None


def _localized_amounts(doc,d,lang):
    """Apply locale separators to every amount that carries the invoice currency."""
    currency=getattr(d,'currency',None) or 'EUR'
    rx=re.compile(r'(?<![\d.,])(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2}))\s*'+re.escape(currency)+r'\b',re.I)
    paragraphs=list(doc.paragraphs)+[p for t in doc.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    for p in paragraphs:
        original=p.text
        def repl(match):
            amount=_decimal_from_printed(match.group(1))
            return match.group(0) if amount is None else f'{_num(amount,lang)} {currency}'
        changed=rx.sub(repl,original)
        if changed!=original:
            p.text=changed
            for r in p.runs:r.font.size=Pt(11)

def _engagement(doc,d,lang):
    show=next((bool(getattr(d,n)) for n in SHOW_FIELDS if hasattr(d,n)),True)
    if not show:return
    value=next((_display_date(getattr(d,n),lang) for n in DATE_FIELDS if hasattr(d,n) and _display_date(getattr(d,n),lang)), '')
    if not value:return
    keys=('engagement letter','lettre de mission',"lettre d'engagement",'mandatsvereinbarung','vereinbarung')
    paragraphs=list(doc.paragraphs)+[p for t in doc.tables for row in t.rows for c in row.cells for p in c.paragraphs]
    for p in paragraphs:
        if any(k in p.text.lower() for k in keys):
            base=re.sub(r'(_+|\s)*(\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{4})?\s*$','',p.text).rstrip()
            p.text=base+' '+value
            for r in p.runs:r.font.size=Pt(11)
            return

def _vat(doc,d,lang):
    rate=Decimal(str(getattr(d,'vat_rate',19) or 19))
    amount=Decimal(str(getattr(d,'vat_amount',0) or 0))
    # If an older record omitted vat_amount but has net/gross totals, recover it.
    if amount == 0:
        gross=Decimal(str(getattr(d,'gross_total',0) or 0))
        net=Decimal(str(getattr(d,'net_total',0) or 0))
        if gross > net:
            amount=gross-net
        elif net and rate:
            amount=(net*rate/Decimal('100')).quantize(Decimal('0.01'))
    word={'en':'VAT','fr':'TVA','de':'MwSt.'}[lang]
    label=f'{word} ({_num(rate,lang,0)}%)'
    matches=[]
    for table in doc.tables:
        for row in table.rows:
            if len(row.cells)<2:continue
            left=re.sub(r'\s+',' ',row.cells[0].text).strip().lower()
            right=re.sub(r'\s+',' ',row.cells[-1].text).strip().lower()
            # Match the invoice tax row, including French templates containing
            # VAT [Or] Exonération de TVA, but never the VAT-number header.
            tax_words=any(x in left for x in ('vat','tva','mwst','umsatzsteuer','autoliquid','reverse charge'))
            tax_values=bool(re.search(r'(^|\s)\d+(?:[.,]\d+)?\s*%',right))
            excluded=any(x in left for x in ('vat number','n° de tva','numéro de tva','ust-nr'))
            if tax_words and tax_values and not excluded: matches.append((table,row))
    if not matches:return
    table,row=matches[-1]
    _text(row.cells[0],label,True)
    _text(row.cells[-1],_money(amount,d,lang),True)
    # Delete any additional tax rows created by a template/older patch.
    for other_table,other_row in matches[:-1]:
        if other_row is not row: other_table._tbl.remove(other_row._tr)

def _details(doc,d):
    currency=getattr(d,'currency',None) or 'EUR'
    for table in doc.tables:
        if not table.rows:continue
        h=[c.text.strip().lower() for c in table.rows[0].cells]
        if len(h)<6 or not any(x in h[0] for x in ('date','datum')):continue
        widths=[Inches(1.12),Inches(1.42),Inches(2.50),Inches(.62),Inches(.88),Inches(1.05)]
        for row in table.rows:
            for i,c in enumerate(row.cells[:6]):_width(c,widths[i])
            _nowrap(row.cells[0])
        # Keep exactly one TOTAL row in the schedule.
        total_rows=[r for r in list(table.rows[1:]) if re.search(r'^(total|gesamt|summe)', r.cells[0].text.strip(), re.I)]
        for duplicate in total_rows[1:]:
            table._tbl.remove(duplicate._tr)
        for row in table.rows[1:]:
            if len(row.cells)>=6:
                for i in (4,5):
                    value=re.sub(r'\s*'+re.escape(currency)+r'\s*$','',row.cells[i].text,flags=re.I).strip()
                    _text(row.cells[i],value,'total' in ' '.join(c.text for c in row.cells).lower())
        return

def _correct(doc,d):
    lang=_lang(d)
    _engagement(doc,d,lang)
    _payment_terms(doc,d,lang)
    _localized_amounts(doc,d,lang)
    _vat(doc,d,lang)
    _details(doc,d)

# Bypass change31's broad VAT matcher, which can overwrite the fees row.
# Use the authoritative pre-localization generator, then apply the precise V34 corrections.
_old_issue=getattr(v31, '_old_issue', app.view_functions.get('issue_fee_note'))
if _old_issue:
    def issue_fee_note_v34(draft_id):
        d=core.db.session.get(core.FeeNoteDraft,draft_id);response=_old_issue(draft_id)
        ctype=response.headers.get('Content-Type','') if hasattr(response,'headers') else ''
        if not d or getattr(response,'status_code',None)!=200 or 'officedocument.wordprocessingml.document' not in ctype:return response
        try:
            doc=Document(io.BytesIO(response.get_data()));_correct(doc,d);out=io.BytesIO();doc.save(out)
            headers=dict(response.headers);headers.pop('Content-Length',None)
            return Response(out.getvalue(),status=200,headers=headers,mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        except Exception:
            app.logger.exception('V34 document correction failed');return response
    app.view_functions['issue_fee_note']=issue_fee_note_v34
