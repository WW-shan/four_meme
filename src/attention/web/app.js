'use strict';
const state = {data: null, view: 'rising', chain: 'all', window: '1h', query: '', busy: false};
const $ = id => document.getElementById(id);
const windows = {'5m':300, '15m':900, '1h':3600, '6h':21600, '24h':86400};
const format = value => value === null || value === undefined ? '\u2014' : new Intl.NumberFormat('en', {maximumFractionDigits:1, notation:value >= 10000 ? 'compact' : 'standard'}).format(value);
const money = value => value === null || value === undefined ? '\u2014' : '$' + format(value);
const date = value => new Date(value * 1000).toLocaleString();
function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
function badge(text, warn=false) { return el('span', text, 'badge' + (warn ? ' warn' : '')); }
function metric(value) { return el('span', format(value), 'metric'); }
function short(address) { return address.slice(0,6) + '...' + address.slice(-5); }
function spark(values) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 96 32'); svg.setAttribute('width','96'); svg.setAttribute('height','32');
  svg.setAttribute('role','img'); svg.setAttribute('aria-label','Observed mentions per five-minute interval over the last hour');
  const max = Math.max(...values, 1);
  values.forEach((value,index) => { const r = document.createElementNS(svg.namespaceURI,'rect'); const height = Math.max(2,value/max*30); r.setAttribute('x', index*8); r.setAttribute('y', 32-height); r.setAttribute('width',5); r.setAttribute('height',height); r.setAttribute('fill', index===11 ? '#b34221' : '#43623e'); svg.append(r); });
  return svg;
}
function empty(title, message) { const n=el('div',undefined,'empty'); n.append(el('strong',title),el('p',message)); return n; }
function table(headings, rows) {
  const wrapper=el('div',undefined,'table-wrap'), t=el('table'), head=el('thead'), tr=el('tr'), body=el('tbody');
  headings.forEach(h=>tr.append(el('th',h))); head.append(tr); t.append(head);
  rows.forEach(cells=>{const row=el('tr'); cells.forEach((c,i)=>{const td=el('td',undefined,i===0?'rank':''); td.append(c instanceof Node ? c : document.createTextNode(String(c))); row.append(td);}); body.append(row);});
  t.append(body); wrapper.append(t); return wrapper;
}
function subject(title, caption, onClick) { const n=el('div'), b=el('button',title,'subject'); b.addEventListener('click',onClick); n.append(b,el('div',caption,'subline')); return n; }
function evidence(post) {
  const n=el('article',undefined,'evidence'); n.append(el('div',`@${post.username || post.author_id} / ${post.kind.toUpperCase()} / ${date(post.created)}`,'evidence-meta'));
  n.append(el('p',post.text));
  if (post.contracts && post.contracts.length) { const c=el('p'); post.contracts.forEach(m=>c.append(badge(`${m.chain || 'CHAIN UNKNOWN'} ${short(m.address)} / ${m.status.replaceAll('_',' ')}`,m.status!=='observed_contract_match'),document.createTextNode(' '))); n.append(c); }
  if (post.url) { try { const url=new URL(post.url); if(url.protocol==='https:' && ['x.com','twitter.com'].includes(url.hostname)){const a=el('a','Open original source \u2197'); a.href=url.href; a.target='_blank'; a.rel='noopener noreferrer'; n.append(a);} } catch (_) {} }
  n.append(el('p',`First received ${date(post.first_observed)}. A mention is not an endorsement.`,'evidence-meta')); return n;
}
function showDetail(item, token=false) {
  const body=$('detail-body'); body.replaceChildren(el('h2',token ? `${item.symbol} / ${item.chain.toUpperCase()}` : item.name));
  if(token){const c=el('div',item.address,'contract'), copy=el('button','Copy CA'); copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(item.address);copy.textContent='Copied';}catch(_){copy.textContent='Select address to copy';}});c.append(copy);body.append(c,el('p',`${item.mapping.replaceAll('_',' ')}. Token identity and tradeability are not verified.`));}
  const social=token ? item.social : item;
  if(social){const m=social.windows[state.window];body.append(el('p',`${m.mentions} observed posts from ${m.authors} distinct authors in ${state.window}. Originals ${m.original}, reposts ${m.repost}, quotes ${m.quote}, replies ${m.reply}.`)); body.append(el('p',`Cumulative reposts reported on observed originals: ${format(m.reported_reposts_of_originals)}. These are not additional observed posts.`)); social.evidence.forEach(p=>body.append(evidence(p)));}
  else body.append(empty('No linked X evidence','No exact contract mention was linked in the observed query coverage. This does not mean there are no discussions on X.'));
  $('detail').showModal();
}
function inChain(topic){return state.chain==='all' || topic.linked_chains.includes(state.chain);}
function matches(text){return text.toLowerCase().includes(state.query.toLowerCase());}
function render(){
  const d=state.data;if(!d)return;
  $('mode-banner').hidden=d.mode!=='demo';$('mode-banner').textContent='DEMO EDITION / Synthetic, frozen observations. No live market data, no real token recommendations.';
  $('updated').textContent=date(d.as_of);$('coverage').textContent=d.mode==='demo'?'Frozen demonstration snapshot':d.coverage;
  const ok=d.sources.filter(s=>s.status==='ok').length;
  const stats=[['TOPICS OBSERVED',d.topics.length,'Within configured queries'],['TOKEN CANDIDATES',d.tokens.length,'Provider discovery, not verified'],['CURATED KOL EVENTS',d.kol_events_total,'Observed mentions, not promotions'],['SOURCES REPORTING',`${ok} / ${d.sources.length}`,'Fresh successful source checks']];
  $('stats').replaceChildren(...stats.map(([label,value,caption])=>{const n=el('div',undefined,'stat');n.append(el('div',label,'stat-label'),el('div',value,'stat-value'),el('div',caption,'stat-caption'));return n;}));
  $('chain').disabled=state.view==='kol';$('window').disabled=state.view==='tokens';
  let content,count=0;
  if(state.view==='rising'||state.view==='sustained'){
    let rows=d.topics.filter(t=>inChain(t)&&matches(t.name+' '+t.id));
    rows.sort((a,b)=>state.view==='rising'?(b.rising_score??-1)-(a.rising_score??-1):Number(b.ranking_eligible)-Number(a.ranking_eligible)||(b.windows[state.window].authors??0)-(a.windows[state.window].authors??0));
    count=rows.length;
    $('view-note').textContent=state.view==='rising'?'Rising uses consecutive 15-minute author windows. Without a complete baseline, observations are unranked. Counts follow the selected window.':'Ranked by distinct observed authors in the selected window. Different queries can overlap; their counts must not be added together.';
    content=rows.length?table(['#','TOPIC / COVERAGE','AUTHORS','MENTIONS','15m GROWTH','LAST HOUR'],rows.map((t,i)=>{const m=t.windows[state.window];return [!t.ranking_eligible||(state.view==='rising'&&t.rising_score===null)?'\u2014':String(i+1).padStart(2,'0'),subject(t.name,t.source_status.toUpperCase()+(t.window_complete[state.window]?'':' / PARTIAL WINDOW'),()=>showDetail(t)),metric(m.authors),metric(m.mentions),t.author_acceleration_15m===null?badge('BASELINE PENDING',true):el('span',`${format(t.author_acceleration_15m)}x`,'metric'+(t.author_acceleration_15m>=1?' positive':'')),spark(t.mentions_5m_bins)];})):empty('No observed topics yet','Configure X topic queries and a personal bearer token to start collecting. Topics without a confirmed contract are retained here.');
  }else if(state.view==='tokens'){
    const rows=d.tokens.filter(t=>(state.chain==='all'||state.chain===t.chain)&&matches(t.name+' '+t.symbol+' '+t.address));count=rows.length;
    $('view-note').textContent='Grouped by chain, ranked within each chain by observed GMGN visits. Stale rows are unranked. Provider rank is retained in the API. Platform visits are not X mentions. Window is fixed at 1h.';
    content=rows.length?table(['CHAIN RANK','TOKEN / CONTRACT','PLATFORM VISITS','LIQUIDITY','X AUTHORS (1h)','EVIDENCE'],rows.map(t=>[format(t.chain_rank),subject(t.symbol||t.name,`${t.chain.toUpperCase()} / ${short(t.address)}`,()=>showDetail(t,true)),metric(t.platform_visits_1h),el('span',money(t.liquidity_usd),'metric'),metric(t.social?.windows['1h'].authors??null),badge(t.stale?'STALE':t.social?'EXACT CA MENTION':'PROVIDER ONLY',t.stale)])):empty('No token candidates yet','Connect a personal GMGN API key to discover candidates across Solana, BNB Chain, Base and Ethereum.');
  }else{
    const rows=d.kol_events.filter(p=>p.created>d.as_of-windows[state.window]&&matches(p.text+' '+p.username));count=rows.length;
    $('view-note').textContent='Posts by your explicitly configured KOL accounts. Original posts, quotes, replies and reposts retain their source evidence. No paid-promotion inference.'+(d.kol_events_truncated?' Showing the latest 100 events; full history is available through the events API.':'');
    content=el('div',undefined,'kol-list');if(rows.length)rows.forEach(p=>content.append(evidence(p)));else content.append(empty('No curated KOL events','Add accounts to kol_accounts and include them in your X queries. A follower count alone does not label an account as a KOL.'));
  }
  $('board').replaceChildren(content);$('result-count').textContent=`${count} OBSERVATIONS`;
  const sources=d.sources.length?d.sources:[{source:'gmgn',status:'unconfigured',detail:'Start the collector with personal GMGN credentials.'},{source:'x',status:'unconfigured',detail:'Configure an X bearer token and explicit topic queries.'}];
  $('source-list').replaceChildren(...sources.map(s=>{const n=el('div',undefined,'source'),label=el('div');label.append(el('div',s.source.toUpperCase(),'source-title'),el('p',s.detail));n.append(label,badge(s.status.replaceAll('_',' ').toUpperCase(),s.status!=='ok'));return n;}));
}
async function refresh(){if(state.busy)return;state.busy=true;$('refresh').disabled=true;const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),10000);try{const r=await fetch('/api/v1/board',{signal:controller.signal});if(!r.ok)throw new Error('response');state.data=await r.json();$('error-banner').hidden=true;render();}catch(_){$('error-banner').hidden=false;$('error-banner').textContent='CONNECTION LOST / Displayed observations may be outdated. Collection status cannot be confirmed.';}finally{clearTimeout(timer);state.busy=false;$('refresh').disabled=false;}}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{state.view=button.dataset.view;document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',String(b===button)));render();}));
$('chain').addEventListener('change',e=>{state.chain=e.target.value;render();});$('window').addEventListener('change',e=>{state.window=e.target.value;render();});$('search').addEventListener('input',e=>{state.query=e.target.value;render();});$('refresh').addEventListener('click',refresh);$('close-detail').addEventListener('click',()=>$('detail').close());
refresh();setInterval(refresh,15000);
