"""Safe, additive presentation update for MARS-IP current app.py.
Does not change invoice records, time entries, database schema or permissions.
"""
from pathlib import Path
import shutil, py_compile, sys
p=Path(sys.argv[1] if len(sys.argv)>1 else 'app.py')
marker='# MARSIP V19 SAFE CHRONOLOGY AND SELECTION'
s=p.read_text(encoding='utf-8')
if marker in s:
    print('Already applied');raise SystemExit(0)
patch=r"""

# MARSIP V19 SAFE CHRONOLOGY AND SELECTION
# UI-only; does not change billing state, invoice associations or database rows.
@app.after_request
def marsip_v19_chronology_selection(response):
    if response.status_code != 200 or 'text/html' not in response.headers.get('Content-Type',''):
        return response
    if request.endpoint not in ('timer','entries','entries_v14','billing_control_v18'):
        return response
    html=response.get_data(as_text=True)
    if 'marsip-v19-safe' in html:return response
    addition=r'''<style id="marsip-v19-safe">
.marsip-v19-selection{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:12px;margin:10px 0;border:1px solid #ccd5e0;border-radius:8px}
.marsip-v19-selection strong{font-variant-numeric:tabular-nums}
.marsip-v19-selection button{width:auto!important}
.marsip-v19-check{width:18px;height:18px;min-width:18px}
.marsip-v19-selected{background:rgba(35,120,190,.07)}
</style>
<script id="marsip-v19-safe-script">
(function(){
 function num(v){const n=Number(String(v||'').trim().replace(/[^0-9.,-]/g,'').replace(',','.'));return Number.isFinite(n)?n:0}
 function dateValue(v){let s=String(v||'').trim();let m=s.match(/(\d{4})-(\d{2})-(\d{2})/);if(m)return m[1]+m[2]+m[3];m=s.match(/(\d{2})[./](\d{2})[./](\d{4})/);if(m)return m[3]+m[2]+m[1];return ''}
 function headings(t){return Array.from(t.querySelectorAll('tr')).find(r=>r.querySelector('th'))}
 function cellValue(cell){if(!cell)return '';const field=cell.querySelector('input[type=date],input[type=datetime-local]');return field?field.value:cell.textContent}
 function setup(table){
   const header=headings(table);if(!header)return;
   const heads=Array.from(header.children).filter(x=>x.tagName==='TH').map(x=>x.textContent.trim().toLowerCase());
   const di=heads.findIndex(x=>x==='date'||x==='service date'||x==='start date/time');
   if(di<0)return;
   const rows=Array.from(table.querySelectorAll('tr')).filter(r=>r!==header&&r.querySelector('td'));
   const cells=r=>Array.from(r.children).filter(x=>x.tagName==='TD');
   // Sort rendered records by their actual displayed service date, not insertion order.
   const sorted=rows.map((r,i)=>({r,i,key:dateValue(cellValue(cells(r)[di]))})).sort((a,b)=>b.key.localeCompare(a.key)||a.i-b.i);
   const parent=rows[0]&&rows[0].parentNode;
   if(parent&&sorted.every(x=>x.r.parentNode===parent))sorted.forEach(x=>parent.appendChild(x.r));
   if(document.body.dataset.marsipV19Select!=='1')return;
   const hi=heads.findIndex(x=>x==='billable'||x==='billable hours'||x==='billable h'||x==='hours');
   const ai=heads.findIndex(x=>x==='amount'||x==='net amount'||x==='revenue');
   if(hi<0||ai<0||!rows.length)return;
   const panel=document.createElement('div');panel.className='marsip-v19-selection';
   panel.innerHTML='<strong>Selected: <span data-count>0</span> entries</strong><strong>Hours: <span data-hours>0.0000</span></strong><strong>Amount: <span data-amount>0.00</span></strong><button type="button" data-all>Select all</button><button type="button" data-none>Deselect all</button><small>Selection is for analysis only. It does not bill or deactivate entries.</small>';
   table.parentNode.insertBefore(panel,table);
   const chosen=[];
   rows.forEach(r=>{const c=cells(r);if(c.length<=Math.max(di,hi,ai))return;
      const box=document.createElement('input');box.type='checkbox';box.checked=true;box.className='marsip-v19-check';box.setAttribute('aria-label','Include entry in selected totals');
      c[di].insertBefore(box,c[di].firstChild);chosen.push({r,box,c});box.addEventListener('change',recalc)
   });
   function recalc(){let h=0,a=0,n=0;chosen.forEach(x=>{x.r.classList.toggle('marsip-v19-selected',x.box.checked);if(!x.box.checked)return;n++;h+=num(cellValue(x.c[hi]));a+=num(cellValue(x.c[ai]))});panel.querySelector('[data-count]').textContent=n;panel.querySelector('[data-hours]').textContent=h.toFixed(4);panel.querySelector('[data-amount]').textContent=a.toFixed(2)}
   panel.querySelector('[data-all]').addEventListener('click',()=>{chosen.forEach(x=>x.box.checked=true);recalc()});
   panel.querySelector('[data-none]').addEventListener('click',()=>{chosen.forEach(x=>x.box.checked=false);recalc()});recalc();
 }
 document.addEventListener('DOMContentLoaded',()=>{
   document.body.dataset.marsipV19Select=(location.pathname==='/entries-v14'||location.pathname==='/admin/billing-control-v18')?'1':'0';
   document.querySelectorAll('table').forEach(setup);
 });
})();
</script>'''
    html=html.replace('</body>',addition+'</body>',1) if '</body>' in html else html+addition
    response.set_data(html);response.headers['Content-Length']=len(response.get_data())
    return response
"""
backup=p.with_name(p.name+'.before_v19_safe.bak')
shutil.copy2(p,backup)
p.write_text(s.rstrip()+patch+'\n',encoding='utf-8')
try:py_compile.compile(str(p),doraise=True)
except Exception:
    shutil.copy2(backup,p)
    raise
print('Installed presentation-only V19. Backup:',backup)
