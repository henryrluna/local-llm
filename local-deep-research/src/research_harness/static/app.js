const $ = (selector) => document.querySelector(selector);
const jobsEl = $('#jobs');
const dialog = $('#jobDialog');
let selectedJob = new URLSearchParams(location.search).get('job');
let healthCheckSequence = 0;

function lines(id){ return $(id).value.split('\n').map(x=>x.trim()).filter(Boolean); }
function escapeHtml(text=''){ const div=document.createElement('div'); div.textContent=text; return div.innerHTML; }
function fmtDate(value){ return value ? new Date(value).toLocaleString() : ''; }
function fmtElapsed(seconds=0){ const value=Math.max(0,Number(seconds)||0); return `${Math.floor(value/60)}m ${String(Math.floor(value%60)).padStart(2,'0')}s`; }
function metricItems(job){
  const m=job.metrics||{};
  if(!m.phase) return [];
  return [
    ['Overall',`${job.progress}%`],
    ['Sources',m.source_count??'—'],
    ['Model call',`${m.call_index||0}/${m.total_calls||0}`],
    ['Generated',`${m.estimated?'~':''}${m.generated_tokens||0} tokens`],
    ['Speed',`${Number(m.tokens_per_second||0).toFixed(1)} tok/s`],
    ['Call time',fmtElapsed(m.elapsed_seconds)],
  ];
}
function liveMetrics(job,compact=false){
  const items=metricItems(job);
  if(!items.length) return '';
  if(compact) return `<p class="metric-line">${items.map(([key,value])=>`${key}: <strong>${escapeHtml(String(value))}</strong>`).join(' · ')}</p>`;
  const updated=job.metrics?.updated_at?`Updated ${fmtDate(job.metrics.updated_at)} · generation figures refresh every 60 seconds`:'';
  return `<section class="live-status"><p class="live-label">Live model status</p><div class="metric-grid">${items.map(([key,value])=>`<div><span>${escapeHtml(key)}</span><strong>${escapeHtml(String(value))}</strong></div>`).join('')}</div><p class="metric-updated">${escapeHtml(updated)}</p></section>`;
}

async function api(path, options={}){
  const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
  let body={}; try{ body=await response.json(); }catch{}
  if(!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

async function health(){
  const sequence=++healthCheckSequence;
  const button=$('#healthButton');
  try{
    const h=await api('/api/health');
    if(sequence!==healthCheckSequence) return;
    const searchLabel=h.searxng_reachable?'search ready':(h.bing_fallback_configured?'Bing fallback':'search offline');
    const bits=[h.ollama_reachable?'Ollama ready':'Ollama offline', searchLabel];
    button.querySelector('span').textContent=bits.join(' · ');
    button.classList.toggle('ok', h.ollama_reachable && h.web_search_ready);
    button.title=h.phone_url ? `Phone: ${h.phone_url} · Click to recheck` : 'Click to recheck';
  }catch(e){
    if(sequence!==healthCheckSequence) return;
    button.classList.remove('ok');
    button.querySelector('span').textContent='Service check failed';
    button.title=`${e.message || 'Health request failed'} · Click to retry`;
  }
}

function jobCard(job){
  return `<article class="job" data-id="${job.id}">
    <div><h3>${escapeHtml(job.question)}</h3><p>${escapeHtml(job.status_message)} · ${fmtDate(job.created_at)}</p>${liveMetrics(job,true)}</div>
    <span class="badge ${job.status}">${job.status.replace('_',' ')}</span>
    <div class="progress"><i style="width:${job.progress}%"></i><span>${job.progress}%</span></div>
  </article>`;
}

async function loadJobs(){
  try{
    const jobs=await api('/api/jobs');
    jobsEl.innerHTML=jobs.length?jobs.map(jobCard).join(''):'<p class="empty">No research jobs yet.</p>';
    jobsEl.querySelectorAll('.job').forEach(el=>el.addEventListener('click',()=>openJob(el.dataset.id)));
    if(selectedJob){ const id=selectedJob; selectedJob=null; openJob(id); }
  }catch(e){ jobsEl.innerHTML=`<p class="error">${escapeHtml(e.message)}</p>`; }
}

async function openJob(id){
  try{
    const job=await api(`/api/jobs/${id}`);
    const terminal=['completed','failed'].includes(job.status);
    $('#jobDetail').innerHTML=`
      <p class="eyebrow">${job.id.slice(0,8)} · ${escapeHtml(job.provider)}</p>
      <h2>${escapeHtml(job.question)}</h2>
      <p><span class="badge ${job.status}">${job.status.replace('_',' ')}</span> ${escapeHtml(job.status_message)}</p>
      <div class="progress"><i style="width:${job.progress}%"></i><span>${job.progress}%</span></div>
      ${liveMetrics(job)}
      ${job.error?`<p class="detail-error">${escapeHtml(job.error)}</p>`:''}
      <div class="actions">
        ${job.report_available?`<a class="download" href="/api/jobs/${id}/report">Download PDF</a>`:''}
        ${!terminal?`<button data-action="stop">Stop</button>`:''}
        ${['failed','needs_attention'].includes(job.status)?`<button data-action="retry">Retry / resume</button>`:''}
      </div>
      <div class="timeline">${job.events.map(e=>`<div class="event"><strong>${escapeHtml(e.message)}</strong><br><time>${fmtDate(e.created_at)}</time></div>`).join('')}</div>`;
    $('#jobDetail').querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',async()=>{
      button.disabled=true;
      try{ await api(`/api/jobs/${id}/${button.dataset.action}`,{method:'POST'}); await openJob(id); await loadJobs(); }catch(e){ alert(e.message); }
    }));
    if(!dialog.open) dialog.showModal();
  }catch(e){ alert(e.message); }
}

$('#jobForm').addEventListener('submit',async(event)=>{
  event.preventDefault(); const button=event.submitter; button.disabled=true; $('#formError').textContent='';
  try{
    const job=await api('/api/jobs',{method:'POST',body:JSON.stringify({
      question:$('#question').value, provider:$('#provider').value, use_web:$('#useWeb').checked,
      include_x:$('#includeX').checked, seed_urls:lines('#seedUrls'), substack_feeds:lines('#substackFeeds'), browser_fallback_urls:[]
    })});
    $('#question').value=''; await loadJobs(); await openJob(job.id);
  }catch(e){ $('#formError').textContent=e.message; }finally{ button.disabled=false; }
});
$('#refresh').addEventListener('click',loadJobs);
$('#healthButton').addEventListener('click',health);
$('.dialog-close').addEventListener('click',()=>dialog.close());
dialog.addEventListener('click',e=>{ if(e.target===dialog) dialog.close(); });
health(); loadJobs(); setInterval(()=>{ loadJobs(); if(dialog.open){ const id=$('#jobDetail .eyebrow')?.textContent?.split(' · ')[0]; const card=[...document.querySelectorAll('.job')].find(x=>x.dataset.id.startsWith(id)); if(card) openJob(card.dataset.id); } },5000);
setInterval(health,30000);
