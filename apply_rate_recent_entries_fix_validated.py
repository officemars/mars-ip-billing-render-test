from pathlib import Path
import py_compile, shutil, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
if not p.exists(): raise SystemExit(f'Missing {p}')
s=p.read_text(encoding='utf-8'); marker='# ==================== CLIENT RATE DEFAULT + RECENT ENTRY WRAP FIX ===================='
if marker in s: print('Patch already installed.'); raise SystemExit(0)
bak=p.with_name(p.name+'.before_rate_recent_entries_fix.bak'); shutil.copy2(p,bak)
patch=r"""

# ==================== CLIENT RATE DEFAULT + RECENT ENTRY WRAP FIX ====================
# 1) Effective rate priority remains matter rate first; if a matter has no explicit
# rate, the linked client's hourly rate is the default before the user's standard rate.
def effective_rate_client_default_final(user, client, matter):
    if matter is not None and matter.matter_hourly_rate is not None:
        return float(matter.matter_hourly_rate)
    if client is not None and client.client_hourly_rate is not None:
        return float(client.client_hourly_rate)
    try:
        special = UserClientRateV11.query.filter_by(user_id=user.id, client_id=client.id).first() if client else None
        if special is not None:
            return float(special.hourly_rate)
    except Exception:
        pass
    return float(user.default_hourly_rate or 0)
effective_rate_v11 = effective_rate_client_default_final

# Keep new matters inheriting the client rate dynamically: no matter-specific rate is
# written unless the administrator explicitly enters one. Existing matter rates remain untouched.

# 2) Recent Entries: preserve headings as whole words, rename the heading, and allow
# body content to wrap at spaces so long descriptions don't force excessive lateral scrolling.
@app.after_request
def recent_entries_readability_final(response):
    if response.status_code==200 and 'text/html' in response.headers.get('Content-Type',''):
        html=response.get_data(as_text=True)
        if request.endpoint=='timer':
            html=html.replace('Override billable hours','Billable hours')
            if 'recent-entry-wrap-final' not in html:
                css=r'''<style id="recent-entry-wrap-final">
/* Recent-entry headers remain intact. */
.wide table th{white-space:nowrap!important;word-break:normal!important;overflow-wrap:normal!important}
/* Body cells may wrap between words, but never split an individual word. */
.wide table td{white-space:normal!important;word-break:normal!important;overflow-wrap:normal!important;vertical-align:top}
/* Description gets a useful bounded width and wraps naturally. */
.wide table td.service-column{min-width:260px!important;width:320px!important;max-width:420px!important;white-space:normal!important;word-break:normal!important;overflow-wrap:normal!important}
.wide table td.service-column textarea,.wide table td textarea[name="services"]{width:100%!important;min-width:240px!important;max-width:100%!important;white-space:pre-wrap!important;word-break:normal!important;overflow-wrap:normal!important;resize:vertical}
/* Short controls/statuses remain readable. */
.wide table td input[type="number"]{min-width:78px;max-width:105px}
.wide table td button,.wide table .invoice-tag,.wide table .billing-tag-v13{white-space:nowrap!important;word-break:normal!important;overflow-wrap:normal!important}
</style>'''
                html=html.replace('</head>',css+'</head>') if '</head>' in html else css+html
            response.set_data(html); response.headers['Content-Length']=len(response.get_data())
    return response
"""
p.write_text(s.rstrip()+patch+'\n',encoding='utf-8')
try: py_compile.compile(str(p),doraise=True)
except Exception: shutil.copy2(bak,p); raise
print('Client-rate default and Recent Entries readability patch installed.')
print('Backup:',bak)
print('Syntax validation passed.')
