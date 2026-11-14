"""Replace the V19 scrollbar implementation with a viewport-bottom control.
Does not modify database, billing, invoice or authentication logic.
"""
from pathlib import Path
import py_compile, shutil, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
s=p.read_text(encoding='utf-8')
marker='# MARSIP V20 VISIBLE ENTRIES SCROLLBAR'
if marker in s:
    print('V20 already installed');raise SystemExit(0)
# Disable earlier conflicting viewport scrollbar hooks only in their after_request
# function bodies by changing their endpoint checks to an impossible endpoint.
for name in ('viewport_horizontal_scroller_v19','viewport_horizontal_scroller_v19_fixed'):
    signature='def '+name+'(response):'
    at=s.find(signature)
    if at>=0:
        end=s.find('\n@app.',at+len(signature))
        if end<0:end=len(s)
        block=s[at:end]
        if name=='viewport_horizontal_scroller_v19':
            # original hook had no endpoint guard: add early return
            block=block.replace(signature,signature+'\n    return response',1)
        else:
            block=block.replace(signature,signature+'\n    return response',1)
        s=s[:at]+block+s[end:]
patch=r"""

# MARSIP V20 VISIBLE ENTRIES SCROLLBAR
@app.after_request
def entries_viewport_scrollbar_v20(response):
    if request.endpoint not in ('entries','entries_v14','billing_control_v18') or response.status_code!=200 or 'text/html' not in response.headers.get('Content-Type',''):
        return response
    html=response.get_data(as_text=True)
    if 'marsip-scroll-v20' in html:return response
    widget=r'''<style id="marsip-scroll-v20">
#marsip-scroll-v20-bar{position:fixed!important;bottom:0!important;left:0!important;right:0!important;height:24px!important;overflow-x:scroll!important;overflow-y:hidden!important;z-index:2147483640!important;background:#e4e9ef!important;border-top:2px solid #9daab9!important;display:none;box-sizing:border-box}
#marsip-scroll-v20-track{height:1px}
body{padding-bottom:30px!important}
</style>
<div id="marsip-scroll-v20-bar" aria-label="Scroll entries horizontally"><div id="marsip-scroll-v20-track"></div></div>
<script id="marsip-scroll-v20-script">
(()=>{
const bar=document.getElementById('marsip-scroll-v20-bar'),track=document.getElementById('marsip-scroll-v20-track');
if(!bar||!track)return;
let target=null,updating=false;
function scrollerFor(table){
 let node=table.parentElement;
 while(node&&node!==document.documentElement){
   const cs=getComputedStyle(node);
   if(node.scrollWidth>node.clientWidth+5&&/(auto|scroll)/.test(cs.overflowX))return node;
   node=node.parentElement;
 }
 const root=document.scrollingElement||document.documentElement;
 return root.scrollWidth>root.clientWidth+5?root:null;
}
function update(){
 const tables=[...document.querySelectorAll('table')];
 const matches=tables.map(t=>({table:t,scroller:scrollerFor(t)})).filter(x=>x.scroller);
 const visible=matches.filter(x=>{let r=x.table.getBoundingClientRect();return r.bottom>40&&r.top<innerHeight-24});
 const selected=(visible.length?visible:matches)[0];
 if(!selected){bar.style.display='none';target=null;return;}
 target=selected.scroller;
 bar.style.display='block';
 track.style.width=Math.max(target.scrollWidth,bar.clientWidth+1)+'px';
 updating=true;bar.scrollLeft=target.scrollLeft;updating=false;
}
bar.addEventListener('scroll',()=>{if(!updating&&target){updating=true;target.scrollLeft=bar.scrollLeft;updating=false}});
document.addEventListener('scroll',e=>{if(target&&(e.target===target||e.target===document)&&!updating){updating=true;bar.scrollLeft=target.scrollLeft;updating=false}},true);
let scheduled=false;
function schedule(){if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;update()})}
window.addEventListener('scroll',schedule,{passive:true});window.addEventListener('resize',schedule);
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',schedule);else schedule();
})();
</script>'''
    html=html.replace('</body>',widget+'</body>',1) if '</body>' in html else html+widget
    response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
"""
bak=p.with_name(p.name+'.before_scroll_v20.bak')
shutil.copy2(p,bak)
p.write_text(s.rstrip()+patch+'\n',encoding='utf-8')
try:py_compile.compile(str(p),doraise=True)
except Exception:shutil.copy2(bak,p);raise
print('V20 installed. Backup:',bak)
