import io
import re
from copy import deepcopy
from datetime import datetime, date, timezone
from decimal import Decimal, ROUND_HALF_UP

from flask import request, Response, jsonify, render_template_string, redirect, url_for, flash, session, send_file
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Emu

import app as core
import change34 as previous
import change25 as entry_editor

app = previous.app
db = core.db
login_required = core.login_required
admin_required = core.admin_required

VERSION = 'v35'

# =============================================================================
# Pure helpers (no database) - also used by the offline test harness
# =============================================================================

def lang_code(language):
    x = str(language or 'English').strip().lower()
    if x.startswith('fr'):
        return 'fr'
    if x.startswith('de') or x.startswith('ger'):
        return 'de'
    return 'en'


def language_name(language):
    return {'fr': 'French', 'de': 'German', 'en': 'English'}[lang_code(language)]


def D(value, default='0'):
    try:
        return Decimal(str(value if value not in (None, '') else default).replace(',', '.'))
    except Exception:
        return Decimal(default)


def money2(value):
    return D(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def fmt_num(value, lang, decimals=2):
    text = f'{money2(value) if decimals == 2 else D(value):,.{decimals}f}'
    if lang in ('fr', 'de'):
        text = text.replace(',', 'X').replace('.', ',').replace('X', '.')
    return text


def fmt_money(value, lang, currency='EUR'):
    currency = currency or 'EUR'
    if lang == 'en':
        return f'{currency} {fmt_num(value, lang)}'
    return f'{fmt_num(value, lang)} {currency}'


def fmt_hours(value, lang):
    q = D(value)
    text = f'{q.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):.2f}'
    return text.replace('.', ',') if lang in ('fr', 'de') else text


def parse_date(value):
    value = str(value or '').strip()
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(value[:19] if 'T' in value or ' ' in value else value, fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return None


def fmt_date(value, lang):
    d = parse_date(value)
    if not d:
        return ''
    return d.strftime('%d.%m.%Y')


def is_exempt(treatment):
    x = str(treatment or '').lower()
    return bool(re.search(r'reverse|exempt|exon|auto ?liq|befrei|steuerschuld|\b0\s*%', x)) or x.strip() == '0'


def canonical_vat_treatment(treatment):
    x = str(treatment or '').lower()
    if 'reverse' in x or 'autoliq' in x or 'auto liq' in x or 'steuerschuld' in x or 'preneur' in x:
        return 'Reverse charge'
    if is_exempt(x):
        return 'Other exemption'
    return 'Standard VAT'


def payment_key(value):
    x = re.sub(r'\s+', ' ', str(value or '').strip().lower())
    compact = re.sub(r'[^a-z0-9]+', '', x)
    if not x:
        return '30'
    if 'end of month' in x or 'fin de mois' in x or 'monatsende' in x or compact in ('30dayseom', '30eom'):
        return '30_eom'
    if re.search(r'\b30\b', x):
        return '30'
    if re.search(r'\b8\b', x) or 'eight' in x or 'huit' in x or 'acht' in x:
        return '8'
    return x  # free text


TEXTS = {
    'en': {
        'draft': 'DRAFT',
        'time': 'Time-based fees excluding VAT\n(according to the attached schedule of services and time entries)',
        'monitoring': 'Annual trademark monitoring according to the attached details',
        'flat': 'Flat fee',
        'costs': 'Costs / Disbursements',
        'vat': 'VAT ({rate} %)',
        'reverse': 'Reverse charge\n(VAT payable by the recipient, Article 196 of VAT Directive 2006/112/EC)',
        'exempt': 'VAT exemption',
        'pay': {'8': 'Payment due within 8 days from receipt of this Fee Note.',
                '30': 'Payment due within 30 days from receipt of this Fee Note.',
                '30_eom': 'Payment due within 30 days end of month from receipt of this Fee Note.',
                'other': 'Payment due within {text} from receipt of this Fee Note.'},
        'details': 'Details of activities',
        'details_monitoring': 'Details of monitoring services',
        'rate': 'Rate',
        'matter_prefix': 'Matter: ',
        'vat_prefix': 'Your VAT number: ',
        'engagement_prefix': 'According to our Engagement Letter dated ',
    },
    'fr': {
        'draft': 'BROUILLON',
        'time': 'Heures facturées HT\n(selon le tableau des prestations et temps passés ci-joint)',
        'monitoring': 'Surveillance annuelle de marque selon le détail joint',
        'flat': 'Honoraires forfaitaires',
        'costs': 'Frais / Débours',
        'vat': 'TVA ({rate} %)',
        'reverse': 'Reverse charge / exonération de TVA\n(TVA autoliquidée par le preneur, art. 196 de la directive TVA 2006/112/CE)',
        'exempt': 'Exonération de TVA',
        'pay': {'8': "Paiement sous huitaine à compter de la date de réception de la présente note d'honoraires.",
                '30': "Paiement sous 30 jours à compter de la date de réception de la présente note d'honoraires.",
                '30_eom': "Paiement à 30 jours fin de mois à compter de la date de réception de la présente note d'honoraires.",
                'other': "Paiement sous {text} à compter de la date de réception de la présente note d'honoraires."},
        'details': 'Détail des activités',
        'details_monitoring': 'Détail des surveillances',
        'rate': 'Taux horaire',
        'matter_prefix': 'Affaire : ',
        'vat_prefix': 'Votre n° de TVA intracommunautaire : ',
        'engagement_prefix': 'Selon notre accord du ',
    },
    'de': {
        'draft': 'ENTWURF',
        'time': 'Nach Zeitaufwand abgerechnete Honorare netto\n(gemäß der beigefügten Aufstellung der Leistungen und Zeiten)',
        'monitoring': 'Jährliche Markenüberwachung gemäß beigefügter Aufstellung',
        'flat': 'Pauschalhonorar',
        'costs': 'Kosten / Auslagen',
        'vat': 'MwSt. ({rate} %)',
        'reverse': 'Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge, Art. 196 MwStSystRL)',
        'exempt': 'Umsatzsteuerbefreiung',
        'pay': {'8': 'Zahlbar innerhalb von 8 Tagen ab Rechnungseingang.',
                '30': 'Zahlbar innerhalb von 30 Tagen ab Rechnungseingang.',
                '30_eom': 'Zahlbar innerhalb von 30 Tagen zum Monatsende ab Rechnungseingang.',
                'other': 'Zahlbar innerhalb von {text} ab Rechnungseingang.'},
        'details': 'Details der Tätigkeiten',
        'details_monitoring': 'Details der Überwachungen',
        'rate': 'Stundensatz',
        'matter_prefix': 'Projekt: ',
        'vat_prefix': 'Ihre USt-IdNr.: ',
        'engagement_prefix': 'Gemäß unserer Vereinbarung vom ',
    },
}


def payment_sentence(terms, lang):
    key = payment_key(terms)
    table = TEXTS[lang]['pay']
    if key in table:
        return table[key]
    return table['other'].format(text=str(terms).strip())


def line_category(line_type, description=''):
    t = str(line_type or '')
    value = (t + ' ' + str(description or '')).lower()
    if t == 'Time-based fees':
        return 'time'
    if t == 'Annual trademark monitoring' or 'monitor' in value or 'surveillance' in value or 'überwachung' in value:
        return 'monitoring'
    if t in ('Costs', 'Disbursement') or 'disbursement' in value or 'débours' in value or 'auslagen' in value:
        return 'costs'
    return 'flat'


# ----------------------------------------------------------------------------- docx utilities

def _set_paragraph_text(paragraph, text, bold=None, size=None, left=False):
    """Replace the paragraph text, keeping the first run's formatting. '\n' -> line break."""
    if left:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    runs = paragraph.runs
    run = runs[0] if runs else paragraph.add_run()
    for extra in runs[1:]:
        extra._r.getparent().remove(extra._r)
    run.text = ''
    parts = str(text if text is not None else '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    for index, part in enumerate(parts):
        if index:
            run.add_break()
        run.add_text(part)
    if bold is not None:
        run.bold = bold
    if size is not None:
        run.font.size = Pt(size)


def _remove_paragraph(paragraph):
    element = paragraph._element
    element.getparent().remove(element)


def _set_cell(cell, text, bold=None, size=11, align_right=False):
    paragraphs = cell.paragraphs
    first = paragraphs[0]
    for extra in paragraphs[1:]:
        _remove_paragraph(extra)
    _set_paragraph_text(first, text, bold=bold, size=size)
    first.paragraph_format.space_before = Pt(0)
    first.paragraph_format.space_after = Pt(0)
    if align_right:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        first.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _cell_width(cell, emu):
    cell.width = Emu(emu)
    tc_pr = cell._tc.get_or_add_tcPr()
    node = tc_pr.find(qn('w:tcW'))
    if node is None:
        node = OxmlElement('w:tcW')
        tc_pr.append(node)
    node.set(qn('w:w'), str(int(emu / 635)))  # EMU -> twips
    node.set(qn('w:type'), 'dxa')


def _no_wrap(cell):
    tc_pr = cell._tc.get_or_add_tcPr()
    if tc_pr.find(qn('w:noWrap')) is None:
        tc_pr.append(OxmlElement('w:noWrap'))


def _row_text(row):
    return ' '.join(c.text for c in row.cells).replace('\xa0', ' ').strip().lower()


def _table_total_width(table):
    grid = table._tbl.find(qn('w:tblGrid'))
    total = 0
    if grid is not None:
        for col in grid.findall(qn('w:gridCol')):
            try:
                total += int(col.get(qn('w:w')) or 0)
            except ValueError:
                pass
    return total * 635 if total else int(6.5 * 914400)


def _insert_row_copy(table, template_row, before_row):
    new_tr = deepcopy(template_row._tr)
    before_row._tr.addprevious(new_tr)
    from docx.table import _Row
    return _Row(new_tr, table)


def _font_size_table(table, size=11):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(size)


def _all_paragraphs(document):
    out = list(document.paragraphs)
    for t in document.tables:
        for row in t.rows:
            for c in row.cells:
                out.extend(c.paragraphs)
    return out


# ----------------------------------------------------------------------------- document builder

def compute_totals(data):
    """Totals from included lines; mirrors the application's recalculation."""
    lines = [l for l in data['lines'] if l.get('included', True)]
    net = sum((money2(l.get('amount')) for l in lines), Decimal('0'))
    telecom = money2(data.get('telecom_amount')) if data.get('show_telecom') else Decimal('0')
    net += telecom
    exempt = is_exempt(data.get('vat_treatment'))
    rate = Decimal('0') if exempt else D(data.get('vat_rate'), '19')
    if not exempt and rate == 0:
        rate = Decimal('19')
    vat = money2(net * rate / Decimal('100')) if not exempt else Decimal('0')
    return {'net': money2(net), 'vat': vat, 'gross': money2(net + vat), 'rate': rate, 'exempt': exempt, 'telecom': telecom}


def build_invoice_document(template_path, data):
    """Build the Word invoice from `data` (plain dict). Returns (bytes, totals)."""
    lang = lang_code(data.get('language'))
    T = TEXTS[lang]
    currency = data.get('currency') or 'EUR'
    number = data.get('number') or T['draft']
    totals = compute_totals(data)
    document = Document(str(template_path))

    # ---- header paragraphs -------------------------------------------------
    address_lines = [x.strip() for x in re.split(r'\r\n|\r|\n', str(data.get('billing_address') or '')) if x.strip()]
    if len(address_lines) == 1 and ',' in address_lines[0]:
        address_lines = [x.strip() for x in address_lines[0].split(',') if x.strip()]
    address_text = '\n'.join(address_lines)
    contact = str(data.get('contact') or '').strip()
    matter_text = str(data.get('matter_text') or '').strip()
    client_name = str(data.get('client_name') or '').strip()
    engagement = fmt_date(data.get('engagement_date'), lang) if data.get('show_engagement', True) else ''
    vat_number = str(data.get('vat_number') or '').strip() if data.get('show_vat_number', True) else ''
    document_date = fmt_date(data.get('document_date'), lang) or date.today().strftime('%d.%m.%Y')
    place = str(data.get('place') or 'Berlin').strip()

    for p in list(document.paragraphs):
        raw = p.text
        text = raw.replace('\xa0', ' ')
        low = text.lower()
        if '[final sequential document number' in low:
            _set_paragraph_text(p, re.sub(r'\[final sequential document number[^\]]*\]', number, text))
        elif '[clientid]' in low:
            _set_paragraph_text(p, data.get('billing_name') or client_name, left=True)
        elif '[billing address]' in low:
            if address_text:
                _set_paragraph_text(p, address_text, left=True)
            else:
                _remove_paragraph(p)
        elif '[country]' in low:
            country = str(data.get('country') or '').strip()
            if country:
                _set_paragraph_text(p, country, left=True)
            else:
                _remove_paragraph(p)
        elif '[contact person' in low:
            if contact:
                _set_paragraph_text(p, contact, left=True)
            else:
                _remove_paragraph(p)
        elif '[place of issue' in low:
            _set_paragraph_text(p, f'{place}, {document_date}')
        elif '[vat number client]' in low:
            if vat_number:
                _set_paragraph_text(p, T['vat_prefix'] + vat_number)
            else:
                _remove_paragraph(p)
        elif '[client name]' in low or '[matter]' in low:
            _set_paragraph_text(p, T['matter_prefix'] + (client_name + (' | ' + matter_text if matter_text else '')))
        elif '[date of the engagement' in low:
            if engagement:
                _set_paragraph_text(p, T['engagement_prefix'] + engagement)
            else:
                _remove_paragraph(p)
        elif '[payment due date' in low or 'payment due within' in low or 'paiement sous' in low or 'zahlbar innerhalb' in low:
            _set_paragraph_text(p, payment_sentence(data.get('payment_terms'), lang), bold=True)
        elif re.match(r'^(details|détail|detail)\s*\[', low):
            cats = {line_category(l.get('line_type'), l.get('description')) for l in data['lines'] if l.get('included', True)}
            _set_paragraph_text(p, T['details_monitoring'] if cats == {'monitoring'} else T['details'])
        elif low.strip() in ('and/or', 'und/oder', 'et/ou'):
            _remove_paragraph(p)
        elif '[free data entry]' in low:
            annex = str(data.get('annex_text') or '').strip()
            if annex:
                _set_paragraph_text(p, annex)
            else:
                _remove_paragraph(p)

    # ---- summary table -------------------------------------------------------
    summary = None
    for table in document.tables:
        if any(k in _row_text(row) for row in table.rows for k in ('total net', 'montant h.t', 'montant ht', 'nettobetrag')):
            summary = table
            break
    if summary is not None:
        rows = list(summary.rows)
        net_index = next(i for i, r in enumerate(rows) if any(k in _row_text(r) for k in ('total net', 'montant h.t', 'montant ht', 'nettobetrag')))
        telecom_index = next((i for i in range(1, net_index) if any(k in _row_text(rows[i]) for k in ('telecom', 'télécom', 'telekom'))), None)
        template_row = rows[1]
        anchor = rows[telecom_index] if telecom_index is not None else rows[net_index]
        # remove every optional fee row between header and telecom/net
        for i in range(1, telecom_index if telecom_index is not None else net_index):
            summary._tbl.remove(rows[i]._tr)
        grouped = {}
        for l in data['lines']:
            if not l.get('included', True):
                continue
            cat = line_category(l.get('line_type'), l.get('description'))
            grouped[cat] = grouped.get(cat, Decimal('0')) + money2(l.get('amount'))
        for cat in ('time', 'monitoring', 'flat', 'costs'):
            if cat in grouped and grouped[cat] != 0:
                row = _insert_row_copy(summary, template_row, anchor)
                _set_cell(row.cells[0], T[cat])
                _set_cell(row.cells[-1], fmt_money(grouped[cat], lang, currency))
        if telecom_index is not None:
            telecom_row = rows[telecom_index]
            if data.get('show_telecom') and totals['telecom'] != 0:
                if data.get('telecom_description'):
                    _set_cell(telecom_row.cells[0], data['telecom_description'])
                _set_cell(telecom_row.cells[-1], fmt_money(totals['telecom'], lang, currency))
            else:
                summary._tbl.remove(telecom_row._tr)
        rows = list(summary.rows)
        net_index = next(i for i, r in enumerate(rows) if any(k in _row_text(r) for k in ('total net', 'montant h.t', 'montant ht', 'nettobetrag')))
        _set_cell(rows[net_index].cells[-1], fmt_money(totals['net'], lang, currency), bold=True)
        vat_row = rows[net_index + 1] if len(rows) > net_index + 1 else None
        gross_row = rows[net_index + 2] if len(rows) > net_index + 2 else None
        if vat_row is not None:
            if totals['exempt']:
                treatment = canonical_vat_treatment(data.get('vat_treatment'))
                label = data.get('vat_wording') or (T['reverse'] if treatment == 'Reverse charge' else T['exempt'])
            else:
                label = T['vat'].format(rate=fmt_num(totals['rate'], lang, 0) if totals['rate'] == totals['rate'].to_integral_value() else fmt_num(totals['rate'], lang, 2))
            _set_cell(vat_row.cells[0], label)
            _set_cell(vat_row.cells[-1], fmt_money(totals['vat'], lang, currency))
        if gross_row is not None:
            _set_cell(gross_row.cells[-1], fmt_money(totals['gross'], lang, currency), bold=True)
        # any further rows after gross (template leftovers) are removed
        for extra in list(summary.rows)[net_index + 3:]:
            summary._tbl.remove(extra._tr)
        for row in summary.rows:
            label_cell = row.cells[0]
            while len(label_cell.paragraphs) > 1 and not label_cell.paragraphs[0].text.strip():
                _remove_paragraph(label_cell.paragraphs[0])
            if len(row.cells) >= 2:
                for cell in row.cells[1:]:
                    for p in cell.paragraphs:
                        from docx.enum.text import WD_ALIGN_PARAGRAPH
                        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # ---- detail schedule ---------------------------------------------------
    detail = None
    for table in document.tables:
        if table.rows and len(table.rows[0].cells) >= 5:
            header = _row_text(table.rows[0])
            if ('date' in header or 'datum' in header) and any(k in header for k in ('timekeeper', 'intervenant', 'bearbeiter')):
                detail = table
                break
    detail_lines = [l for l in data['lines'] if l.get('included', True) and (l.get('include_in_detail', True) or l.get('line_type') == 'Time-based fees')]
    if detail is not None and data.get('show_time_schedule', True) and detail_lines:
        total_width = _table_total_width(detail)
        rows = list(detail.rows)
        header_row = rows[0]
        total_row = next((r for r in rows[1:] if re.match(r'^(total|gesamt|summe)', r.cells[0].text.strip(), re.I)), rows[-1])
        body_template = next((r for r in rows[1:] if r._tr is not total_row._tr), total_row)
        template_tr = deepcopy(body_template._tr)
        for r in rows[1:]:
            if r._tr is not total_row._tr:
                detail._tbl.remove(r._tr)
        # add the Rate column (position 4) if the template has five columns
        if len(header_row.cells) == 5:
            grid = detail._tbl.find(qn('w:tblGrid'))
            cols = grid.findall(qn('w:gridCol')) if grid is not None else []
            if len(cols) >= 5:
                new_col = deepcopy(cols[3])
                cols[3].addnext(new_col)
            for tr in [header_row._tr, template_tr]:
                tcs = tr.findall(qn('w:tc'))
                if len(tcs) == 5:
                    new_tc = deepcopy(tcs[3])
                    tcs[3].addnext(new_tc)
        from docx.table import _Row
        header_row = _Row(header_row._tr, detail)
        # The template TOTAL row uses merged cells; rebuild it from the body row
        # so that it always has one cell per column.
        total_label = total_row.cells[0].text.strip() or 'TOTAL'
        new_total_tr = deepcopy(template_tr)
        total_row._tr.addprevious(new_total_tr)
        detail._tbl.remove(total_row._tr)
        total_row = _Row(new_total_tr, detail)
        header_labels = [c.text.strip() for c in header_row.cells]
        header_labels[4] = T['rate']
        for i, label in enumerate(header_labels):
            _set_cell(header_row.cells[i], label, bold=True)
        sum_hours = Decimal('0')
        sum_amount = Decimal('0')
        for l in sorted(detail_lines, key=lambda x: (parse_date(x.get('service_date')) or date.min, x.get('sort_order', 0), x.get('id', 0))):
            new_tr = deepcopy(template_tr)
            total_row._tr.addprevious(new_tr)
            row = _Row(new_tr, detail)
            qty = D(l.get('quantity'))
            amount = money2(l.get('amount'))
            sum_hours += qty
            sum_amount += amount
            values = [fmt_date(l.get('service_date'), lang), str(l.get('timekeeper') or ''), str(l.get('description') or ''),
                      fmt_hours(qty, lang), fmt_num(l.get('rate'), lang), fmt_num(amount, lang)]
            for i, v in enumerate(values):
                _set_cell(row.cells[i], v, align_right=(i >= 3))
            _no_wrap(row.cells[0])
        total_values = [total_label, '', '', fmt_hours(sum_hours, lang), '', fmt_num(sum_amount, lang)]
        for i, v in enumerate(total_values):
            _set_cell(total_row.cells[i], v, bold=True, align_right=(i >= 3))
        shares = [0.125, 0.185, 0.395, 0.085, 0.10, 0.11]
        grid = detail._tbl.find(qn('w:tblGrid'))
        if grid is not None:
            for i, col in enumerate(grid.findall(qn('w:gridCol'))[:6]):
                col.set(qn('w:w'), str(int(total_width * shares[i] / 635)))
        tbl_pr = detail._tbl.tblPr
        tbl_w = tbl_pr.find(qn('w:tblW')) if tbl_pr is not None else None
        if tbl_w is not None:
            tbl_w.set(qn('w:w'), str(int(total_width / 635)))
            tbl_w.set(qn('w:type'), 'dxa')
        for row in detail.rows:
            for i, cell in enumerate(row.cells[:6]):
                _cell_width(cell, int(total_width * shares[i]))
        _font_size_table(detail, 11)
    elif detail is not None:
        # no schedule wanted: drop the whole page-two block (title paragraph stays harmless)
        detail._tbl.getparent().remove(detail._tbl)

    output = io.BytesIO()
    document.save(output)
    return output.getvalue(), totals


# =============================================================================
# Database-facing helpers
# =============================================================================

def facts(kind, ident):
    try:
        return core.fact_data(kind, ident) or {}
    except Exception:
        return {}


def applicable_rate(user, client, matter):
    """Agreed hierarchy: timekeeper rate by default, client rate overrides, matter rate overrides."""
    if matter is not None and matter.matter_hourly_rate is not None and float(matter.matter_hourly_rate) > 0:
        return float(matter.matter_hourly_rate)
    if client is not None and client.client_hourly_rate is not None and float(client.client_hourly_rate) > 0:
        return float(client.client_hourly_rate)
    try:
        special = core.UserClientRateV11.query.filter_by(user_id=user.id, client_id=client.id).first() if (user and client) else None
        if special is not None and float(special.hourly_rate or 0) > 0:
            return float(special.hourly_rate)
    except Exception:
        pass
    return float(user.default_hourly_rate or 0) if user else 0.0


def issued_invoice_numbers(timer_id):
    """Numbers of ISSUED (not cancelled) invoices that contain this entry."""
    numbers = []
    try:
        for link in core.TimerInvoiceLinkV18.query.filter_by(timer_id=timer_id).all():
            d = db.session.get(core.FeeNoteDraft, link.draft_id) if link.draft_id else None
            if d is None or d.status == 'Issued':
                if link.invoice_number and link.invoice_number not in numbers:
                    numbers.append(link.invoice_number)
    except Exception:
        pass
    for line in core.FeeNoteLine.query.filter_by(source_timer_id=timer_id).all():
        d = db.session.get(core.FeeNoteDraft, line.draft_id)
        if d and d.status == 'Issued' and d.invoice_number and d.invoice_number not in numbers:
            numbers.append(d.invoice_number)
    return numbers


def open_draft_ids(timer_id):
    out = []
    for line in core.FeeNoteLine.query.filter_by(source_timer_id=timer_id).all():
        d = db.session.get(core.FeeNoteDraft, line.draft_id)
        if d and d.status == 'Draft' and d.id not in out:
            out.append(d.id)
    return out


def entry_state(t):
    """Classify one timer for the invoice picker."""
    active = core._v18_active(t.id) if hasattr(core, '_v18_active') else True
    issued = issued_invoice_numbers(t.id)
    drafts = open_draft_ids(t.id)
    if t.status in ('Running', 'Paused'):
        return 'running', 'still running / paused - stop it first', False, False
    if not active:
        return 'inactive', 'deactivated in Billing Control', False, True
    if issued:
        return 'issued', 'already on issued invoice ' + ', '.join(issued), False, False
    if not t.billable:
        return 'nonbillable', 'marked not billable', False, True
    if drafts:
        return 'draft', 'already in draft #' + ', #'.join(str(x) for x in drafts), False, True
    if t.invoiced_status == 'Invoiced' or (t.invoice_number or '').strip():
        return 'stale', 'was flagged "Invoiced" but no issued invoice contains it - will be billed now', True, True
    return 'open', 'unbilled', True, True


def client_entries(client_id, matter_id=None):
    q = core.Timer.query.filter_by(client_id=client_id)
    if matter_id:
        q = q.filter(core.Timer.matter_id == matter_id)
    out = []
    for t in q.order_by(core.Timer.start_time, core.Timer.id).all():
        state, note, checked, selectable = entry_state(t)
        rate = float(t.applied_rate or 0)
        hours = float(t.billable_hours or 0)
        amount = float(t.amount or 0)
        if state in ('open', 'stale') and rate <= 0:
            rate = applicable_rate(t.user, t.client, t.matter)
            amount = round(hours * rate, 2)
            note = (note + '; ' if note != 'unbilled' else '') + 'rate 0 on entry - hierarchy rate %.2f will be applied' % rate
        out.append({'id': t.id, 'date': t.start_time.date().isoformat() if t.start_time else '', 'timekeeper': t.user.display_name if t.user else '',
                    'matter': t.matter.name if t.matter else '', 'matter_id': t.matter_id, 'services': t.services or '', 'hours': hours,
                    'rate': rate, 'amount': amount, 'state': state, 'note': note, 'checked': checked, 'selectable': selectable})
    return out


# =============================================================================
# New-invoice page
# =============================================================================

NEW_INVOICE_PAGE = r'''{% extends "base.html" %}{% block content %}
<h1>Create fee note</h1>
<section class="card"><form method="post" id="new-invoice-form">
<div class="grid">
<label>Client<select id="client" name="client_id" required><option value="">Select</option>{% for c in clients %}<option value="{{c.id}}" {% if c.id==selected_client %}selected{% endif %}>{{c.name}}</option>{% endfor %}</select></label>
<label>Matter<select id="matter" name="matter_id"><option value="">All matters of the client</option></select></label>
<label>Contact<select id="contact" name="contact_id"><option value="">None</option></select></label>
<label>Language<select name="language" id="language"><option value="">Client language (automatic)</option><option>English</option><option>French</option><option>German</option></select></label>
</div>
<div id="client-facts" class="muted" style="font-size:.9em;margin:8px 0"></div>
<h2 style="margin-top:14px">Time entries of this client</h2>
<p class="muted" style="font-size:.9em">Ticked entries become invoice lines. Greyed entries are already on an issued invoice. Fix wrong data in <a href="{{url_for('billing_control_v18')}}">Billing Control</a> or via Entries &rsaquo; Edit before creating the note.</p>
<div class="table-scroll"><table id="entries"><tr><th></th><th>Date</th><th>Timekeeper</th><th>Matter</th><th>Description</th><th>Hours</th><th>Rate</th><th>Amount</th><th>State</th></tr><tr><td colspan="9">Select a client.</td></tr></table></div>
<p><b>Selected: <span id="sel-count">0</span> entries, <span id="sel-hours">0.00</span> h, <span id="sel-amount">0.00</span></b></p>
<button>Create draft fee note</button>
</form></section>
<script>
(function(){
const P={{payload|tojson}};
const client=document.getElementById('client'),matter=document.getElementById('matter'),contact=document.getElementById('contact'),lang=document.getElementById('language'),tbl=document.getElementById('entries'),facts=document.getElementById('client-facts');
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function totals(){let n=0,h=0,a=0;tbl.querySelectorAll('input[name="timer_ids"]:checked').forEach(cb=>{n++;h+=Number(cb.dataset.hours||0);a+=Number(cb.dataset.amount||0);});document.getElementById('sel-count').textContent=n;document.getElementById('sel-hours').textContent=h.toFixed(2);document.getElementById('sel-amount').textContent=a.toFixed(2);}
async function load(){const cid=client.value;if(!cid){return;}
 const c=P.clients[cid]||{};
 facts.innerHTML='Language: <b>'+esc(c.language)+'</b> · VAT: <b>'+esc(c.vat_treatment)+'</b> · Payment terms: <b>'+esc(c.payment_terms||'30 days')+'</b> · Client rate: <b>'+esc(c.rate||'timekeeper rate')+'</b> · Engagement letter: <b>'+esc(c.engagement_date||'-')+'</b>'+(c.missing.length?'<br><span style="color:#b00">Missing client data: '+esc(c.missing.join(', '))+' – complete it in Admin › Data management.</span>':'');
 const r=await fetch('/api/invoice-entries/'+cid+(matter.value?'?matter_id='+matter.value:''));const d=await r.json();
 const curM=matter.value;matter.innerHTML='<option value="">All matters of the client</option>'+d.matters.map(m=>`<option value="${m.id}" ${String(m.id)===curM?'selected':''}>${esc((m.reference?m.reference+' | ':'')+m.name)}</option>`).join('');
 const curC=contact.value;contact.innerHTML='<option value="">None</option>'+d.contacts.map(x=>`<option value="${x.id}" ${String(x.id)===curC?'selected':''}>${esc(x.display_name)}</option>`).join('');
 if(!d.entries.length){tbl.innerHTML='<tr><th></th><th>Date</th><th>Timekeeper</th><th>Matter</th><th>Description</th><th>Hours</th><th>Rate</th><th>Amount</th><th>State</th></tr><tr><td colspan="9">No time entries recorded for this client'+(matter.value?' and matter':'')+'.</td></tr>';totals();return;}
 tbl.innerHTML='<tr><th><input type="checkbox" id="all" checked></th><th>Date</th><th>Timekeeper</th><th>Matter</th><th>Description</th><th>Hours</th><th>Rate</th><th>Amount</th><th>State</th></tr>'+d.entries.map(e=>`<tr style="${e.state==='issued'||e.state==='running'?'opacity:.5':''}"><td>${e.selectable?`<input type="checkbox" name="timer_ids" value="${e.id}" data-hours="${e.hours}" data-amount="${e.amount}" ${e.checked?'checked':''}>`:''}</td><td style="white-space:nowrap">${esc(e.date)}</td><td>${esc(e.timekeeper)}</td><td>${esc(e.matter)}</td><td>${esc(e.services)}</td><td>${e.hours.toFixed(2)}</td><td>${e.rate.toFixed(2)}</td><td>${e.amount.toFixed(2)}</td><td style="font-size:.85em">${esc(e.note)}</td></tr>`).join('');
 document.getElementById('all').addEventListener('change',ev=>{tbl.querySelectorAll('input[name="timer_ids"]').forEach(cb=>cb.checked=ev.target.checked);totals();});
 totals();}
tbl.addEventListener('change',totals);client.addEventListener('change',()=>{matter.value='';load();});matter.addEventListener('change',load);
if(client.value)load();
document.getElementById('new-invoice-form').addEventListener('submit',e=>{if(!tbl.querySelector('input[name="timer_ids"]:checked')&&!confirm('No time entries selected. Create an empty draft?'))e.preventDefault();});
})();
</script>
{% endblock %}'''


def _client_payload():
    out = {}
    for c in core.Client.query.order_by(db.func.lower(core.Client.name)).all():
        cf = facts('client', c.id)
        treatment = c.vat_treatment or cf.get('vat_treatment') or ''
        terms = c.payment_terms or cf.get('payment_terms') or ''
        missing = []
        if not (c.address or '').strip():
            missing.append('billing address')
        if not treatment:
            missing.append('VAT treatment')
        if not (c.language or '').strip():
            missing.append('language')
        out[str(c.id)] = {'language': language_name(c.language), 'vat_treatment': canonical_vat_treatment(treatment) if treatment else '(not set - Standard VAT assumed)',
                          'payment_terms': terms, 'rate': ('%.2f' % float(c.client_hourly_rate)) if c.client_hourly_rate is not None else '',
                          'engagement_date': fmt_date(cf.get('engagement_letter_date'), 'fr'), 'missing': missing}
    return out


_old_invoice_new = app.view_functions['invoice_new']


def invoice_new_v35():
    if request.method == 'GET':
        clients = core.alpha_clients(True) if hasattr(core, 'alpha_clients') else core.Client.query.order_by(core.Client.name).all()
        return render_template_string(NEW_INVOICE_PAGE, clients=clients, payload={'clients': _client_payload()}, selected_client=request.args.get('client_id', type=int))
    # Resolve the language before the older layers run: an empty box means
    # "client language", never English.
    client = db.session.get(core.Client, request.form.get('client_id', type=int)) if request.form.get('client_id') else None
    matter = db.session.get(core.Matter, request.form.get('matter_id', type=int)) if request.form.get('matter_id') else None
    if not client:
        flash('Select a client first.')
        return redirect(url_for('invoice_new'))
    form = request.form.copy()
    form['language'] = language_name(form.get('language') or (matter.language if matter and matter.language else None) or client.language)
    request.form = form
    before = db.session.query(db.func.max(core.FeeNoteDraft.id)).scalar() or 0
    response = _old_invoice_new()
    draft = core.FeeNoteDraft.query.filter(core.FeeNoteDraft.id > before).order_by(core.FeeNoteDraft.id.desc()).first()
    if draft:
        try:
            apply_draft_defaults(draft)
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception('v35 draft defaults failed')
            flash('Draft created, but defaults could not be applied completely. Check the header fields.')
    return response


app.view_functions['invoice_new'] = login_required(admin_required(invoice_new_v35))


@app.get('/api/invoice-entries/<int:client_id>')
@login_required
@admin_required
def invoice_entries_v35(client_id):
    mid = request.args.get('matter_id', type=int)
    matters = core.Matter.query.filter_by(client_id=client_id).order_by(db.func.lower(core.Matter.name), core.Matter.id).all()
    if hasattr(core, 'matter_active_final'):
        matters = [m for m in matters if core.matter_active_final(m)]
    contacts = core.contacts_for_context(client_id, mid) if hasattr(core, 'contacts_for_context') else core.Contact.query.filter_by(client_id=client_id).all()
    return jsonify({'matters': [{'id': m.id, 'reference': m.reference or '', 'name': m.name} for m in matters],
                    'contacts': [{'id': c.id, 'display_name': c.display_name} for c in contacts],
                    'entries': client_entries(client_id, mid)})


def apply_draft_defaults(draft):
    """Client/matter master data and rate hierarchy applied to a draft (new or on request)."""
    client = db.session.get(core.Client, draft.client_id)
    matter = db.session.get(core.Matter, draft.matter_id) if draft.matter_id else None
    if not client:
        return
    cf = facts('client', client.id)
    mf = facts('matter', matter.id) if matter else {}
    chosen = (request.form.get('language') or '').strip()
    draft.language = language_name(chosen or (matter.language if matter and matter.language else None) or client.language)
    draft.client_name = client.name
    draft.billing_name = client.billing_name or client.name
    draft.billing_address = client.address or ''
    draft.country = client.country or ''
    draft.vat_number = client.vat_number or ''
    draft.show_vat_number = bool(draft.vat_number)
    draft.currency = (matter.currency if matter and matter.currency else None) or client.currency or 'EUR'
    draft.payment_terms = (matter.payment_terms if matter and matter.payment_terms else None) or mf.get('payment_terms') or client.payment_terms or cf.get('payment_terms') or '30 days'
    treatment = (matter.vat_treatment if matter and matter.vat_treatment else None) or mf.get('vat_treatment') or client.vat_treatment or cf.get('vat_treatment') or 'Standard VAT'
    draft.vat_treatment = canonical_vat_treatment(treatment)
    draft.vat_rate = 0 if draft.vat_treatment != 'Standard VAT' else 19
    draft.vat_label = {'French': 'TVA', 'German': 'MwSt.', 'English': 'VAT'}[draft.language]
    engagement = mf.get('engagement_letter_date') or mf.get('engagement_date') or cf.get('engagement_letter_date') or ''
    if engagement:
        draft.engagement_date = engagement
    draft.show_engagement = bool(draft.engagement_date)
    if not draft.document_date:
        draft.document_date = date.today().isoformat()
    telecom_text = {'English': 'Post and telecommunications flat rate pursuant to statutory provisions (No. 7002 RVG).',
                    'French': 'Forfait de télécommunications conformément aux dispositions légales (n° 7002 RVG).',
                    'German': 'Telekommunikationspauschale entsprechend den gesetzlichen Regelungen (Nr. 7002 RVG).'}[draft.language]
    draft.telecom_description = telecom_text
    for line in core.FeeNoteLine.query.filter_by(draft_id=draft.id).all():
        line.vat_treatment = draft.vat_treatment
        line.vat_rate = draft.vat_rate
        if line.source_timer_id:
            t = db.session.get(core.Timer, line.source_timer_id)
            if t:
                if float(t.applied_rate or 0) <= 0:
                    t.applied_rate = applicable_rate(t.user, t.client, t.matter)
                    t.amount = round(float(t.billable_hours or 0) * float(t.applied_rate), 2)
                line.rate = float(t.applied_rate or 0)
                line.quantity = float(t.billable_hours or 0)
                if not line.amount_override:
                    line.amount = round(line.quantity * line.rate, 2)
                line.timekeeper = t.user.display_name if t.user else line.timekeeper
                line.service_date = t.start_time.date().isoformat() if t.start_time else line.service_date
                line.include_in_detail = True
    db.session.flush()
    recalc(draft)


def recalc(draft):
    fn = getattr(core, 'recalculate_v5', None) or getattr(core, 'recalculate', None)
    if fn:
        fn(draft)


@app.post('/invoices/<int:draft_id>/apply-defaults-v35')
@login_required
@admin_required
def apply_defaults_v35(draft_id):
    draft = db.session.get(core.FeeNoteDraft, draft_id)
    if not draft:
        return ('Not found', 404)
    if draft.status == 'Issued':
        flash('Issued invoice: cancel it first if master data must be re-applied.')
    else:
        try:
            apply_draft_defaults(draft)
            db.session.commit()
            flash('Client/matter data and hourly-rate hierarchy re-applied to this draft.')
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('v35 re-apply failed')
            flash('Could not re-apply defaults: %s' % exc)
    return redirect(request.referrer or url_for('edit_fee_note', draft_id=draft_id))


# =============================================================================
# Word generation: preview and issue
# =============================================================================

def draft_payload(draft, number):
    lines = []
    for l in core.FeeNoteLine.query.filter_by(draft_id=draft.id).order_by(core.FeeNoteLine.sort_order, core.FeeNoteLine.id).all():
        lines.append({'id': l.id, 'sort_order': l.sort_order or 0, 'included': bool(l.included), 'line_type': l.line_type, 'description': l.description,
                      'service_date': l.service_date, 'timekeeper': l.timekeeper, 'quantity': l.quantity, 'rate': l.rate, 'amount': l.amount,
                      'include_in_detail': bool(l.include_in_detail) or l.line_type == 'Time-based fees'})
    title_code = core.setting('invoice_contact_title_' + str(draft.id), '') if hasattr(core, 'setting') else ''
    title_map = {'English': {'mr': 'Mr', 'ms': 'Ms'}, 'French': {'mr': 'M.', 'ms': 'Mme'}, 'German': {'mr': 'Herr', 'ms': 'Frau'}}
    contact = (title_map.get(language_name(draft.language), {}).get(title_code, '') + ' ' + (draft.contact_name or '')).strip()
    return {'language': draft.language, 'number': number, 'billing_name': draft.billing_name, 'billing_address': draft.billing_address,
            'country': draft.country, 'contact': contact, 'place': draft.issue_place or 'Berlin', 'document_date': draft.document_date,
            'vat_number': draft.vat_number, 'show_vat_number': bool(draft.show_vat_number) and bool((draft.vat_number or '').strip()),
            'client_name': draft.client_name, 'matter_text': draft.matter_text, 'engagement_date': draft.engagement_date,
            'show_engagement': bool(draft.show_engagement), 'payment_terms': draft.payment_terms, 'currency': draft.currency or 'EUR',
            'vat_treatment': draft.vat_treatment, 'vat_rate': draft.vat_rate, 'vat_wording': draft.vat_wording,
            'telecom_description': draft.telecom_description, 'telecom_amount': draft.telecom_amount, 'show_telecom': bool(draft.show_telecom),
            'show_time_schedule': bool(draft.show_time_schedule), 'annex_text': draft.annex_text, 'lines': lines}


def template_for(draft):
    code = {'French': 'FR', 'German': 'DE'}.get(language_name(draft.language), 'EN')
    return core.ASSETS / ('MARSIP reference invoice ' + code + '.docx')


def file_name(draft, number):
    safe_client = re.sub(r'[^A-Za-z0-9 ._-]+', '', draft.client_name or draft.billing_name or 'Client').strip()
    stamp = (draft.document_date or date.today().isoformat()).replace('-', '')[:8]
    return '{} - {} - Invoice - {}.docx'.format(number, safe_client, stamp)


def docx_response(data, filename):
    return send_file(io.BytesIO(data), as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


def normalise_before_output(draft):
    draft.vat_treatment = canonical_vat_treatment(draft.vat_treatment)
    draft.vat_rate = 0 if draft.vat_treatment != 'Standard VAT' else 19
    for line in core.FeeNoteLine.query.filter_by(draft_id=draft.id).all():
        line.vat_rate = draft.vat_rate
    recalc(draft)


@app.get('/invoices/<int:draft_id>/preview-v35.docx')
@login_required
@admin_required
def preview_fee_note_v35(draft_id):
    draft = db.session.get(core.FeeNoteDraft, draft_id)
    if not draft:
        return ('Not found', 404)
    template = template_for(draft)
    if not template.exists():
        flash('Template missing: ' + template.name)
        return redirect(url_for('edit_fee_note', draft_id=draft.id))
    normalise_before_output(draft)
    db.session.commit()
    number = draft.invoice_number or TEXTS[lang_code(draft.language)]['draft']
    data, _ = build_invoice_document(template, draft_payload(draft, number))
    return docx_response(data, file_name(draft, number))


def next_number_v35(draft):
    if draft.invoice_number:
        return draft.invoice_number
    year = date.today().year
    prefix = 'BER'
    try:
        start = int(core.setting('invoice_next', '3172') or 3172)
    except ValueError:
        start = 3172
    pattern = re.compile(rf'^{year}-{prefix}(\d+)$', re.I)
    used = []
    for row in core.FeeNoteDraft.query.filter(core.FeeNoteDraft.invoice_number.isnot(None)).all():
        m = pattern.match(str(row.invoice_number or '').strip())
        if not m:
            continue
        if row.status == 'Cancelled':
            # a cancelled note does not block its number; keep history as CANCELLED-<number>
            row.invoice_number = 'CANCELLED-' + row.invoice_number
            continue
        used.append(int(m.group(1)))
    db.session.flush()
    number = max([start - 1] + used) + 1
    core.set_setting('invoice_next', number + 1)
    return f'{year}-{prefix}{number}'


core.next_number = next_number_v35


def issue_fee_note_v35(draft_id):
    draft = db.session.get(core.FeeNoteDraft, draft_id)
    if not draft:
        return ('Not found', 404)
    if draft.status == 'Cancelled':
        flash('This invoice is cancelled. Reactivate it in the invoice register before issuing.')
        return redirect(url_for('edit_fee_note', draft_id=draft.id))
    template = template_for(draft)
    if not template.exists():
        flash('Template missing: ' + template.name)
        return redirect(url_for('edit_fee_note', draft_id=draft.id))
    normalise_before_output(draft)
    if not draft.invoice_number:
        draft.invoice_number = next_number_v35(draft)
    number = draft.invoice_number
    data, totals = build_invoice_document(template, draft_payload(draft, number))
    draft.status = 'Issued'
    draft.issued_at = draft.issued_at or datetime.now(timezone.utc)
    included = core.FeeNoteLine.query.filter_by(draft_id=draft.id, included=True).all()
    for line in included:
        if line.source_timer_id:
            t = db.session.get(core.Timer, line.source_timer_id)
            if t:
                refs = [x.strip() for x in str(t.invoice_number or '').split('|') if x.strip() and not x.strip().startswith('CANCELLED')]
                if number not in refs:
                    refs.append(number)
                t.invoice_number = ' | '.join(refs)
                t.invoiced_status = 'Invoiced'
    db.session.commit()
    try:
        if hasattr(core, '_v18_sync_links'):
            core._v18_sync_links(draft)
        if hasattr(core, '_v18_audit'):
            core._v18_audit('invoice', draft.id, 'issue_v35', None, core._v18_draft_snap(draft))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception('v35 link sync failed (invoice itself was issued)')
    return docx_response(data, file_name(draft, number))


app.view_functions['issue_fee_note'] = login_required(admin_required(issue_fee_note_v35))


# Cancelling frees the number so the sequence can be reused (e.g. test invoices).
_old_control_action = app.view_functions.get('invoice_control_action_v18')
if _old_control_action:
    def invoice_control_action_v35(draft_id):
        response = _old_control_action(draft_id)
        if request.form.get('action') == 'cancel':
            d = db.session.get(core.FeeNoteDraft, draft_id)
            if d and d.status == 'Cancelled' and d.invoice_number and not d.invoice_number.startswith('CANCELLED-'):
                old = d.invoice_number
                d.invoice_number = 'CANCELLED-' + old
                for line in core.FeeNoteLine.query.filter_by(draft_id=d.id).all():
                    if line.source_timer_id:
                        t = db.session.get(core.Timer, line.source_timer_id)
                        if t:
                            refs = [x.strip() for x in str(t.invoice_number or '').split('|') if x.strip() and x.strip() != old]
                            t.invoice_number = ' | '.join(refs) or None
                            if not refs:
                                t.invoiced_status = 'Unbilled'
                db.session.commit()
                flash('Number %s released; it can be used again by the next issued invoice.' % old)
        return response
    app.view_functions['invoice_control_action_v18'] = login_required(admin_required(invoice_control_action_v35))


# =============================================================================
# Editor pages: preview button, re-apply defaults button, Timekeeper wording
# =============================================================================

@app.after_request
def editor_buttons_v35(response):
    if response.status_code != 200 or 'text/html' not in response.headers.get('Content-Type', ''):
        return response
    if request.endpoint not in ('edit_fee_note', 'edit_fee_note_original_v4', 'invoice_control_edit_v18'):
        return response
    did = request.view_args.get('draft_id') if request.view_args else None
    if not did:
        return response
    html = response.get_data(as_text=True)
    if 'marsip-v35-buttons' in html:
        return response
    draft = db.session.get(core.FeeNoteDraft, did)
    status = draft.status if draft else ''
    block = ('<section class="card" id="marsip-v35-buttons"><h2>Word document</h2>'
             '<p><a class="button" href="%s">Preview Word (no number, nothing is changed)</a></p>'
             % url_for('preview_fee_note_v35', draft_id=did))
    if status != 'Issued':
        block += ('<form method="post" action="%s" style="display:inline"><button class="secondary">Re-apply client/matter data and rate hierarchy</button></form> '
                  % url_for('apply_defaults_v35', draft_id=did))
    block += ('<form method="post" action="%s" style="display:inline"><button>%s</button></form>'
              % (url_for('issue_fee_note', draft_id=did), 'Regenerate issued Word invoice' if status == 'Issued' else 'Issue: assign number and download Word invoice'))
    block += '<p class="muted" style="font-size:.85em">Issuing assigns the next number (<b>%s</b> onwards), marks the note as issued and flags its time entries as invoiced. Use Preview while the note is still being checked.</p></section>' % (core.setting('invoice_next', '3172') if hasattr(core, 'setting') else '')
    html = html.replace('</main>', block + '</main>', 1) if '</main>' in html else html + block
    html = html.replace('<label>Employee<select', '<label>Timekeeper<select')
    response.set_data(html)
    response.headers['Content-Length'] = len(response.get_data())
    return response


# Entries > Edit: the person is the timekeeper.
entry_editor.EDIT_TEMPLATE = entry_editor.EDIT_TEMPLATE.replace('<label>Employee<select name="user_id"', '<label>Timekeeper<select name="user_id"')


# =============================================================================
# Master data: VAT treatment choice, client engagement date, matter defaults
# =============================================================================

VAT_CHOICES = ('Standard VAT', 'Reverse charge', 'Other exemption')

_client_vat_input = '<label>VAT treatment<input name="vat_treatment"></label>'
_client_vat_select = ('<label>VAT treatment<select name="vat_treatment">' + ''.join('<option>%s</option>' % x for x in VAT_CHOICES) + '</select></label>'
                      '<label>Engagement letter date (client default)<input type="date" name="engagement_letter_date_client"></label>')
if _client_vat_input in core.MASTER_CONSOLIDATED:
    core.MASTER_CONSOLIDATED = core.MASTER_CONSOLIDATED.replace(_client_vat_input, _client_vat_select, 1)

_old_payload_client = core.payload_client


def payload_client_v35(x):
    out = _old_payload_client(x)
    f = facts('client', x.id)
    out['vat_treatment'] = canonical_vat_treatment(x.vat_treatment or f.get('vat_treatment') or '') if (x.vat_treatment or f.get('vat_treatment')) else 'Standard VAT'
    out['engagement_letter_date_client'] = f.get('engagement_letter_date', '')
    out['payment_terms'] = x.payment_terms or f.get('payment_terms', '')
    return out


core.payload_client = payload_client_v35


def _flash_errors(fn):
    """Show the real reason when a master-data save fails instead of 'see server log'."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('v35 master save failed')
            flash('Not saved: %s' % exc)
            return redirect(url_for('master_v4'))
    wrapped.__name__ = 'v35_' + getattr(fn, '__name__', 'save')
    return wrapped


_old_client_save = app.view_functions['client_fact_v7']


def client_save_v35():
    existing_id = request.form.get('id', type=int)
    before_max = db.session.query(db.func.max(core.Client.id)).scalar() or 0
    response = _old_client_save()
    client = db.session.get(core.Client, existing_id) if existing_id else core.Client.query.filter(core.Client.id > before_max).order_by(core.Client.id.desc()).first()
    if client:
        try:
            treatment = canonical_vat_treatment(request.form.get('vat_treatment') or 'Standard VAT')
            client.vat_treatment = treatment
            client.payment_terms = (request.form.get('payment_terms') or '').strip() or client.payment_terms
            update = {'vat_treatment': treatment}
            raw = (request.form.get('engagement_letter_date_client') or '').strip()
            if raw and parse_date(raw):
                update['engagement_letter_date'] = parse_date(raw).isoformat()
            core.save_fact('client', client.id, update)
            db.session.commit()
            flash('Client "%s" saved (#%s).' % (client.name, client.id))
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('v35 client post-save failed')
            flash('Client saved, but VAT/engagement defaults failed: %s' % exc)
    elif not existing_id:
        flash('The client record was not created. Check the required fields (name) and try again.')
    return response


app.view_functions['client_fact_v7'] = login_required(admin_required(_flash_errors(client_save_v35)))

_old_matter_save = app.view_functions['matter_fact_v7']


def matter_save_v35():
    existing_id = request.form.get('id', type=int)
    before_max = db.session.query(db.func.max(core.Matter.id)).scalar() or 0
    response = _old_matter_save()
    matter = db.session.get(core.Matter, existing_id) if existing_id else core.Matter.query.filter(core.Matter.id > before_max).order_by(core.Matter.id.desc()).first()
    if matter:
        try:
            mf = facts('matter', matter.id)
            cf = facts('client', matter.client_id)
            if not mf.get('engagement_letter_date') and cf.get('engagement_letter_date'):
                core.save_fact('matter', matter.id, {'engagement_letter_date': cf['engagement_letter_date']})
            db.session.commit()
            flash('Matter "%s" saved (#%s).' % (matter.name, matter.id))
        except Exception as exc:
            db.session.rollback()
            flash('Matter saved, but the engagement-date default failed: %s' % exc)
    elif not existing_id:
        flash('The matter record was not created. Check the required fields (name, client) and try again.')
    return response


app.view_functions['matter_fact_v7'] = login_required(admin_required(_flash_errors(matter_save_v35)))

_old_contact_save = app.view_functions.get('contact_fact_v7')
if _old_contact_save:
    app.view_functions['contact_fact_v7'] = login_required(admin_required(_flash_errors(_old_contact_save)))


@app.after_request
def master_defaults_v35(response):
    """Matter form: hourly rate and engagement date default to the client's values."""
    if request.endpoint != 'master_v4' or response.status_code != 200 or 'text/html' not in response.headers.get('Content-Type', ''):
        return response
    html = response.get_data(as_text=True)
    if 'marsip-v35-master' in html:
        return response
    payload = {str(c.id): {'rate': ('%.2f' % float(c.client_hourly_rate)) if c.client_hourly_rate is not None else '',
                           'engagement': facts('client', c.id).get('engagement_letter_date', ''),'terms':c.payment_terms or facts('client',c.id).get('payment_terms','')}
               for c in core.Client.query.all()}
    import json as _json
    script = ('<script id="marsip-v35-master">(function(){const C=%s;'
              'const rec=document.getElementById("matter-record"),form=rec&&rec.closest("form");if(!form)return;'
              'const cl=form.querySelector("[name=client_id]"),rate=form.querySelector("[name=hourly_rate]"),eng=form.querySelector("[name=engagement_letter_date]"),terms=form.querySelector("[name=payment_terms]");'
              'function fill(force){const c=C[cl.value]||{};if(rate&&(force||!rate.value))rate.value=c.rate||"";if(eng&&(force||!eng.value))eng.value=c.engagement||"";if(terms&&(force||!terms.value))terms.value=c.terms||"";}'
              'cl.addEventListener("change",()=>fill(true));rec.addEventListener("change",()=>{if(!rec.value)setTimeout(()=>fill(true),0);});if(!rec.value)fill(false);})();</script>'
              % _json.dumps(payload))
    html = html.replace('</body>', script + '</body>', 1) if '</body>' in html else html + script
    response.set_data(html)
    response.headers['Content-Length'] = len(response.get_data())
    return response


# Add line in the invoice editor: an empty/zero rate takes the client's rate hierarchy.
_old_line_add = app.view_functions.get('invoice_line_add_v18')
if _old_line_add:
    def invoice_line_add_v35(draft_id):
        form = request.form.copy()
        if D(form.get('rate')) <= 0:
            d = db.session.get(core.FeeNoteDraft, draft_id)
            if d:
                user = db.session.get(core.User, form.get('employee_id', type=int)) if form.get('employee_id') else None
                client = db.session.get(core.Client, d.client_id)
                matter = db.session.get(core.Matter, d.matter_id) if d.matter_id else None
                form['rate'] = '%.2f' % applicable_rate(user, client, matter)
                request.form = form
        return _old_line_add(draft_id)
    app.view_functions['invoice_line_add_v18'] = login_required(admin_required(invoice_line_add_v35))


@app.get('/health-v35')
def health_v35():
    return jsonify({'layer': VERSION, 'issue': app.view_functions['issue_fee_note'].__name__, 'invoice_new': app.view_functions['invoice_new'].__name__})