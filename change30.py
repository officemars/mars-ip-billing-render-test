"""MARS-IP V30: Entries actions + client-scoped matter dropdown.
Builds on change29. Uses existing audited V18 action handler, without new
hard-delete logic or changes to the database schema. Gunicorn: change30:app.
"""
import json
from markupsafe import Markup
from flask import request, session
import app as core
import change29 as previous

app = previous.app

# The V18 action route already implements deactivate, reactivate, restore
# unbilled and guarded delete, including audit and issued-invoice checks.
assert 'billing_entry_action_v18' in app.view_functions, 'V18 entry action route missing'
assert hasattr(core, '_entries_template_service_date_final'), 'Entries template helper missing'

_original_template = core._entries_template_service_date_final
_needle = "{{invoice_refs.get(t.id,'Unbilled')}}</span></td></tr>"
# The original Entries page remains intact. V27 already inserts an Edit link.
# Add actions before the invoice cell closes, without adding any table columns.
_needle_with_edit = "</a>{% endif %}</td></tr>"

def entry_active_v30(timer_id):
    return core._v18_active(timer_id)

app.jinja_env.globals['entry_active_v30'] = entry_active_v30

_actions = '''<div class="marsip-v30-actions" style="display:flex;gap:5px;flex-wrap:wrap;margin-top:5px">
<form method="post" action="{{url_for('billing_entry_action_v18',timer_id=t.id)}}" style="display:inline">
{% if entry_active_v30(t.id) %}
<button type="submit" name="action" value="deactivate" class="secondary" style="padding:3px 6px;font-size:12px" onclick="return confirm('Deactivate this entry? It will be excluded from future invoice selection.')">Deactivate</button>
{% else %}
<strong style="color:#9b4b00">Inactive</strong>
<button type="submit" name="action" value="reactivate" class="secondary" style="padding:3px 6px;font-size:12px">Reactivate</button>
{% endif %}
</form>
<form method="post" action="{{url_for('billing_entry_action_v18',timer_id=t.id)}}" style="display:inline">
<button type="submit" name="action" value="delete" class="danger" style="padding:3px 6px;font-size:12px" onclick="return confirm('Permanently delete this entry? This cannot be undone. Entries linked to issued invoices cannot be deleted.')">Delete</button>
</form>
<form method="post" action="{{url_for('billing_entry_action_v18',timer_id=t.id)}}" style="display:inline">
<button type="submit" name="action" value="unbill" class="secondary" style="padding:3px 6px;font-size:12px" onclick="return confirm('Restore this entry to Unbilled? Issued invoice dependencies will block this action.')">Restore unbilled</button>
</form></div>'''

def _entries_template_v30():
    template = _original_template()
    if 'marsip-v30-actions' in template:
        return template
    # Works with the known V27 Edit link, regardless of its style attributes.
    marker = "{% endif %}</td></tr>"
    invoice_pos = template.find("invoice_refs.get(t.id")
    if invoice_pos < 0:
        app.logger.error('V30: invoice cell not found, preserving original Entries page')
        return template
    pos = template.find(marker, invoice_pos)
    if pos < 0:
        app.logger.error('V30: Edit link marker missing, preserving original Entries page')
        return template
    return template[:pos + len('{% endif %}')] + _actions + template[pos + len('{% endif %}'):]

core._entries_template_service_date_final = _entries_template_v30

@app.after_request
def filter_entries_matters_v30(response):
    if request.endpoint not in ('entries_v14', 'entries') or response.status_code != 200 or 'text/html' not in response.content_type:
        return response
    if session.get('role') != 'Administrator':
        return response
    html = response.get_data(as_text=True)
    if 'marsip-v30-matters' in html:
        return response
    matter_clients = {str(m.id): str(m.client_id) for m in core.Matter.query.all()}
    script = '''<script id="marsip-v30-matters">(function(){
const map=MATTER_MAP;
function setup(){
 const forms=Array.from(document.querySelectorAll('form'));
 forms.forEach(function(f){
  const client=f.querySelector('select[name="client_id"]'), matter=f.querySelector('select[name="matter_id"]');
  if(!client||!matter)return;
  function refresh(){
   const selected=client.value;
   Array.from(matter.options).forEach(function(o){
    if(!o.value){o.hidden=false;return;}
    o.hidden=!!selected&&map[o.value]!==selected;
   });
   if(matter.selectedOptions.length&&matter.selectedOptions[0].hidden)matter.value='';
  }
  client.addEventListener('change',refresh);refresh();
 });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();</script>'''.replace('MATTER_MAP', json.dumps(matter_clients))
    html = html.replace('</body>', script + '</body>', 1) if '</body>' in html else html + script
    response.set_data(html)
    response.headers['Content-Length'] = len(response.get_data())
    return response
