"""Replace only the previous V19 viewport scrollbar with a reliable Entries scrollbar.
Preserves all other application logic and creates a rollback copy.
"""
from pathlib import Path
import py_compile, shutil, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
s=p.read_text(encoding='utf-8')
start='# MARSIP V19 SINGLE PAGE INVOICE AND VIEWPORT SCROLLER'
end='# MARSIP V19 ENTRIES VISIBLE SCROLLER FIX'
if end in s:
    print('Already installed');raise SystemExit(0)
if start not in s:
    raise SystemExit('Previous V19 layout section not found. No changes made.')
# Keep invoice standalone-page redirect and other V19 functionality; replace only its after_request scrollbar function.
idx=s.find('@app.after_request\ndef viewport_horizontal_scroller_v19',s.index(start))
if idx<0:raise SystemExit('Previous scrollbar function not found. No changes made.')
# Previous layout patch is the final block. Fail safely if other code follows.
old=s[idx:]
if 'return response' not in old or not old.rstrip().endswith('return response'):
    raise SystemExit('Unexpected file layout. No changes made.')
replacement=r"""
# MARSIP V19 ENTRIES VISIBLE SCROLLER FIX
# A single viewport-bottom bar controls the widest visible entries table container.
@app.after_request
def viewport_horizontal_scroller_v19_fixed(response):
    if response.status_code!=200 or 'text/html' not in response.headers.get('Content-Type',''):
        return response
    if request.endpoint not in ('entries','entries_v14','billing_control_v18'):
        return response
    html=response.get_data(as_text=True)
    if 'marsip-fixed-entry-scroller-v19' in html:return response
    addition=r'''<style id="marsip-fixed-entry-scroller-v19">
#marsip-entry-scrollbar{position:fixed;left:0;right:0;bottom:0;height:22px;z-index:2147483000;overflow-x:auto;overflow-y:hidden;background:#e7ebf0;border-top:1px solid #b7c2cd;display:none}
#marsip-entry-scrollbar-inner{height:1px;pointer-events:none}
body{padding-bottom:28px!important}
</style>
<div id="marsip-entry-scrollbar" aria-label="Horizontal entries navigation"><div id="marsip-entry-scrollbar-inner"></div></div>
<script id="marsip-fixed-entry-scroller-script-v19">
(function(){
 const bar=document.getElementById('marsip-entry-scrollbar'),inside=document.getElementById('marsip-entry-scrollbar-inner');
 if(!bar||!inside)return;
 let target=null,lock=false;
 function refresh(){
   const tables=[...document.querySelectorAll('table')];
   const options=[];
   for(const table of tables){
     let e=table;
     while(e&&e!==document.body){
       if(e.scrollWidth>e.clientWidth+8){
         const st=getComputedStyle(e);
         if(/auto|scroll/.test(st.overflowX)){options.push({element:e,table:table});break}
       }
       e=e.parentElement;
     }
   }
   if(!options.length){bar.style.display='none';target=null;return}
   const visible=options.filter(x=>{const r=x.table.getBoundingClientRect();return r.top<innerHeight-25&&r.bottom>30});
   const chosen=(visible.length?visible:options)[0].element;
   target=chosen;
   bar.style.display='block';inside.style.width=Math.max(chosen.scrollWidth,chosen.clientWidth+1)+'px';
   lock=true;bar.scrollLeft=chosen.scrollLeft;lock=false;
 }
 bar.addEventListener('scroll',()=>{if(target&&!lock){lock=true;target.scrollLeft=bar.scrollLeft;lock=false}});
 document.addEventListener('scroll',e=>{if(target&&e.target===target&&!lock){lock=true;bar.scrollLeft=target.scrollLeft;lock=false}},true);
 let pending=false;
 function schedule(){if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;refresh()})}
 window.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule);
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',schedule);else schedule();
})();
</script>'''
    html=html.replace('</body>',addition+'</body>',1) if '</body>' in html else html+addition
    response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
"""
bak=p.with_name(p.name+'.before_entries_scroller_fix.bak')
shutil.copy2(p,bak)
p.write_text(s[:idx]+replacement,encoding='utf-8')
try:py_compile.compile(str(p),doraise=True)
except Exception:shutil.copy2(bak,p);raise
print('Entries scrollbar fix installed. Backup:',bak)
