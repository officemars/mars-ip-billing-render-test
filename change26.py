"""MARS-IP V26: preserve original Entries layout, add reliable Edit links.
Uses the V25 editor and updates only the existing invoice-status cell in the
original V317 Entries template. Does not replace the Entries route or table.
Gunicorn entrypoint: change26:app
"""
import app as core
import change25 as editor

app = core.app
_original_entries_template = core._entries_template_service_date_final

# Exact markup from ENTRIES_V317, as confirmed on OVH.
_needle = '''{{invoice_refs.get(t.id,'Unbilled')}}</span></td></tr>'''
_replacement = '''{{invoice_refs.get(t.id,'Unbilled')}}</span>{% if session.get('role') == 'Administrator' %}<br><a class="marsip-edit-v26" href="{{ url_for('admin_entry_edit_v25', timer_id=t.id, back=request.full_path) }}" style="display:inline-block;margin-top:5px;white-space:nowrap;font-size:0.88em">Edit</a>{% endif %}</td></tr>'''

def _entries_template_with_edit_v26():
    original = _original_entries_template()
    if _needle not in original:
        app.logger.error('V26: Entries template marker missing; original table preserved')
        return original
    return original.replace(_needle, _replacement, 1)

core._entries_template_service_date_final = _entries_template_with_edit_v26
