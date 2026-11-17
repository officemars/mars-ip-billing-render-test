"""MARS-IP change33 - targeted invoice Word output corrections.

Builds on update32:app and leaves app.py and all earlier patches unchanged.
Gunicorn entrypoint: change33:app

Fixes:
- preserves the engagement-letter date entered on the invoice-generation form;
- prints that date in the engagement-letter sentence;
- removes the currency suffix from page-two Rate, Amount and TOTAL cells;
- widens the page-two Date column and prevents date wrapping;
- renders VAT as "VAT (19%)" with the calculated VAT amount opposite.
"""
import io
import re
from datetime import datetime
from decimal import Decimal

from flask import request, Response
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

import app as core
import update32 as previous

app = previous.app


def _valid_date_text(value):
    value = str(value or '').strip()
    if not value:
        return ''
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return ''


def _submitted_engagement_date():
    # The invoice form has used both field names in earlier versions.
    return _valid_date_text(
        request.form.get('engagement_date')
        or request.form.get('engagement_letter_date')
    )


# update32 previously replaced a manually entered engagement date with the Matter
# default. Run after update32 and restore the submitted value as authoritative.
_old_invoice_new_change33 = app.view_functions['invoice_new']

def invoice_new_change33():
    submitted = _submitted_engagement_date() if request.method == 'POST' else ''
    before = ({x.id for x in core.FeeNoteDraft.query.with_entities(core.FeeNoteDraft.id).all()}
              if request.method == 'POST' else set())
    response = _old_invoice_new_change33()
    if request.method == 'POST' and submitted:
        try:
            created = [x for x in core.FeeNoteDraft.query.all() if x.id not in before]
            draft = created[0] if len(created) == 1 else None
            if draft:
                draft.engagement_date = submitted
                draft.show_engagement = True
                core.db.session.commit()
        except Exception:
            core.db.session.rollback()
            app.logger.exception('change33 could not preserve submitted engagement date')
    return response

app.view_functions['invoice_new'] = invoice_new_change33


def _response_bytes(response):
    try:
        return response.get_data()
    except Exception:
        return b''.join(response.response)


def _lang(draft):
    value = (getattr(draft, 'language', None) or 'English').strip().lower()
    if value.startswith('fr'):
        return 'fr'
    if value.startswith('de') or value.startswith('ger'):
        return 'de'
    return 'en'


def _number(value, lang, decimals=2):
    amount = Decimal(str(value or 0))
    text = f'{amount:,.{decimals}f}'
    if lang in ('fr', 'de'):
        text = text.replace(',', 'X').replace('.', ',').replace('X', '.')
    return text


def _money(value, draft, lang):
    return f'{_number(value, lang)} {getattr(draft, "currency", None) or "EUR"}'


def _display_date(value, lang):
    iso = _valid_date_text(value)
    if not iso:
        return ''
    parsed = datetime.strptime(iso, '%Y-%m-%d').date()
    return parsed.strftime('%d.%m.%Y') if lang in ('fr', 'de') else parsed.isoformat()


def _set_cell_text(cell, text, size=11, bold=False):
    cell.text = str(text)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for paragraph in cell.paragraphs:
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        for run in paragraph.runs:
            run.font.size = Pt(size)
            run.bold = bold


def _set_cell_width(cell, width):
    cell.width = width
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn('w:tcW'))
    if tc_w is None:
        tc_w = OxmlElement('w:tcW')
        tc_pr.append(tc_w)
    tc_w.set(qn('w:w'), str(int(width.inches * 1440)))
    tc_w.set(qn('w:type'), 'dxa')


def _no_wrap(cell):
    tc_pr = cell._tc.get_or_add_tcPr()
    if tc_pr.find(qn('w:noWrap')) is None:
        tc_pr.append(OxmlElement('w:noWrap'))


def _is_detail_table(table):
    if not table.rows:
        return False
    heads = [c.text.strip().lower() for c in table.rows[0].cells]
    return (len(heads) >= 5 and
            any(x in heads[0] for x in ('date', 'datum')) and
            any(any(k in x for k in ('timekeeper', 'intervenant', 'bearbeiter')) for x in heads) and
            any(any(k in x for k in ('amount', 'montant', 'betrag')) for x in heads))


def _strip_currency(text, currency):
    currency = re.escape(currency or 'EUR')
    return re.sub(r'\s*' + currency + r'\s*$', '', str(text or ''), flags=re.IGNORECASE).strip()


