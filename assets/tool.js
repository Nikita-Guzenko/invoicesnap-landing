/* InvoiceSnap free form (NIK-547, 8 Nov 2026). One document model feeds the live paper, PDF, Word and Excel.
   No libraries and no form data on the network: gate_browser.py fails a page if a typed value appears in any request.
   Country facts come from a law record (data/law/<locale>.json) in the page config, never from this file: tax modes,
   labels, notes, currency, date order, paper size, text direction. tests/fixtures holds a German and an Arabic record.
   PDF limit (8 Nov 2026): base Helvetica + WinAnsi covers Latin-1 only. Locales with other scripts or letters
   (ar, he, hi, ja, ko, th, zh, el, ru, uk, pl, cs, hr, hu, ro, sk, tr, vi) need an embedded font subset before launch.
   gate_browser.py blocks those pages, and the form warns a user who types such a letter on an English page. */
(function(){
'use strict';
var C=JSON.parse(document.getElementById('tool-cfg').textContent),L=C.law,U=L.ui,TAX=L.tax;
var $=function(s,r){return (r||document).querySelector(s)},$$=function(s,r){return [].slice.call((r||document).querySelectorAll(s))};
var S=JSON.parse(JSON.stringify(C.sample));
var money=new Intl.NumberFormat(L.locale,{style:'currency',currency:L.currency});
var num=new Intl.NumberFormat(L.locale,{maximumFractionDigits:2});
var unitMoney=new Intl.NumberFormat(L.locale,{style:'currency',currency:L.currency,minimumFractionDigits:2,maximumFractionDigits:4});
function fm(n){return money.format(n||0)}
/* A unit price can be below a cent: 0.875 per word must not print as 0.88 beside 1,000 words = 875.00. */
function fu(n){return unitMoney.format(n||0)}
function fd(v){if(!v)return '';var p=v.split('-');return L.dateFmt.replace('Y',p[0]).replace('M',p[1]).replace('D',p[2])}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function addDays(v,d){if(!v||d===''||d==null)return '';var t=new Date(v+'T12:00:00Z'),n=Math.max(-36500,Math.min(36500,parseNum(d)));if(isNaN(t))return '';t.setUTCDate(t.getUTCDate()+n);return t.toISOString().slice(0,10)}
/* toPrecision: 102*8.25/100 is 8.414999... in floating point, and must round to 8.42 like Excel and tool_pages.py (review, 9 Nov 2026). */
function cents(n){return Math.round(+(n*100).toPrecision(12))/100}
/* People type "1,250.50", "$100", "8.25%", "2 hrs" or, in another country, "1.250,50". With both marks the last one is the decimal.
   With one mark: the country's decimal mark is decimal; the other mark is thousands only in groups of 3 digits, so "1,5" stays 1.5. */
/* Anything else ("1/2", "5-10", "1.2.3", "approx. 100") is not guessed: it counts as 0 and the form says to check it (review, 9 Nov 2026). */
var NUM_OK=/^[\p{Sc}]?\s*-?(\d{1,3}([.,]\d{3})+([.,]\d+)?|\d+([.,]\d+)?|[.,]\d+)\s*(%|\p{Sc}|\p{L}{1,8}\.?)?$/u;
function numOk(v){return NUM_OK.test(String(v==null?'':v).trim())}
function parseNum(v){if(!numOk(v))return 0;var s=String(v).replace(/[^\d.,-]/g,'');var dot=s.lastIndexOf('.'),com=s.lastIndexOf(','),dec;
 if(dot>=0&&com>=0)dec=dot>com?'.':',';else if(dot<0&&com<0)dec='';else{var one=dot>=0?'.':',';dec=one===L.fmt.decimal||!new RegExp('^-?\\d{1,3}(\\'+one+'\\d{3})+$').test(s)?one:''}
 var parts=dec?[s.slice(0,s.lastIndexOf(dec)),s.slice(s.lastIndexOf(dec)+1)]:[s,''];return +(parts[0].replace(/[.,]/g,'')+'.'+parts[1])||0}
function mode(s){return TAX.type==='modes'?TAX.modes[s.mode]:null}
function totals(s){var t={net:0,labor:0,mat:0,tax:0};
 s.items.forEach(function(i){var a=cents(parseNum(i.q)*parseNum(i.r));t.net+=a;if(i.k==='L')t.labor+=a;else t.mat+=a});
 t.net=cents(t.net);t.labor=cents(t.labor);t.mat=cents(t.mat);var m=mode(s);
 if(m)t.tax=cents(t.net*m.rate/100);else{var base=(C.taxBase&&s.taxOn==='parts')?t.mat:t.net;t.tax=cents(base*parseNum(s.rate)/100)}
 t.gross=cents(t.net+t.tax);t.paid=cents(parseNum(s.paid));t.balance=cents(t.gross-t.paid);
 t.deposit=cents(t.gross*parseNum(s.depositPct)/100);return t}
/* A note in a law record may hold {labor}, {laborGross} or {due}; "when" limits it to one form choice. */
function lawNotes(s,t,due){var m=mode(s),out=[];if(m&&m.note)out.push(m.note);
 (L.notes||[]).forEach(function(n){var ok=Object.keys(n.when||{}).every(function(k){return s[k]===n.when[k]});if(!ok)return;
  out.push(n.text.replace('{labor}',fm(t.labor)).replace('{laborGross}',fm(m&&m.rate?cents(t.labor*(1+m.rate/100)):t.labor)).replace('{due}',fd(due)))});return out}
function compose(s,t){var k=C.kind,due=k==='estimate'?'':addDays(s.date,s.dueDays),m=mode(s),tot=[],meta,sec=[],notes=[];
 if(C.split)tot.push([U.labor_total,fm(t.labor)],[U.parts_total,fm(t.mat)]);
 tot.push([U.net,fm(t.net)]);
 if(m){if(m.line)tot.push([m.line,fm(t.tax)])}else if(parseNum(s.rate))tot.push([U.taxLine+' '+parseNum(s.rate)+'%'+(C.taxBase&&s.taxOn==='parts'?' ('+U.parts_short+')':''),fm(t.tax)]);
 tot.push([(m&&m.total)||(k==='estimate'?U.estimate_total:U.gross),fm(t.gross),1]);
 if(k==='estimate'){if(parseNum(s.depositPct))tot.push([U.deposit_line+' '+parseNum(s.depositPct)+'%',fm(t.deposit)])}
 else if(t.paid){tot.push([U.paid_line,fm(t.paid)],[U.balance_line,fm(t.balance),1])}
 if(k==='estimate'){meta=[[U.est_no,s.no],[U.est_date,fd(s.date)],[U.valid_until,fd(addDays(s.date,s.validDays))],[U.start_date,fd(s.start)]];
  if(s.scope)sec.push([U.scope,s.scope]);if(s.exclusions)sec.push([U.exclusions,s.exclusions])}
 else if(k==='freelance'){meta=[[U.inv_no,s.no],[U.inv_date,fd(s.date)],[U.project,s.project],[U.due_date,fd(due)]];
  if(s.rights)sec.push([U.rights,s.rights]);if(s.lateFee)sec.push([U.late_fee,s.lateFee])}
 else meta=[[U.inv_no,s.no],[U.inv_date,fd(s.date)],[U.service_date,fd(s.svc)],[U.due_date,fd(due)]];
 notes=lawNotes(s,t,due);if(s.terms)notes.push(s.terms);if(s.pay)notes.push(U.pay_by+': '+s.pay);
 return {title:k==='estimate'?U.title_estimate:U.title_invoice,from:[s.bizName,s.bizAddr,s.bizId,s.bizContact].filter(Boolean),
  toLabel:k==='estimate'?U.prepared_for:U.bill_to,to:[s.custName,s.custAddr,s.siteAddr&&s.siteAddr!==s.custAddr?U.service_at+': '+s.siteAddr:''].filter(Boolean),
  meta:meta.filter(function(x){return x[1]}),metaAll:meta,tot:tot,sections:sec,notes:notes,accept:k==='estimate'?U.accept_line:''}}
function doc(s){var t=totals(s),d=compose(s,t);d.rows=s.items.filter(function(i){return i.d||i.q||i.r}).map(function(i){var q=parseNum(i.q),r=parseNum(i.r);return {d:i.d,q:num.format(q),u:i.u||'',r:fu(r),a:fm(cents(q*r)),qn:q,rn:r,k:i.k}});d.t=t;return d}
/* The blank PDF is for filling in by hand: labels and ruled lines, no $0.00 (review, 9 Nov 2026). */
function blankDoc(s){var d=doc(s),B='______________',k=C.kind,tot=[];d.from=[U.your_biz,B+B,B+B];d.to=[B+B,B+B];d.meta=d.metaAll.map(function(m){return [m[0],B]});
 d.rows=[];for(var i=0;i<12;i++)d.rows.push({d:'',q:'',u:'',r:'',a:''});
 tot.push([U.net,B],[U.taxLine,B],[k==='estimate'?U.estimate_total:U.gross,B,1]);if(k==='estimate')tot.push([U.deposit_line,B]);else tot.push([U.paid_line,B],[U.balance_line,B,1]);d.tot=tot;
 d.sections=k==='estimate'?[[U.scope,B+B+B+B],[U.exclusions,B+B+B+B]]:k==='freelance'?[[U.rights,B+B+B+B],[U.late_fee,B+B+B+B]]:[];d.notes=[U.pay_by+': '+B+B];return d}
function blank(){var s=JSON.parse(JSON.stringify(C.sample));Object.keys(s).forEach(function(k){if(k==='items')s.items=[{d:'',q:'',u:'',r:'',k:'L'},{d:'',q:'',u:'',r:'',k:'M'},{d:'',q:'',u:'',r:'',k:'M'},{d:'',q:'',u:'',r:'',k:'M'}];else if(C.keepOnClear.indexOf(k)<0)s[k]=''});return s}

/* ---------- form ---------- */
function bind(){$$('[data-f]').forEach(function(el){var k=el.getAttribute('data-f');
  if(el.type==='radio')el.checked=(S[k]===el.value);else el.value=S[k]==null?'':S[k];
  if(el.dataset.bound)return;el.dataset.bound='1';
  el.addEventListener(el.type==='radio'?'change':'input',function(){if(el.type==='radio'&&!el.checked)return;S[k]=el.value;render()})})}
function rowHTML(it){return '<label class="d"><span>'+U.desc+'</span><input data-i="d" value="'+esc(it.d)+'"></label><label class="q"><span>'+U.qty+'</span><input data-i="q" inputmode="decimal" value="'+esc(it.q)+'"></label><label class="u"><span>'+U.unit+'</span><input data-i="u" value="'+esc(it.u)+'"></label><label class="p"><span>'+U.price+'</span><input data-i="r" inputmode="decimal" value="'+esc(it.r)+'"></label>'+(C.split?'<label class="k"><span>'+U.kind+'</span><select data-i="k"><option value="L"'+(it.k==='L'?' selected':'')+'>'+U.labor+'</option><option value="M"'+(it.k!=='L'?' selected':'')+'>'+U.material+'</option></select></label>':'')+'<button type="button" class="x" aria-label="'+esc(U.remove)+'">×</button>'}
function rows(){var box=$('#items');box.innerHTML='';S.items.forEach(function(it,i){var r=document.createElement('div');r.className='row'+(C.split?'':' nosplit');r.innerHTML=rowHTML(it);
  $$('[data-i]',r).forEach(function(el){el.addEventListener(el.tagName==='SELECT'?'change':'input',function(){it[el.getAttribute('data-i')]=el.value;render()})});
  $('.x',r).addEventListener('click',function(){S.items.splice(i,1);rows();render()});box.appendChild(r)})}
function addItem(d,q,u,r,k){S.items.push({d:d,q:q,u:u,r:r,k:k});rows();render();var last=$('#items .row:last-child');if(last)last.scrollIntoView({block:'nearest'});var st=$('#added');if(st)st.textContent=U.added+': '+d}

/* ---------- live paper ---------- */
function paperHTML(d){var h='<div class="p-top"><div><b class="p-co">'+esc(d.from[0]||U.your_biz)+'</b>'+d.from.slice(1).map(function(x){return '<div>'+esc(x)+'</div>'}).join('')+'</div><div class="p-title">'+esc(d.title)+'</div></div>';
 h+='<div class="p-mid"><div><div class="p-lab">'+esc(d.toLabel)+'</div>'+d.to.map(function(x){return '<div>'+esc(x)+'</div>'}).join('')+'</div><table class="p-meta">'+d.meta.map(function(m){return '<tr><th>'+esc(m[0])+'</th><td>'+esc(m[1])+'</td></tr>'}).join('')+'</table></div>';
 h+='<table class="p-items"><thead><tr><th>'+esc(U.desc)+'</th><th class="n">'+esc(U.qty)+'</th><th>'+esc(U.unit)+'</th><th class="n">'+esc(U.price)+'</th><th class="n">'+esc(U.amount)+'</th></tr></thead><tbody>'+d.rows.map(function(r){return '<tr><td>'+esc(r.d)+'</td><td class="n">'+r.q+'</td><td>'+esc(r.u)+'</td><td class="n">'+r.r+'</td><td class="n">'+r.a+'</td></tr>'}).join('')+'</tbody></table>';
 h+='<table class="p-tot">'+d.tot.map(function(x){return '<tr'+(x[2]?' class="b"':'')+'><th>'+esc(x[0])+'</th><td>'+esc(x[1])+'</td></tr>'}).join('')+'</table>';
 h+=d.sections.map(function(x){return '<p class="p-note"><b>'+esc(x[0])+':</b> '+esc(x[1])+'</p>'}).join('');
 h+=d.notes.map(function(n){return '<p class="p-note">'+esc(n)+'</p>'}).join('');
 if(d.accept)h+='<p class="p-note p-accept">'+esc(d.accept)+'</p>';return h}
function lost(d){var txt=[d.title].concat(d.from,d.to,d.notes).concat(d.meta.map(function(m){return m[1]})).concat(d.sections.map(function(x){return x[1]})).concat(d.rows.map(function(r){return r.d+r.u})).join(' ');
 var bad={};String(txt).split('').forEach(function(ch){if(ch.charCodeAt(0)>255&&PDFMAP[ch.charCodeAt(0)]===undefined&&!/\s/.test(ch))bad[ch]=1});return Object.keys(bad).join(' ')}
function badNumber(){var vals=[S.rate,S.paid,S.depositPct];S.items.forEach(function(i){vals.push(i.q,i.r)});return vals.filter(function(v){return v!==''&&v!=null&&!numOk(v)})[0]||''}
function render(){var d=doc(S);$('#paper').innerHTML=paperHTML(d);$$('[data-total]').forEach(function(e){e.textContent=fm(d.t.gross)});
 var miss=C.required.filter(function(k){return k==='items'?!S.items.some(function(i){return i.d}):!S[k]}).map(function(k){return U.required[k]}),b=$('#missing'),gone=lost(d),badNum=badNumber();
 b.className=(miss.length||gone||badNum)?'miss':'miss ok';b.textContent=gone?U.pdf_lost.replace('{chars}',gone):badNum?U.bad_number.replace('{value}',badNum):(miss.length?(U.missing+': '+miss.join(', ')):U.complete);
 $$('[data-show]').forEach(function(e){var p=e.getAttribute('data-show').split('=');e.hidden=S[p[0]]!==p[1]})}

/* ---------- PDF: hand-written, so the page needs no library ---------- */
var W=[278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,278,278,584,584,584,556,1015,667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611,278,278,278,469,556,333,556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500,334,260,334,584];
var PDFMAP={0x20AC:0x80,0x2013:0x96,0x2014:0x97,0x2022:0x95,0x201E:0x84,0x201C:0x93,0x201D:0x94,0x2018:0x91,0x2019:0x92,0x2026:0x85,0x2122:0x99,0x0160:0x8A,0x0161:0x9A,0x0152:0x8C,0x0153:0x9C,0x017D:0x8E,0x017E:0x9E,0x0178:0x9F};
function tw(s,sz,b){var w=0,n=String(s).normalize('NFD').replace(/[̀-ͯ]/g,'');for(var i=0;i<n.length;i++){var c=n.charCodeAt(i);w+=(c>=32&&c<=126)?W[c-32]:556}return w*sz/1000*(b?1.06:1)}
function pe(s){var o='';s=String(s).replace(/[   ]/g,' ');for(var i=0;i<s.length;i++){var c=s.charCodeAt(i),ch;if(PDFMAP[c]!==undefined)c=PDFMAP[c];else if(c>255)c=0x3F;ch=String.fromCharCode(c);if(ch==='('||ch===')'||ch==='\\')o+='\\'+ch;else o+=ch}return o}
function wrap(s,sz,max){var out=[],line='';String(s).split(/\s+/).forEach(function(w){while(tw(w,sz)>max&&w.length>1){var n=Math.max(1,Math.floor(w.length*max/tw(w,sz)));if(line){out.push(line);line=''}out.push(w.slice(0,n));w=w.slice(n)}var t=line?line+' '+w:w;if(tw(t,sz)>max&&line){out.push(line);line=w}else line=t});if(line)out.push(line);return out.length?out:['']}
function pdf(d){var PW=L.page[0],PH=L.page[1],M=50,pages=[],c='',y=PH-M;
 function color(r,g,b){c+=r+' '+g+' '+b+' rg\n'}
 function T(s,x,yy,sz,b,right){if(right)x-=tw(s,sz,b);c+='BT /'+(b?'F2':'F1')+' '+sz+' Tf '+x.toFixed(2)+' '+yy.toFixed(2)+' Td ('+pe(s)+') Tj ET\n'}
 function LN(x1,yy,x2,g){c+=(g||0.82)+' G 0.6 w '+x1+' '+yy.toFixed(2)+' m '+x2+' '+yy.toFixed(2)+' l S\n'}
 function next(){pages.push(c);c='';y=PH-M}
 function need(h){if(y-h<M+10)next()}
 var R=PW-M,cQ=R-205,cU=R-195,cP=R-70,cA=R,dW=cQ-M-34;
 color(0.043,0.071,0.125);wrap(d.from[0]||'',14,R-M-160).forEach(function(l,i){T(l,M,y-i*16,14,1)});T(d.title,R,y,20,1,1);y-=16*wrap(d.from[0]||'',14,R-M-160).length;
 d.from.slice(1).forEach(function(l){wrap(l,9,R-M-160).forEach(function(w){T(w,M,y,9);y-=12})});y=Math.min(y,PH-M-30)-14;
 var top=y;T(d.toLabel,M,y,8,1);y-=13;d.to.forEach(function(l){wrap(l,10,R-M-230).forEach(function(w){T(w,M,y,10);y-=13})});
 var my=top;d.meta.forEach(function(m){T(m[0],R-110,my,9,1,1);wrap(m[1],9,105).forEach(function(w){T(w,R,my,9,0,1);my-=11});my-=2});y=Math.min(y,my)-14;
 function head(){c+='0.929 0.957 1 rg '+M+' '+(y-5).toFixed(2)+' '+(R-M)+' 18 re f\n';color(0.043,0.071,0.125);T(U.desc,M+4,y,8.5,1);T(U.qty,cQ,y,8.5,1,1);T(U.unit,cU,y,8.5,1);T(U.price,cP,y,8.5,1,1);T(U.amount,cA-4,y,8.5,1,1);y-=18}
 head();d.rows.forEach(function(r){var ls=wrap(r.d,9.5,dW);need(Math.min(ls.length,3)*12+8);if(y===PH-M)head();T(ls[0],M+4,y,9.5);T(r.q,cQ,y,9.5,0,1);T(r.u,cU,y,9.5);T(r.r,cP,y,9.5,0,1);T(r.a,cA-4,y,9.5,0,1);ls.slice(1).forEach(function(l){y-=12;if(y<M+10){next();y-=12}T(l,M+4,y,9.5)});y-=7;LN(M,y,R);y-=11});
 y-=4;need(d.tot.length*16+10);d.tot.forEach(function(x){T(x[0],R-110,y,x[2]?11:9.5,x[2],1);T(x[1],R-4,y,x[2]?11:9.5,x[2],1);y-=x[2]?18:14});y-=6;
 /* need() per line: a long text flows over as many pages as it takes. A block check lost the text after one page (review, 9 Nov 2026). */
 function para(s){wrap(s,8.5,R-M).forEach(function(l){need(11);T(l,M,y,8.5);y-=11});y-=5}
 d.sections.forEach(function(x){need(22);T(x[0],M,y,8.5,1);y-=11;para(x[1])});
 d.notes.forEach(para);
 if(d.accept){need(30);y-=12;T(d.accept,M,y,9.5);y-=12}
 pages.push(c);
 var objs=[],n=pages.length,kids=[];for(var i=0;i<n;i++)kids.push((5+i*2)+' 0 R');
 objs[1]='<< /Type /Catalog /Pages 2 0 R >>';objs[2]='<< /Type /Pages /Kids ['+kids.join(' ')+'] /Count '+n+' >>';
 objs[3]='<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>';objs[4]='<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>';
 pages.forEach(function(p,i){objs[5+i*2]='<< /Type /Page /Parent 2 0 R /MediaBox [0 0 '+PW+' '+PH+'] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents '+(6+i*2)+' 0 R >>';objs[6+i*2]='<< /Length '+p.length+' >>\nstream\n'+p+'endstream'});
 var out='%PDF-1.4\n%\xE2\xE3\xCF\xD3\n',xref=[0];for(var k=1;k<objs.length;k++){xref[k]=out.length;out+=k+' 0 obj\n'+objs[k]+'\nendobj\n'}
 var xr=out.length;out+='xref\n0 '+objs.length+'\n0000000000 65535 f \n';for(k=1;k<objs.length;k++)out+=('0000000000'+xref[k]).slice(-10)+' 00000 n \n';
 out+='trailer\n<< /Size '+objs.length+' /Root 1 0 R >>\nstartxref\n'+xr+'\n%%EOF\n';
 var u=new Uint8Array(out.length);for(k=0;k<out.length;k++)u[k]=out.charCodeAt(k)&255;return new Blob([u],{type:'application/pdf'})}

/* ---------- ZIP (stored) for Word and Excel ---------- */
var CRC=(function(){var t=[];for(var n=0;n<256;n++){var c=n;for(var k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;t[n]=c>>>0}return t})();
function crc(b){var c=0xFFFFFFFF;for(var i=0;i<b.length;i++)c=CRC[(c^b[i])&255]^(c>>>8);return (c^0xFFFFFFFF)>>>0}
function zip(files){var enc=new TextEncoder(),chunks=[],cd=[],off=0;function h(a,v,l){for(var i=0;i<l;i++)a.push((v>>>(8*i))&255)}
 Object.keys(files).forEach(function(name){var nb=enc.encode(name),db=enc.encode(files[name]),cr=crc(db),a=[];h(a,0x04034b50,4);h(a,20,2);h(a,0x0800,2);h(a,0,2);h(a,0,2);h(a,0x21,2);h(a,cr,4);h(a,db.length,4);h(a,db.length,4);h(a,nb.length,2);h(a,0,2);
  var b=[];h(b,0x02014b50,4);h(b,20,2);h(b,20,2);h(b,0x0800,2);h(b,0,2);h(b,0,2);h(b,0x21,2);h(b,cr,4);h(b,db.length,4);h(b,db.length,4);h(b,nb.length,2);h(b,0,2);h(b,0,2);h(b,0,2);h(b,0,2);h(b,0,4);h(b,off,4);
  chunks.push(new Uint8Array(a),nb,db);cd.push(new Uint8Array(b),nb);off+=a.length+nb.length+db.length});
 var cs=cd.reduce(function(s,x){return s+x.length},0),e=[];h(e,0x06054b50,4);h(e,0,2);h(e,0,2);h(e,Object.keys(files).length,2);h(e,Object.keys(files).length,2);h(e,cs,4);h(e,off,4);h(e,0,2);
 return chunks.concat(cd,[new Uint8Array(e)])}
/* XML 1.0 forbids control characters; one pasted U+000B made the Word and Excel files unreadable (review, 9 Nov 2026). */
function x(s){return esc(s).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g,' ').replace(/'/g,'&apos;')}
var XH='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',RELS='http://schemas.openxmlformats.org/package/2006/relationships';
function docx(d){var rtl=L.dir==='rtl';function P(t,b,sz,al){return '<w:p><w:pPr>'+(rtl?'<w:bidi/>':'')+(al?'<w:jc w:val="'+al+'"/>':'')+'</w:pPr><w:r><w:rPr>'+(b?'<w:b/>':'')+(sz?'<w:sz w:val="'+sz+'"/>':'')+(rtl?'<w:rtl/>':'')+'</w:rPr><w:t xml:space="preserve">'+x(t)+'</w:t></w:r></w:p>'}
 function Cell(t,w,b,al){return '<w:tc><w:tcPr><w:tcW w:w="'+w+'" w:type="dxa"/></w:tcPr>'+P(t,b,18,al)+'</w:tc>'}
 var ws=[4300,800,900,1500,1500],bd='<w:tblBorders>'+['top','left','bottom','right','insideH','insideV'].map(function(s){return '<w:'+s+' w:val="single" w:sz="4" w:space="0" w:color="BFC9D9"/>'}).join('')+'</w:tblBorders>';
 var tb='<w:tbl><w:tblPr><w:tblW w:w="9000" w:type="dxa"/>'+bd+'</w:tblPr><w:tblGrid>'+ws.map(function(w){return '<w:gridCol w:w="'+w+'"/>'}).join('')+'</w:tblGrid><w:tr>'+[U.desc,U.qty,U.unit,U.price,U.amount].map(function(t,i){return Cell(t,ws[i],1)}).join('')+'</w:tr>'+d.rows.map(function(r){return '<w:tr>'+Cell(r.d,ws[0])+Cell(r.q,ws[1],0,'right')+Cell(r.u,ws[2])+Cell(r.r,ws[3],0,'right')+Cell(r.a,ws[4],0,'right')+'</w:tr>'}).join('')+'</w:tbl>';
 var body=P(d.title,1,36)+d.from.map(function(l,i){return P(l,!i)}).join('')+P('')+P(d.toLabel,1)+d.to.map(function(l){return P(l)}).join('')+P('')+d.meta.map(function(m){return P(m[0]+': '+m[1])}).join('')+P('')+tb+P('')+d.tot.map(function(t){return P(t[0]+': '+t[1],t[2],0,'right')}).join('')+P('')+d.sections.map(function(s){return P(s[0],1,17)+P(s[1],0,17)}).join('')+d.notes.map(function(n){return P(n,0,17)}).join('')+(d.accept?P('')+P(d.accept):'');
 return zip({'[Content_Types].xml':XH+'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
 '_rels/.rels':XH+'<Relationships xmlns="'+RELS+'"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
 'word/document.xml':XH+'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'<w:sectPr><w:pgSz w:w="'+L.page[0]*20+'" w:h="'+L.page[1]*20+'"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>'})}
/* Formula cells carry a cached value: iPhone Quick Look and other previews show only the cache and do not calculate. */
function xlsx(d,s){var out=[],n=0;function sc(col,t,st){return '<c r="'+col+n+'" t="inlineStr"'+(st?' s="'+st+'"':'')+'><is><t xml:space="preserve">'+x(t)+'</t></is></c>'}function nc(col,v,st){return '<c r="'+col+n+'" s="'+(st||2)+'"><v>'+v+'</v></c>'}function fc(col,f,v,st){return '<c r="'+col+n+'" s="'+(st||2)+'"><f>'+f+'</f><v>'+v+'</v></c>'}
 function add(cs){n++;out.push('<row r="'+n+'">'+cs()+'</row>')}var t=d.t,m=mode(s),taxPart=!m&&C.taxBase&&s.taxOn==='parts',rate=m?m.rate:parseNum(s.rate);
 add(function(){return sc('A',d.title,1)});d.from.forEach(function(l){add(function(){return sc('A',l)})});add(function(){return ''});add(function(){return sc('A',d.toLabel,1)});d.to.forEach(function(l){add(function(){return sc('A',l)})});add(function(){return ''});d.meta.forEach(function(q){add(function(){return sc('A',q[0],1)+sc('B',q[1])})});add(function(){return ''});
 add(function(){return sc('A',U.desc,1)+sc('B',U.qty,1)+sc('C',U.unit,1)+sc('D',U.price,1)+sc('E',U.amount,1)+sc('F',U.taxed,1)});var first=n+1;
 var rs=d.rows.slice();while(rs.length<12)rs.push({d:'',qn:'',u:'',rn:'',k:'M'});rs.forEach(function(r){add(function(){var v=r.qn===''?0:cents(r.qn*r.rn);return sc('A',r.d)+(r.qn===''?'':nc('B',r.qn))+sc('C',r.u)+(r.rn===''?'':nc('D',r.rn,5))+fc('E','ROUND(B'+n+'*D'+n+',2)',v)+nc('F',(taxPart&&r.k==='L')?0:1,4)})});var last=n;
 add(function(){return ''});add(function(){return sc('D',U.net,1)+fc('E','SUM(E'+first+':E'+last+')',t.net)});var net=n;
 add(function(){return sc('D',U.taxLine+' %',1)+nc('E',rate,5)});var rt=n;
 add(function(){return sc('D',U.taxLine,1)+fc('E','ROUND(SUMPRODUCT(E'+first+':E'+last+',F'+first+':F'+last+')*E'+rt+'/100,2)',t.tax)});var tx=n;
 add(function(){return sc('D',d.tot.filter(function(q){return q[2]})[0][0],1)+fc('E','E'+net+'+E'+tx,t.gross,3)});var gr=n;
 if(C.kind==='estimate'){if(parseNum(s.depositPct)){add(function(){return sc('D',U.deposit_line+' %',1)+nc('E',parseNum(s.depositPct))});var dp=n;add(function(){return sc('D',U.deposit_line,1)+fc('E','ROUND(E'+gr+'*E'+dp+'/100,2)',t.deposit)})}}
 else if(t.paid){add(function(){return sc('D',U.paid_line,1)+nc('E',t.paid)});var pd=n;add(function(){return sc('D',U.balance_line,1)+fc('E','E'+gr+'-E'+pd,t.balance,3)})}
 add(function(){return ''});d.sections.forEach(function(q){add(function(){return sc('A',q[0],1)});add(function(){return sc('A',q[1])})});d.notes.forEach(function(l){add(function(){return sc('A',l)})});if(d.accept){add(function(){return sc('A',d.accept)})}
 return zip({'[Content_Types].xml':XH+'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
 '_rels/.rels':XH+'<Relationships xmlns="'+RELS+'"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
 'xl/workbook.xml':XH+'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="'+x(d.title.slice(0,30))+'" sheetId="1" r:id="rId1"/></sheets><calcPr fullCalcOnLoad="1"/></workbook>',
 'xl/_rels/workbook.xml.rels':XH+'<Relationships xmlns="'+RELS+'"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
 'xl/styles.xml':XH+'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="#,##0.00##"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="164" fontId="1" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1"/><xf numFmtId="1" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
 'xl/worksheets/sheet1.xml':XH+'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols><col min="1" max="1" width="48" customWidth="1"/><col min="2" max="3" width="10" customWidth="1"/><col min="4" max="5" width="14" customWidth="1"/><col min="6" max="6" width="8" customWidth="1"/></cols><sheetData>'+out.join('')+'</sheetData></worksheet>'})}

/* ---------- downloads ---------- */
function track(name,params){if(typeof gtag==='function')gtag('event',name,params)}
/* tool_used (NIK-634): the first edit or download on this page view; ga.js sends it once. */
function used(action,format){if(typeof window.trackToolUsed==='function')window.trackToolUsed(action,format)}
['input','change'].forEach(function(t){document.addEventListener(t,function(e){if(e.target&&e.target.closest&&e.target.closest('[data-f],[data-i]'))used('edit')},true)});
function save(blob,name){var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(a.href)},2000)}
function dl(kind,src){var s=src||S,d=src?blankDoc(src):doc(s),base=C.slug+(src?'-blank':'-'+String(s.no||'draft').replace(/[^\w-]+/g,'-'));
 /* Only the file type and page go to GA4, for the weekly download count. Never a form value. */
 track('tool_download',{file_format:kind,blank:!!src,tool_page:location.pathname});
 used('download',kind);
 if(kind==='pdf')save(pdf(d),base+'.pdf');
 if(kind==='docx')save(new Blob(docx(d),{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}),base+'.docx');
 if(kind==='xlsx')save(new Blob(xlsx(d,s),{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),base+'.xlsx')}
$$('[data-dl]').forEach(function(b){b.addEventListener('click',function(){dl(b.getAttribute('data-dl'),b.hasAttribute('data-blank')?blank():null)})});
$('#add-row').addEventListener('click',function(){used('edit');addItem('',1,'','',C.split?'M':'L')});
$$('[data-add]').forEach(function(b){b.addEventListener('click',function(){var v=JSON.parse(b.getAttribute('data-add'));addItem(v[0],v[1],v[2],v[3],v[4])})});
$('#clear').addEventListener('click',function(){S=blank();bind();rows();render()});
$$('a[href*="apps.apple.com"]').forEach(function(a){a.addEventListener('click',function(){track('click_app_store',{event_category:'conversion',event_label:'tool page',tool_page:location.pathname})})});
/* click_web_app is sent by ga.js for every page (NIK-634). */
bind();rows();render();
window.__invoice={state:function(){return S},totals:function(){return totals(S)},parse:parseNum};
})();
