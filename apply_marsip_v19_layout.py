"""MARS-IP layout fix: single-page invoice editor and visible bottom-of-screen scrollbar.
Run against current production app.py. No database modifications.
"""
from pathlib import Path
import shutil, py_compile, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
if not p.is_file(): raise SystemExit('app.py not found')
s=p.read_text(encoding='utf-8')
marker='# MARSIP V19 SINGLE PAGE INVOICE AND VIEWPORT SCROLLER'
if marker in s: raise SystemExit('Already applied')
patch = r"""

# MARSIP V19 SINGLE PAGE INVOICE AND VIEWPORT SCROLLER
# Presentation only; existing invoice routes, saves and database untouched.
# Full invoice editor is the sole editor instead of an iframe within another editor.
_old_edit_fee_note_single_page_v19 = app.view_functions.get('edit_fee_note')
if _old_edit_fee_note_single_page_v19:
    def edit_fee_note_single_page_v19(draft_id):
        return redirect(url_for('edit_fee_note_original_v4', draft_id=draft_id))
    app.view_functions['edit_fee_note'] = login_required(admin_required(edit_fee_note_single_page_v19))

@app.after_request
def viewport_horizontal_scroller_v19(response):
    if response.status_code != 200 or 'text/html' not in response.headers.get('Content-Type',''):
        return response
    html = response.get_data(as_text=True)
    if 'marsip-viewport-scroll-v19' in html:
        return response
    addition = r'''<style id="marsip-viewport-scroll-v19">
/* One horizontal scrollbar anchored to the visible screen bottom. */
#marsip-v19-scrollbar{position:fixed;bottom:0;left:0;right:0;height:19px;overflow-x:auto;overflow-y:hidden;background:#f3f5f8;border-top:1px solid #d4d8e0;z-index:9999;display:none}
#marsip-v19-scrollbar-inner{height:1px}
body{padding-bottom:25px!important}
.card.wide,.table-scroll{max-width:100%;overflow-x:auto}
</style>
<div id="marsip-v19-scrollbar" role="scrollbar" aria-label="Horizontal page navigation"><div id="marsip-v19-scrollbar-inner"></div></div>
<script id="marsip-v19-scroll-script">
(function(){
 const bar=document.getElementById('marsip-v19-scrollbar'),inner=document.getElementById('marsip-v19-scrollbar-inner');
 if(!bar||!inner)return;
 let target=null,busy=false;
 function candidates(){return Array.from(document.querySelectorAll('.table-scroll,.card.wide,main,body')).filter(e=>e!==bar&&e.scrollWidth>e.clientWidth+8)}
 function choose(){
   const items=candidates().filter(e=>{const r=e.getBoundingClientRect();return r.top<innerHeight-25&&r.bottom>40});
   // Prefer the wide table/card currently visible, not a hidden nested scroller.
   target=items.find(e=>e.matches('.table-scroll,.card.wide'))||items[0]||null;
   if(!target){bar.style.display='none';return}
   bar.style.display='block';inner.style.width=target.scrollWidth+'px';
   busy=true;bar.scrollLeft=target.scrollLeft;busy=false;
 }
 bar.addEventListener('scroll',()=>{if(!busy&&target){busy=true;target.scrollLeft=bar.scrollLeft;busy=false}});
 document.addEventListener('scroll',()=>{if(target&&!busy){busy=true;bar.scrollLeft=target.scrollLeft;busy=false}},true);
 let scheduled=false;
 function update(){if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;choose()})}
 window.addEventListener('scroll',update,{passive:true});window.addEventListener('resize',update);
 document.addEventListener('DOMContentLoaded',update);
 update();
})();
</script>'''
    html=html.replace('</body>',addition+'</body>',1) if '</body>' in html else html+addition
    response.set_data(html)
    response.headers['Content-Length']=len(response.get_data())
    return response
"""
bak=p.with_name(p.name+'.before_v19_layout.bak')
shutil.copy2(p,bak)
p.write_text(s.rstrip()+patch+'\n',encoding='utf-8')
try:py_compile.compile(str(p),doraise=True)
except Exception:
    shutil.copy2(bak,p)
    raise
print('Layout patch installed; backup:',bak)