def _fix_engagement_sentence(document, draft, lang):
    value = _display_date(getattr(draft, 'engagement_date', ''), lang)
    if not value or not getattr(draft, 'show_engagement', True):
        return
    phrases = ('engagement letter dated', "lettre d'engagement", 'lettre de mission', 'mandatsvereinbarung', 'vereinbarung vom')
    for paragraph in document.paragraphs:
        lower = paragraph.text.lower()
        if any(phrase in lower for phrase in phrases):
            text = paragraph.text.rstrip()
            # Replace a trailing blank/underscore or an already printed date.
            text = re.sub(r'(_+|\s)*(\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{4})?\s*$', '', text).rstrip()
            paragraph.text = text + ' ' + value
            for run in paragraph.runs:
                run.font.size = Pt(11)
            return
    # Some templates place the sentence in a table cell.
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                lower = cell.text.lower()
                if any(phrase in lower for phrase in phrases):
                    text = re.sub(r'(_+|\s)*(\d{4}-\d{2}-\d{2}|\d{2}[./]\d{2}[./]\d{4})?\s*$', '', cell.text).rstrip()
                    _set_cell_text(cell, text + ' ' + value, 11)
                    return


def _fix_vat_row(document, draft, lang):
    treatment = (getattr(draft, 'vat_treatment', '') or '').lower()
    reverse = 'reverse' in treatment or 'exempt' in treatment
    rate = Decimal('0') if reverse else Decimal(str(getattr(draft, 'vat_rate', 19) or 19))
    amount = Decimal('0') if reverse else Decimal(str(getattr(draft, 'vat_amount', 0) or 0))
    if lang == 'fr':
        label = 'TVA (0%)' if reverse else f'TVA ({_number(rate, lang, 0)}%)'
    elif lang == 'de':
        label = 'MwSt. (0%)' if reverse else f'MwSt. ({_number(rate, lang, 0)}%)'
    else:
        label = 'VAT (0%)' if reverse else f'VAT ({_number(rate, lang, 0)}%)'
    for table in document.tables:
        for row in table.rows:
            joined = ' '.join(c.text for c in row.cells).strip().lower()
            if (len(row.cells) >= 2 and
                (re.search(r'\bvat\b', joined) or 'tva' in joined or 'mwst' in joined) and
                'number' not in joined and 'ust-nr' not in joined):
                _set_cell_text(row.cells[0], label, 11, True)
                _set_cell_text(row.cells[-1], _money(amount, draft, lang), 11, True)
                return


def _fix_detail_table(document, draft, lang):
    currency = getattr(draft, 'currency', None) or 'EUR'
    for table in document.tables:
        if not _is_detail_table(table):
            continue
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        # 1.12 inches reliably fits ISO dates at 11 pt. The Services column
        # gives up the small amount of width needed, keeping the table on-page.
        widths = [Inches(1.12), Inches(1.42), Inches(2.50), Inches(0.62), Inches(0.88), Inches(1.05)]
        for row in table.rows:
            for index, cell in enumerate(row.cells[:len(widths)]):
                _set_cell_width(cell, widths[index])
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(11)
            if row.cells:
                _no_wrap(row.cells[0])
        # Remove currency only from Rate and Amount cells, including TOTAL.
        for row in table.rows[1:]:
            if len(row.cells) >= 6:
                _set_cell_text(row.cells[4], _strip_currency(row.cells[4].text, currency), 11,
                               bold=any(r.bold for p in row.cells[4].paragraphs for r in p.runs))
                _set_cell_text(row.cells[5], _strip_currency(row.cells[5].text, currency), 11,
                               bold=('total' in ' '.join(c.text for c in row.cells).lower()))
        return


def _correct_invoice_document(document, draft):
    lang = _lang(draft)
    _fix_engagement_sentence(document, draft, lang)
    _fix_vat_row(document, draft, lang)
    _fix_detail_table(document, draft, lang)


_old_issue_change33 = app.view_functions['issue_fee_note']

def issue_fee_note_change33(draft_id):
    draft = core.db.session.get(core.FeeNoteDraft, draft_id)
    response = _old_issue_change33(draft_id)
    content_type = response.headers.get('Content-Type', '') if hasattr(response, 'headers') else ''
    if (not draft or getattr(response, 'status_code', None) != 200 or
            'officedocument.wordprocessingml.document' not in content_type):
        return response
    try:
        document = Document(io.BytesIO(_response_bytes(response)))
        _correct_invoice_document(document, draft)
        output = io.BytesIO()
        document.save(output)
        headers = dict(response.headers)
        headers.pop('Content-Length', None)
        return Response(
            output.getvalue(), status=200, headers=headers,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception:
        app.logger.exception('change33 invoice correction failed; returning change32 output')
        return response

app.view_functions['issue_fee_note'] = issue_fee_note_change33
