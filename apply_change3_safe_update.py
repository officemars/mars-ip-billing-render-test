from pathlib import Path
import py_compile
import shutil
import sys

TARGET = Path(sys.argv[1] if len(sys.argv) > 1 else 'change3.py')
MARKER = '# ==================== CHANGE 3.17: SAFE ACCESS, ARCHIVE, DATE FILTERS, INVOICE REPAIR ===================='
if not TARGET.exists():
    raise SystemExit(f'File not found: {TARGET}')
original = TARGET.read_text(encoding='utf-8')
if MARKER in original:
    print('Change 3.17 is already installed; no changes made.')
    raise SystemExit(0)
backup = TARGET.with_name(TARGET.name + '.before_change317.bak')
shutil.copy2(TARGET, backup)
PATCH = r'''

# ==================== CHANGE 3.17: SAFE ACCESS, ARCHIVE, DATE FILTERS, INVOICE REPAIR ====================
# Append-only compatibility patch. No existing route, model, or historical record is removed.
from types import SimpleNamespace
from flask import make_response


def _client_is_active_v317(client):
    value = fact_data('client', client.id).get('active', True)
    return value not in (False, 0, '0', 'false', 'False', 'no', 'No')


# 1. All authenticated users can select every active client and every matter for that client.
def matters_all_users_v317(client_id):
    client = db.session.get(Client, client_id)
    if not client or not _client_is_active_v317(client):
        return jsonify([])
    rows = Matter.query.filter_by(client_id=client_id).order_by(Matter.reference, Matter.name).all()
    return jsonify([{'id': row.id, 'reference': row.reference or '', 'name': row.name or ''} for row in rows])
app.view_functions['matters'] = login_required(matters_all_users_v317)


def timer_all_clients_v317():
    active = Timer.query.filter(
        Timer.user_id == session['uid'], Timer.status.in_(['Running', 'Paused'])
    ).order_by(Timer.id.desc()).all()
    for entry in active:
        entry.seconds = elapsed_safe(entry)
    recent_query = (
        Timer.query.filter_by(status='Stopped')
        if session.get('role') == 'Administrator'
        else Timer.query.filter_by(user_id=session['uid'], status='Stopped')
    )
    recent = recent_query.order_by(Timer.id.desc()).limit(50).all()
    clients = [c for c in Client.query.order_by(Client.name).all() if _client_is_active_v317(c)]
    return render_template_string(
        TIMER_PAGE_RUNTIME_V15,
        clients=clients,
        active=active,
        recent=recent,
        invoice_refs={t.id: invoice_references_text_v15(t.id) for t in recent},
    )
app.view_functions['timer'] = login_required(timer_all_clients_v317)


# 2. Client active/not-active control in the existing Client Record. Historical data is retained.
DATA_MANAGEMENT_V317 = DATA_MANAGEMENT_V10.replace(
    '<label>Optional client rate<input type="number" step="0.01" name="rate"></label></div>',
    '<label>Optional client rate<input type="number" step="0.01" name="rate"></label>'
    '<label>Status<select name="active"><option value="1">Active</option>'
    '<option value="0">Not active - archive</option></select></label></div>'
)


def _client_payload_v317(client):
    payload = _v10_client_payload(client)
    payload['active'] = '1' if _client_is_active_v317(client) else '0'
    return payload


def data_management_v317():
    clients = Client.query.order_by(Client.name).all()
    contacts = Contact.query.order_by(Contact.display_name).all()
    matters = Matter.query.order_by(Matter.name).all()
    return render_template_string(
        DATA_MANAGEMENT_V317,
        clients=clients,
        contacts=contacts,
        matters=matters,
        client_payload={str(c.id): _client_payload_v317(c) for c in clients},
        contact_payload={str(c.id): _v10_contact_payload(c) for c in contacts},
        matter_payload={str(m.id): _v10_matter_payload(m) for m in matters},
    )
app.view_functions['master_v4'] = login_required(admin_required(data_management_v317))


def save_client_with_archive_v317():
    client = db.session.get(Client, int(request.form['id'])) if request.form.get('id') else Client(
        external_id='USR-' + datetime.now().strftime('%Y%m%d%H%M%S%f')
    )
    client.name = request.form['name'].strip()
    client.billing_name = request.form.get('billing_name', '').strip() or client.name
    client.address = request.form.get('address', '').strip()
    client.country = request.form.get('country', '').strip()
    client.vat_number = request.form.get('vat_number', '').strip()
    client.language = request.form.get('language') or 'English'
    client.currency = request.form.get('currency') or 'EUR'
    client.client_hourly_rate = fnum(request.form.get('rate'), None)
    db.session.add(client)
    db.session.flush()
    save_fact('client', client.id, {
        'payment_terms': request.form.get('payment_terms', ''),
        'vat_treatment': request.form.get('vat_treatment', ''),
        'billing_email': request.form.get('billing_email', ''),
        'active': request.form.get('active', '1') == '1',
    })
    db.session.commit()
    flash('Client saved and archived.' if request.form.get('active') == '0' else 'Client Record saved.')
    return redirect(url_for('master_v4'))
app.view_functions['client_fact_v7'] = login_required(admin_required(save_client_with_archive_v317))


# 3. Admin Entries filters by user/client/matter and inclusive start/end dates.
ENTRIES_V317 = ENTRIES_V14.replace(
    "{% endif %}</div><button>Apply filters</button>",
    "{% endif %}<label>From date<input type=\"date\" name=\"date_from\" value=\"{{date_from}}\"></label>"
    "<label>To date<input type=\"date\" name=\"date_to\" value=\"{{date_to}}\"></label>"
    "</div><button>Apply filters</button>"
)


def entries_v317():
    query = Timer.query
    selected_user = request.args.get('user_id', type=int)
    selected_client = request.args.get('client_id', type=int)
    selected_matter = request.args.get('matter_id', type=int)
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    if session.get('role') != 'Administrator':
        query = query.filter(Timer.user_id == session['uid'])
        selected_user = session['uid']
    elif selected_user:
        query = query.filter(Timer.user_id == selected_user)
    if selected_client:
        query = query.filter(Timer.client_id == selected_client)
    if selected_matter:
        query = query.filter(Timer.matter_id == selected_matter)
    try:
        if date_from:
            start = datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            query = query.filter(Timer.start_time >= start)
        if date_to:
            end = datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1)
            query = query.filter(Timer.start_time < end)
    except ValueError:
        flash('Use valid From and To dates.')
    entries = query.order_by(Timer.id.desc()).all()
    return render_template_string(
        ENTRIES_V317,
        entries=entries,
        clients=Client.query.order_by(Client.name).all(),
        matters=Matter.query.order_by(Matter.name).all(),
        users=User.query.order_by(User.display_name).all(),
        selected_user=selected_user,
        selected_client=selected_client,
        selected_matter=selected_matter,
        date_from=date_from,
        date_to=date_to,
        user_names={u.id: u.display_name for u in User.query.all()},
        client_names={c.id: c.name for c in Client.query.all()},
        matter_names={m.id: m.name for m in Matter.query.all()},
        invoice_refs={t.id: invoice_references_text_v15(t.id) for t in entries},
        total_worked=sum(float(t.worked_hours or 0) for t in entries),
        total_billable=sum(float(t.billable_hours or 0) for t in entries),
        total_amount=sum(float(t.amount or 0) for t in entries),
    )
app.view_functions['entries_v14'] = login_required(entries_v317)


# 4. The embedded full editor retains its controls but hides the duplicate site navigation.
COMBINED_EDITOR_V5 = COMBINED_EDITOR_V5.replace(
    "src=\"{{url_for('edit_fee_note_original_v4',draft_id=draft.id)}}\"",
    "src=\"{{url_for('edit_fee_note_original_v4',draft_id=draft.id)}}?embedded=1\""
)

@app.after_request
def embedded_invoice_editor_v317(response):
    if (
        request.endpoint == 'edit_fee_note_original_v4'
        and request.args.get('embedded') == '1'
        and response.status_code == 200
        and 'text/html' in response.headers.get('Content-Type', '')
    ):
        html = response.get_data(as_text=True)
        css = ('<style id="embedded-v317">header,nav,.navbar,.topbar,.sidebar,.site-header,'
               '.app-header,.main-nav{display:none!important}body{padding-top:0!important}</style>')
        html = html.replace('</head>', css + '</head>') if '</head>' in html else css + html
        response.set_data(html)
        response.headers['Content-Length'] = len(response.get_data())
    return response


# Invoice-specific contact title, translated at generation time.
@app.post('/invoices/<int:draft_id>/contact-title-v317')
@login_required
@admin_required
def invoice_contact_title_v317(draft_id):
    set_setting('invoice_contact_title_' + str(draft_id), request.form.get('contact_title', ''))
    flash('Contact title saved.')
    return redirect(url_for('edit_fee_note', draft_id=draft_id))

_old_invoice_editor_v317 = app.view_functions['edit_fee_note']
def invoice_editor_v317(draft_id):
    response = make_response(_old_invoice_editor_v317(draft_id))
    if response.status_code == 200 and 'text/html' in response.headers.get('Content-Type', ''):
        current = setting('invoice_contact_title_' + str(draft_id), '')
        options = ''.join(
            '<option value="{}" {}>{}</option>'.format(value, 'selected' if current == value else '', label)
            for value, label in [('', 'No title'), ('mr', 'Mr / M. / Herr'), ('ms', 'Ms / Mme / Frau')]
        )
        card = (
            '<section class="card"><h2>Contact title</h2><form method="post" action="{}">'
            '<label>Title before contact name<select name="contact_title">{}</select></label>'
            '<button>Save contact title</button></form></section>'
        ).format(url_for('invoice_contact_title_v317', draft_id=draft_id), options)
        html = response.get_data(as_text=True)
        marker = '<section class="card wide"><h2>Full editable fee note</h2>'
        html = html.replace(marker, card + marker)
        response.set_data(html)
        response.headers['Content-Length'] = len(response.get_data())
    return response
app.view_functions['edit_fee_note'] = login_required(admin_required(invoice_editor_v317))


def _invoice_category_v317(line):
    value = ((line.line_type or '') + ' ' + (line.description or '')).lower()
    if line.line_type == 'Time-based fees': return 'time'
    if line.line_type == 'Annual trademark monitoring': return 'monitoring'
    if line.line_type in ('Costs', 'Disbursement'): return 'costs'
    if line.line_type in ('Flat fee', 'Trademark filing package', 'Renewal package', 'Success fee', 'Free data entry'): return 'flat'
    if 'monitor' in value or 'surveillance' in value or 'überwachung' in value: return 'monitoring'
    if 'cost' in value or 'disbursement' in value or 'frais' in value or 'kosten' in value: return 'costs'
    return 'flat'


def _invoice_labels_v317(language):
    if language == 'French':
        return {'time':'Heures facturées HT\n(selon le tableau des prestations et temps passés ci-joint)', 'monitoring':'Surveillance annuelle de marque selon le détail joint', 'flat':'Honoraires forfaitaires', 'costs':'Frais / Débours'}
    if language == 'German':
        return {'time':'Nach Zeitaufwand abgerechnete Honorare netto\n(gemäß der beigefügten Aufstellung der Leistungen und Zeiten)', 'monitoring':'Jährliche Markenüberwachung gemäß beigefügter Aufstellung', 'flat':'Pauschalhonorar', 'costs':'Kosten / Auslagen'}
    return {'time':'Time-based fees excluding VAT\n(according to the attached schedule of services and time entries)', 'monitoring':'Annual trademark monitoring according to the attached details', 'flat':'Flat fee', 'costs':'Costs / Disbursements'}


def _clean_duplicate_place_v317(document):
    replacements = {'Berlin Berlin,':'Berlin,', 'Berlin, Berlin,':'Berlin,', 'Berlin Berlin':'Berlin'}
    previous.replace_everywhere(document, replacements)


def issue_invoice_repaired_v317(draft_id):
    draft = db.session.get(FeeNoteDraft, draft_id)
    if not draft:
        return ('Not found', 404)
    reverse = draft.vat_treatment in ('Reverse charge', 'Other exemption')
    draft.vat_rate = 0 if reverse else 19
    included = FeeNoteLine.query.filter_by(draft_id=draft.id, included=True).order_by(FeeNoteLine.sort_order, FeeNoteLine.id).all()
    for line in included:
        line.vat_rate = 0 if reverse else 19
    recalculate_v5(draft)
    if not draft.invoice_number:
        draft.invoice_number = next_number(draft)
    number = draft.invoice_number
    code = {'English':'EN', 'French':'FR', 'German':'DE'}.get(draft.language, 'EN')
    template = ASSETS / ('MARSIP reference invoice ' + code + '.docx')
    if not template.exists():
        flash('Template missing: ' + template.name)
        return redirect(url_for('edit_fee_note', draft_id=draft.id))
    document = Document(template)
    labels = _invoice_labels_v317(draft.language)
    grouped = {}
    for line in included:
        category = _invoice_category_v317(line)
        grouped[category] = grouped.get(category, 0.0) + float(line.amount or 0)
    summary_lines = []
    for order, category in enumerate(('time', 'monitoring', 'flat', 'costs'), 1):
        amount = grouped.get(category, 0.0)
        if abs(amount) > 0.00001:
            summary_lines.append(SimpleNamespace(id=order, sort_order=order, description=labels[category], amount=amount))
    previous.rebuild_summary(document, draft, summary_lines)
    detail = [line for line in included if line.include_in_detail or line.line_type == 'Time-based fees']
    if draft.show_time_schedule:
        previous.rebuild_detail(document, draft, detail)
    title_code = setting('invoice_contact_title_' + str(draft.id), '')
    title_map = {'English':{'mr':'Mr', 'ms':'Ms'}, 'French':{'mr':'M.', 'ms':'Mme'}, 'German':{'mr':'Herr', 'ms':'Frau'}}
    contact = (title_map.get(draft.language, {}).get(title_code, '') + ' ' + (draft.contact_name or '')).strip()
    replacements = {
        '[final sequential document number, assigned only when the document is issued]': number,
        '[ClientId]': draft.billing_name,
        '[billing address]': draft.billing_address,
        '[country]': draft.country,
        '[contact person at the client or contact person linked to the matter]': contact,
        '[place of issue, with Berlin as the default]': draft.issue_place or 'Berlin',
        '[place of issue, with Berlin as the default ]': draft.issue_place or 'Berlin',
        '[document date, with the current date as the default]': draft.document_date,
        '[document date, with the current date as the default ]': draft.document_date,
        '[VAT number Client]': draft.vat_number if draft.show_vat_number else '',
        '[client Name]': draft.client_name,
        '[Matter]': draft.matter_text,
        '[date of the Engagement Letter]': draft.engagement_date if draft.show_engagement else '',
        '[date of the Engagement letter]': draft.engagement_date if draft.show_engagement else '',
        '[payment due date: eight days/30 days/30 days end of month]': draft.payment_terms,
        '[payment due date: acht Tage/30 Tage/30 Tage zum Monatsende]': draft.payment_terms,
        '[payment due date: huitainte/30 jours/30 jours fin de mois]': draft.payment_terms,
        'XXXXX': draft.payment_terms,
        'XXXXXX': draft.payment_terms,
        '20 %': '19 %',
        '20%': '19%',
        'Final Total Including VAT': previous.money(draft.gross_total, draft.currency),
        'Total Final TTC': previous.money(draft.gross_total, draft.currency),
    }
    previous.replace_everywhere(document, replacements)
    _clean_duplicate_place_v317(document)
    output = BytesIO()
    document.save(output)
    output.seek(0)
    draft.status = 'Issued'
    draft.issued_at = datetime.now(timezone.utc)
    for line in included:
        if line.source_timer_id:
            timer = db.session.get(Timer, line.source_timer_id)
            if timer:
                refs = [x.strip() for x in str(timer.invoice_number or '').split('|') if x.strip()]
                if number not in refs:
                    refs.append(number)
                timer.invoice_number = ' | '.join(refs)
                timer.invoiced_status = 'Invoiced'
    db.session.commit()
    safe_client = re.sub(r'[^A-Za-z0-9 ._-]+', '', draft.client_name or draft.billing_name or 'Client').strip()
    stamp = (draft.document_date or date.today().isoformat()).replace('-', '')[:8]
    filename = '{} - {} - Invoice - {}.docx'.format(number, safe_client, stamp)
    return send_file(output, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
app.view_functions['issue_fee_note'] = login_required(admin_required(issue_invoice_repaired_v317))
'''
TARGET.write_text(original.rstrip() + PATCH + '\n', encoding='utf-8')
try:
    py_compile.compile(str(TARGET), doraise=True)
except Exception:
    shutil.copy2(backup, TARGET)
    raise
print(f'Updated safely: {TARGET}')
print(f'Backup created: {backup}')
print('Python compilation passed.')
