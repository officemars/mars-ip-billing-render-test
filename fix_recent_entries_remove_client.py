from pathlib import Path
import py_compile, shutil, sys

p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
if not p.exists(): raise SystemExit(f'Missing {p}')
s=p.read_text(encoding='utf-8')
marker='# ==================== RECENT ENTRIES CLIENT COLUMN TEMPLATE FIX ===================='
if marker in s:
    print('Patch already installed.'); raise SystemExit(0)
bak=p.with_name(p.name+'.before_recent_client_template_fix.bak')
shutil.copy2(p,bak)

patch=r'''

# ==================== RECENT ENTRIES CLIENT COLUMN TEMPLATE FIX ====================
# Modify the Jinja timer template itself BEFORE rendering. This keeps Date aligned and
# removes only the Client header/cell from Recent Entries.
def _remove_recent_client_from_timer_template_final(template):
    if not template:
        return template
    # Header: exact Recent Entries header sequence used by the consolidated timer template.
    template=template.replace(
        '<th>Date</th><th>Client</th><th>Matter</th>',
        '<th>Date</th><th>Matter</th>',
        1
    )
    # Body: remove only the client cell that immediately follows the editable Date cell.
    template=template.replace(
        '<td><input type="date" name="entry_date" value="{{t.start_time.strftime(\'%Y-%m-%d\')}}" required></td><td>{{t.client.name}}</td><td>{{t.matter.name}}</td>',
        '<td><input type="date" name="entry_date" value="{{t.start_time.strftime(\'%Y-%m-%d\')}}" required></td><td>{{t.matter.name}}</td>',
        1
    )
    return template

if 'TIMER_PAGE_RUNTIME_V15' in globals():
    TIMER_PAGE_RUNTIME_V15=_remove_recent_client_from_timer_template_final(TIMER_PAGE_RUNTIME_V15)
if 'TIMER_PAGE_V11' in globals():
    TIMER_PAGE_V11=_remove_recent_client_from_timer_template_final(TIMER_PAGE_V11)
if 'TIMER_PAGE_V101' in globals():
    TIMER_PAGE_V101=_remove_recent_client_from_timer_template_final(TIMER_PAGE_V101)
'''

p.write_text(s.rstrip()+patch+'\n',encoding='utf-8')
try:
    py_compile.compile(str(p),doraise=True)
except Exception:
    shutil.copy2(bak,p); raise
print('Recent Entries Client column template fix installed.')
print('Backup:',bak)
print('Syntax validation passed.')
