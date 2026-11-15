"""MARS-IP V22: remove failed synthetic scrollbars and fit Entries tables to viewport.
No database, billing, invoice, authentication, or timer logic is changed.
"""
from pathlib import Path
import re, shutil, py_compile, sys

p = Path(sys.argv[1] if len(sys.argv) > 1 else 'app.py')
if not p.is_file():
    raise SystemExit(f'Missing {p}')

s = p.read_text(encoding='utf-8')
marker = '# MARSIP V22 ENTRIES FIT VIEWPORT'
if marker in s:
    print('V22 already installed.')
    raise SystemExit(0)

# Disable every earlier experimental screen-bottom scrollbar injection.
old_handlers = [
    'viewport_horizontal_scroller_v19',
    'viewport_horizontal_scroller_v19_fixed',
    'entries_viewport_scrollbar_v20',
    'marsip_entries_scroller_v21',
]
for name in old_handlers:
    signature = f'def {name}(response):\n'
    s = s.replace(signature, signature + '    return response\n', 1)

patch = r"""

# MARSIP V22 ENTRIES FIT VIEWPORT
# Root-cause correction: the Entries table is constrained to the visible page width,
# while long Matter and Services content wraps between complete words.
@app.after_request
def marsip_entries_fit_viewport_v22(response):
    if (
        request.endpoint not in ('entries', 'entries_v14', 'billing_control_v18')
        or response.status_code != 200
        or 'text/html' not in response.headers.get('Content-Type', '')
    ):
        return response

    html = response.get_data(as_text=True)
    if 'marsip-entries-fit-v22' in html:
        return response

    addition = r'''<style id="marsip-entries-fit-v22">
/* Remove synthetic fixed scrollbars from previous experiments. */
#marsip-v19-scrollbar,
#marsip-entry-scrollbar,
#marsip-v21-bar,
#marsip-scroll-v20-bar {
    display: none !important;
}

html,
body {
    width: 100% !important;
    max-width: 100% !important;
    overflow-x: hidden !important;
}

main,
.card,
.card.wide,
.table-scroll,
.marsip-v21-viewport {
    width: 100% !important;
    max-width: 100% !important;
    min-width: 0 !important;
    overflow-x: visible !important;
    box-sizing: border-box !important;
}

/* Every Entries table must remain inside the visible page. */
table.marsip-entries-v22 {
    width: 100% !important;
    max-width: 100% !important;
    min-width: 0 !important;
    table-layout: fixed !important;
    border-collapse: collapse !important;
}

table.marsip-entries-v22 th,
table.marsip-entries-v22 td {
    min-width: 0 !important;
    max-width: none !important;
    white-space: normal !important;
    word-break: normal !important;
    overflow-wrap: break-word !important;
    vertical-align: top !important;
    box-sizing: border-box !important;
}

table.marsip-entries-v22 th {
    overflow-wrap: normal !important;
    hyphens: none !important;
}

/* Compact descriptive columns. They use height instead of excessive width. */
table.marsip-entries-v22 .v22-matter { width: 15% !important; }
table.marsip-entries-v22 .v22-services { width: 25% !important; }
table.marsip-entries-v22 .v22-client { width: 13% !important; }
table.marsip-entries-v22 .v22-date { width: 9% !important; }
table.marsip-entries-v22 .v22-user { width: 9% !important; }
table.marsip-entries-v22 .v22-status { width: 7% !important; }
table.marsip-entries-v22 .v22-number { width: 7% !important; }
table.marsip-entries-v22 .v22-invoice { width: 9% !important; }
table.marsip-entries-v22 .v22-actions { width: 10% !important; }

table.marsip-entries-v22 input,
table.marsip-entries-v22 select,
table.marsip-entries-v22 textarea,
table.marsip-entries-v22 button {
    max-width: 100% !important;
    box-sizing: border-box !important;
}

table.marsip-entries-v22 textarea {
    width: 100% !important;
    min-width: 0 !important;
    min-height: 105px !important;
    resize: vertical !important;
    white-space: pre-wrap !important;
    word-break: normal !important;
    overflow-wrap: break-word !important;
}

@media (max-width: 1100px) {
    table.marsip-entries-v22 { font-size: 13px !important; }
    table.marsip-entries-v22 th,
    table.marsip-entries-v22 td { padding: 6px 5px !important; }
}
</style>
<script id="marsip-entries-fit-script-v22">
(() => {
  function classify(text) {
    const h = text.trim().toLowerCase();
    if (h === 'matter') return 'v22-matter';
    if (h === 'services' || h === 'service' || h === 'service description') return 'v22-services';
    if (h === 'client') return 'v22-client';
    if (h === 'date' || h === 'service date' || h === 'start date/time') return 'v22-date';
    if (h === 'user' || h === 'timekeeper') return 'v22-user';
    if (h === 'status') return 'v22-status';
    if (['worked','billable','hours','billable hours','rate','amount'].includes(h)) return 'v22-number';
    if (h.includes('invoice')) return 'v22-invoice';
    if (h === 'actions' || h === 'action') return 'v22-actions';
    return '';
  }

  document.querySelectorAll('table').forEach(table => {
    const headerRow = [...table.querySelectorAll('tr')].find(row => row.querySelector('th'));
    if (!headerRow) return;
    const headers = [...headerRow.children].filter(el => el.tagName === 'TH');
    const labels = headers.map(th => th.textContent.trim().toLowerCase());
    if (!labels.includes('matter') || !(labels.includes('services') || labels.includes('service') || labels.includes('service description'))) return;

    table.classList.add('marsip-entries-v22');
    const classes = headers.map(th => {
      const cls = classify(th.textContent);
      if (cls) th.classList.add(cls);
      return cls;
    });

    table.querySelectorAll('tr').forEach(row => {
      const cells = [...row.children].filter(el => el.tagName === 'TD' || el.tagName === 'TH');
      classes.forEach((cls, index) => {
        if (cls && cells[index]) cells[index].classList.add(cls);
      });
    });
  });
})();
</script>'''

    html = html.replace('</body>', addition + '</body>', 1) if '</body>' in html else html + addition
    response.set_data(html)
    response.headers['Content-Length'] = len(response.get_data())
    return response
"""

backup = p.with_name(p.name + '.before_v22_fit_screen.bak')
shutil.copy2(p, backup)
p.write_text(s.rstrip() + patch + '\n', encoding='utf-8')
try:
    py_compile.compile(str(p), doraise=True)
except Exception:
    shutil.copy2(backup, p)
    raise
print('V22 installed. Rollback:', backup)
