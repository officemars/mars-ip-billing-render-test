"""MARS-IP V27: single Edit link, automatic amounts, original Entries layout.
Requires unchanged app.py and existing change25.py. Gunicorn: change27:app
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import timedelta
from flask import request, redirect, url_for, flash, render_template_string
import app as core
import change25 as previous

app = core.app

def _number(value):
    raw = str(value or '0').strip().replace(' ', '').replace(',', '.')
    n = Decimal(raw)
    if not n.is_finite() or n < 0:
        raise ValueError('Hours and rates must be nonnegative finite numbers.')
    return n

# Disable V25's JavaScript link injection, which caused the duplicate.
for group in app.after_request_funcs.values():
    group[:] = [f for f in group if f.__name__ != 'add_admin_edit_link_v25']

# Keep the exact existing Entries page, adding a single link inside the
# existing invoice-number cell. No columns, layout or selection changes.
_original_template = core._entries_template_service_date_final
_needle = "{{invoice_refs.get(t.id,'Unbilled')}}</span></td></tr>"
_insert = """{{invoice_refs.get(t.id,'Unbilled')}}</span>{% if session.get('role') == 'Administrator' %}<br><a href="{{url_for('admin_entry_edit_v25',timer_id=t.id,back=request.full_path)}}" style="display:inline-block;margin-top:5px;white-space:nowrap">Edit</a>{% endif %}</td></tr>"""

def _entries_template_v27():
    original = _original_template()
    if _needle not in original:
        app.logger.error('V27 Entries insertion marker missing')
        return original
    return original.replace(_needle, _insert, 1)

core._entries_template_service_date_final = _entries_template_v27

# Reuse V25's edit page with calculated (read-only) amount and live preview.
previous.EDIT_TEMPLATE = previous.EDIT_TEMPLATE.replace(
    'name="amount" min="0" step="0.01"',
    'name="amount" min="0" step="0.01" readonly'
)
previous.EDIT_TEMPLATE = previous.EDIT_TEMPLATE.replace(
    '</form>',
    '''<p style="font-size:.9em">Amount = billable hours × hourly rate. Changes are saved to the central entry and editable drafts. Issued invoices remain unchanged.</p>
<script>
(function(){
  const f=document.currentScript.closest('form');
  if(!f)return;
  const bill=f.elements['billable_hours'], rate=f.elements['applied_rate'], amount=f.elements['amount'];
  function numeric(x){const n=Number(String(x).trim().replace(',','.'));return Number.isFinite(n)&&n>=0?n:0;}
  function recalc(){amount.value=(Math.round((numeric(bill.value)*numeric(rate.value)+Number.EPSILON)*100)/100).toFixed(2);}
  bill.addEventListener('input',recalc);rate.addEventListener('input',recalc);
  recalc();
})();
</script></form>''', 1
)

# Replace only the V25 handler, preserving its URL and administrator checks.
@core.login_required
@core.admin_required
def admin_entry_edit_v27(timer_id):
    entry = core.db.session.get(core.Timer, timer_id)
    if entry is None:
        return ('Time entry not found', 404)
    back = previous._safe_back(request.values.get('back'))
    if request.method == 'POST':
        try:
            user = core.db.session.get(core.User, int(request.form['user_id']))
            if user is None:
                raise ValueError('Invalid employee.')
            worked = _number(request.form['worked_hours'])
            billable = _number(request.form['billable_hours'])
            rate = _number(request.form['applied_rate'])
            amount = (billable * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            services = (request.form.get('services') or '').strip()
            if not services:
                raise ValueError('Description is required.')
            before = core._v18_entry_snap(entry) if hasattr(core, '_v18_entry_snap') else None
            entry.user_id = user.id
            entry.worked_hours = float(worked.quantize(Decimal('0.0001')))
            entry.billable_hours = float(billable.quantize(Decimal('0.0001')))
            entry.applied_rate = float(rate.quantize(Decimal('0.01')))
            entry.amount = float(amount)
            entry.services = services
            entry.notes = (request.form.get('notes') or '').strip()
            entry.timekeeper_rate = user.default_hourly_rate
            if entry.stop_time is not None and entry.start_time is not None:
                entry.stop_time = entry.start_time + timedelta(hours=entry.worked_hours)
            previous._sync_draft_lines(entry, user)
            if before is not None and hasattr(core, '_v18_audit'):
                core._v18_audit('timer', entry.id, 'admin_edit_v27', before, core._v18_entry_snap(entry))
            core.db.session.commit()
            flash('Entry saved; amount recalculated and editable invoice drafts synchronized.')
            return redirect(back)
        except (ValueError, InvalidOperation, KeyError, TypeError) as exc:
            core.db.session.rollback()
            flash('Entry not saved: ' + str(exc))
        except Exception:
            core.db.session.rollback()
            app.logger.exception('V27 entry save failed')
            flash('Entry not saved. Check server logs.')
    return render_template_string(previous.EDIT_TEMPLATE, entry=entry,
                                  users=core.User.query.order_by(core.User.display_name).all(), back=back)

app.view_functions['admin_entry_edit_v25'] = admin_entry_edit_v27
