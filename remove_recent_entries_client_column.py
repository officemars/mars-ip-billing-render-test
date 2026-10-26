from pathlib import Path
import py_compile, shutil, sys

p = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
if not p.exists():
    raise SystemExit(f'Missing {p}')
source = p.read_text(encoding='utf-8')
marker = '# ==================== RECENT ENTRIES REMOVE CLIENT COLUMN FINAL ===================='
if marker in source:
    print('Patch already installed.')
    raise SystemExit(0)
backup = p.with_name(p.name + '.before_remove_recent_client.bak')
shutil.copy2(p, backup)

patch = r'''

# ==================== RECENT ENTRIES REMOVE CLIENT COLUMN FINAL ====================
# Template-level removal only. Do not hide columns with nth-child CSS because the row
# contains forms and that can misalign Date/Client cells.
@app.after_request
def remove_recent_entries_client_column_final(response):
    if request.endpoint == 'timer' and response.status_code == 200 and 'text/html' in response.headers.get('Content-Type',''):
        html = response.get_data(as_text=True)
        # Scope strictly to the Recent entries section.
        start = html.find('<h2>Recent entries</h2>')
        if start >= 0:
            end = html.find('</section>', start)
            if end >= 0:
                section = html[start:end]
                # Remove Client header only from this table.
                section = section.replace('<th>Client</th>', '', 1)
                # Remove the client data cell for each recent-entry row, preserving Date and Matter.
                section = section.replace('<td>{{t.client.name}}</td>', '')
                html = html[:start] + section + html[end:]
                response.set_data(html)
                response.headers['Content-Length'] = len(response.get_data())
    return response
'''

p.write_text(source.rstrip() + patch + '\n', encoding='utf-8')
try:
    py_compile.compile(str(p), doraise=True)
except Exception:
    shutil.copy2(backup, p)
    raise
print('Recent Entries Client column removal installed.')
print('Backup:', backup)
print('Syntax validation passed.')
