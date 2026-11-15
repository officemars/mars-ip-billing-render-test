"""MARS-IP V25 - targeted administrator entry editor.
Preserves the current Entries page and only appends an Edit link to its existing action cell.
Start with: gunicorn change25:app ...
"""
from datetime import datetime, timezone, timedelta
from flask import request, redirect, url_for, flash, render_template_string, session
import app as core

app = core.app
db = core.db
Timer = core.Timer
User = core.User
FeeNoteLine = core.FeeNoteLine
FeeNoteDraft = core.FeeNoteDraft
login_required = core.login_required
admin_required = core.admin_required

EDIT_TEMPLATE = r'''{% extends "base.html" %}{% block content %}
<div class="page-head"><div><h1>Edit time entry</h1><p>Entry #{{ entry.id }}</p></div><a href="{{ back }}">Back to Entries</a></div>
<section class="card wide">
<form method="post">
<input type="hidden" name="back" value="{{ back }}">
<div class="grid">
<label>Employee<select name="user_id" required>{% for u in users %}<option value="{{u.id}}" {% if u.id == entry.user_id %}selected{% endif %}>{{u.display_name}}</option>{% endfor %}</select></label>
<label>Worked hours<input type="number" name="worked_hours" min="0" step="0.0001" value="{{entry.worked_hours or 0}}" required></label>
<label>Billable hours<input type="number" name="billable_hours" min="0" step="0.0001" value="{{entry.billable_hours or 0}}" required></label>
<label>Hourly rate<input type="number" name="applied_rate" min="0" step="0.01" value="{{entry.applied_rate or 0}}" required></label>
<label>Amount<input type="number" name="amount" min="0" step="0.01" value="{{entry.amount or 0}}" required></label>
</div>
<label>Description<textarea name="services" rows="6" required>{{entry.services or ''}}</textarea></label>
<label>Notes<textarea name="notes" rows="5">{{entry.notes or ''}}</textarea></label>
<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:14px"><button type="submit">Save changes</button><a class="button secondary" href="{{back}}">Cancel</a></div>
</form>
</section>
{% endblock %}'''

def _safe_back(value):
    value = value or url_for('entries_v14')
    return value if value.startswith('/') and not value.startswith('//') else url_for('entries_v14')

def _sync_draft_lines(entry, employee):
    affected = set()
    for line in FeeNoteLine.query.filter_by(source_timer_id=entry.id).all():
        draft = db.session.get(FeeNoteDraft, line.draft_id)
        if not draft or draft.status in ('Issued', 'Cancelled'):
            continue
        line.service_date = entry.start_time.date().isoformat()
        line.timekeeper = employee.display_name
        line.description = entry.services
        line.quantity = entry.billable_hours
        line.rate = entry.applied_rate
        line.amount = entry.amount
        calculated = round(float(entry.billable_hours or 0) * float(entry.applied_rate or 0), 2)
        line.amount_override = abs(float(entry.amount or 0) - calculated) > 0.001
        affected.add(draft.id)
    for draft_id in affected:
        draft = db.session.get(FeeNoteDraft, draft_id)
        if not draft:
            continue
        if hasattr(core, '_v18_recalc'):
            core._v18_recalc(draft)
        elif hasattr(core, 'recalculate_v5'):
            core.recalculate_v5(draft)
        elif hasattr(core, 'recalculate'):
            core.recalculate(draft)

@app.route('/admin/time-entry/<int:timer_id>/edit-v25', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_entry_edit_v25(timer_id):
    entry = db.session.get(Timer, timer_id)
    if not entry:
        return ('Time entry not found', 404)
    back = _safe_back(request.values.get('back'))
    if request.method == 'POST':
        try:
            employee = db.session.get(User, int(request.form['user_id']))
            if not employee:
                raise ValueError('Selected employee does not exist.')
            worked = max(0.0, float(request.form.get('worked_hours') or 0))
            billable = max(0.0, float(request.form.get('billable_hours') or 0))
            rate = max(0.0, float(request.form.get('applied_rate') or 0))
            amount = max(0.0, float(request.form.get('amount') or 0))
            services = (request.form.get('services') or '').strip()
            notes = (request.form.get('notes') or '').strip()
            if not services:
                raise ValueError('Description is required.')

            entry.user_id = employee.id
            entry.worked_hours = round(worked, 4)
            entry.billable_hours = round(billable, 4)
            entry.applied_rate = round(rate, 2)
            entry.amount = round(amount, 2)
            entry.services = services
            entry.notes = notes
            entry.timekeeper_rate = employee.default_hourly_rate
            if entry.stop_time is not None and entry.start_time is not None:
                entry.stop_time = entry.start_time + timedelta(hours=entry.worked_hours)

            _sync_draft_lines(entry, employee)
            db.session.commit()
            flash('Entry updated in the central database. All live views and editable invoice drafts now use the saved values.')
            return redirect(back)
        except Exception as exc:
            db.session.rollback()
            app.logger.exception('V25 administrator entry update failed for timer %s', timer_id)
            flash('Entry was not saved: ' + str(exc))

    users = User.query.order_by(User.display_name, User.id).all()
    return render_template_string(EDIT_TEMPLATE, entry=entry, users=users, back=back)

@app.after_request
def add_admin_edit_link_v25(response):
    # Do not replace or restyle the existing page. Inject only one link into each
    # existing row whose current action identifies the Timer ID.
    if (
        session.get('role') != 'Administrator'
        or request.endpoint not in ('entries', 'entries_v14')
        or response.status_code != 200
        or 'text/html' not in response.headers.get('Content-Type', '')
    ):
        return response
    html = response.get_data(as_text=True)
    if 'marsip-admin-edit-v25' in html:
        return response
    addition = r'''
<script id="marsip-admin-edit-v25">
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('table tr').forEach(function (row) {
    if (!row.querySelector('td') || row.querySelector('.marsip-edit-v25')) return;
    var candidates = row.querySelectorAll('form[action], a[href], [data-entry-id], [data-timer-id]');
    var id = '';
    Array.from(candidates).some(function (node) {
      var raw = node.getAttribute('data-entry-id') || node.getAttribute('data-timer-id') || node.getAttribute('action') || node.getAttribute('href') || '';
      var match = raw.match(/(?:admin\/entry|time-entry|timer|recent-v10|billing-control-v18)\/(\d+)/);
      if (match) { id = match[1]; return true; }
      return false;
    });
    if (!id) return;
    var cells = row.querySelectorAll(':scope > td');
    if (!cells.length) return;
    var target = cells[cells.length - 1];
    var link = document.createElement('a');
    link.className = 'marsip-edit-v25';
    link.textContent = 'Edit';
    link.href = '/admin/time-entry/' + id + '/edit-v25?back=' + encodeURIComponent(location.pathname + location.search);
    link.style.display = 'inline-block';
    link.style.marginLeft = '6px';
    link.style.whiteSpace = 'nowrap';
    target.appendChild(link);
  });
});
</script>'''
    html = html.replace('</body>', addition + '</body>', 1) if '</body>' in html else html + addition
    response.set_data(html)
    response.headers['Content-Length'] = len(response.get_data())
    return response
