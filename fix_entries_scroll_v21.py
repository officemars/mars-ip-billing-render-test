"""Replace all previous viewport scrollbar handlers with a single Entries table controller.
Does not alter data or invoice logic. Requires current V19/V20 app.py.
"""
from pathlib import Path
import re, shutil, py_compile, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
s=p.read_text(encoding='utf-8')
marker='# MARSIP V21 SINGLE TABLE SCROLLER'
if marker in s:raise SystemExit('Already installed')
# Turn off old scrollbar injection hooks, preserving their surrounding code.
for name in ['viewport_horizontal_scroller_v19','viewport_horizontal_scroller_v19_fixed','entries_viewport_scrollbar_v20']:
    pattern=r'(def '+re.escape(name)+r'\(response\):\n)'
    s,n=re.subn(pattern,r'\1    return response\n',s,count=1)
# Need at least one prior version, otherwise refuse instead of guessing.
if not any('def '+x+'(response):' in s for x in ['viewport_horizontal_scroller_v19','viewport_horizontal_scroller_v19_fixed','entries_viewport_scrollbar_v20']):
    raise SystemExit('Existing scrollbar implementation not found. No changes made.')
patch=r"""

# MARSIP V21 SINGLE TABLE SCROLLER
@app.after_request
def marsip_entries_scroller_v21(response):
    if request.endpoint not in ('entries','entries_v14','billing_control_v18') or response.status_code!=200 or 'text/html' not in response.headers.get('Content-Type',''):
        return response
    html=response.get_data(as_text=True)
    if 'marsip-scroll-v21' in html:return response
    addon=r'''<style id="marsip-scroll-v21">
#marsip-v21-bar{position:fixed!important;left:0!important;right:0!important;bottom:0!important;height:25px!important;overflow-x:auto!important;overflow-y:hidden!important;z-index:2147483640!important;background:#e8edf3!important;border-top:2px solid #a3b1c0!important;box-sizing:border-box!important}
#marsip-v21-track{height:1px!important}
body{padding-bottom:32px!important}
.marsip-v21-viewport{width:100%!important;max-width:100%!important;overflow-x:auto!important;box-sizing:border-box!important}
.marsip-v21-viewport>table{width:max-content!important;min-width:100%!important;max-width:none!important;table-layout:auto!important}
</style>
<div id="marsip-v21-bar" aria-label="Scroll time entries horizontally"><div id="marsip-v21-track"></div></div>
<script id="marsip-v21-script">
(()=>{
const bar=document.getElementById('marsip-v21-bar'),track=document.getElementById('marsip-v21-track');
if(!bar||!track)return;
let viewport=null,lock=false;
function setup(){
  const tables=[...document.querySelectorAll('table')];
  const t=tables.find(t=>{const text=[...t.querySelectorAll('th')].map(h=>h.textContent.trim().toLowerCase());return text.includes('matter')&&(text.includes('services')||text.includes('service description'))})||tables[0];
  if(!t){bar.style.display='none';return}
  // Wrap the actual table so the same element owns its full horizontal range.
  let wrap=t.parentElement;
  if(!wrap.classList.contains('marsip-v21-viewport')){
    wrap=document.createElement('div');wrap.className='marsip-v21-viewport';t.parentNode.insertBefore(wrap,t);wrap.appendChild(t);
  }
  viewport=wrap;
  const width=Math.ceil(Math.max(t.scrollWidth,t.getBoundingClientRect().width));
  track.style.width=Math.max(width,bar.clientWidth+1)+'px';
  bar.style.display=width>wrap.clientWidth+2?'block':'none';
  lock=true;bar.scrollLeft=wrap.scrollLeft;lock=false;
}
bar.addEventListener('scroll',()=>{if(viewport&&!lock){lock=true;viewport.scrollLeft=bar.scrollLeft;lock=false}});
document.addEventListener('scroll',e=>{if(e.target===viewport&&!lock){lock=true;bar.scrollLeft=viewport.scrollLeft;lock=false}},true);
let pending=false;function update(){if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;setup()})}
window.addEventListener('resize',update);if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',update);else update();
})();
</script>'''
    html=html.replace('</body>',addon+'</body>',1) if '</body>' in html else html+addon
    response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
"""
bak=p.with_name(p.name+'.before_v21_scroll.bak');shutil.copy2(p,bak)
p.write_text(s.rstrip()+patch+'\n',encoding='utf-8')
try:py_compile.compile(str(p),doraise=True)
except Exception:shutil.copy2(bak,p);raise
print('V21 installed; rollback:',bak)
