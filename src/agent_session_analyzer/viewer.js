'use strict';
const $ = id => document.getElementById(id);
const initialQuery = new URLSearchParams(location.search);
let files = [], selected = null, loadVersion = 0, scanVersion = 0;
let activeRoots = initialQuery.has('root') ? initialQuery.getAll('root') : null;
let experiments = [];
const groupState = new Map();
const fmt = n => n == null ? '未提供' : Number(n).toLocaleString('zh-CN',{maximumFractionDigits:6});
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
function el(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text!=null)n.textContent=text; return n; }
function badge(text, kind='') { return el('span','badge '+kind,text); }
function notice(text, error=false) { $('message').replaceChildren(); if(text)$('message').append(el('div','notice'+(error?' error':''),text)); }
function extra(title, value) { const d=el('details','extra');d.append(el('summary','',title),el('pre','',pretty(value)));return d; }
function block(parent,title,value,cls='answer') { parent.append(el('div','block-title',title),el('div',cls,value == null ? '未提供' : value)); }
function duration(start,end) { return start != null && end != null && end>=start ? ((end-start)/1000).toFixed(2)+' s' : '耗时未知'; }
async function api(url, options) { const r=await fetch(url, options); const data=await r.json(); if(!r.ok)throw new Error(data.error||r.statusText); return data; }
function updateFilters() {
  for(const [id,values,label] of [
    ['filter-harness',files.map(f=>f.harness),'全部 Harness'],
    ['filter-model',files.flatMap(f=>f.models),'全部模型'],
    ['filter-experiment',files.map(f=>f.experiment),'全部实验'],
    ['filter-condition',files.map(f=>f.condition).filter(Boolean),'全部 condition']
  ]) {
    const select=$(id), previous=select.value;
    select.replaceChildren(new Option(label,''));
    for(const value of [...new Set(values)].sort((a,b)=>a.localeCompare(b,'zh-CN')))select.add(new Option(value,value));
    if(values.includes(previous))select.value=previous;
  }
}
function fileButton(f) {
  const b=el('button','file'+(selected===f.id?' active':''));
  b.setAttribute('aria-pressed',String(selected===f.id));
  b.title=`${f.title}\n${f.root} / ${f.name}`;
  b.append(el('span','file-title',f.title),el('small','file-path',f.name));
  if(f.condition)b.append(el('small','',`${f.condition} · repeat ${f.repeat??'未知'}`));
  b.append(el('small','',new Date(f.modified_ms).toLocaleString('zh-CN')+' · '+(f.size/1024).toFixed(1)+' KB'));
  if(f.error)b.append(el('small','file-error',f.error));
  b.addEventListener('click',()=>load(f.id));return b;
}
function fileGroups(parent, items, depth=0, path=[]) {
  if(depth===3) { for(const f of items)parent.append(fileButton(f));return; }
  const groups=new Map();
  for(const f of items) {
    const labels=depth===0?[f.harness]:depth===1?f.models:[f.experiment];
    for(const label of labels) {
      if(depth===1&&$('filter-model').value&&label!==$('filter-model').value)continue;
      if(!groups.has(label))groups.set(label,[]);
      groups.get(label).push(f);
    }
  }
  for(const [label,rows] of [...groups].sort(([a],[b])=>a.localeCompare(b,'zh-CN'))) {
    const key=JSON.stringify([...path,label]), group=el('details','file-group');
    group.open=groupState.get(key)??true;
    const head=el('summary','');
    head.append(el('span','',label),el('small','',rows.length));group.append(head);
    group.addEventListener('toggle',()=>groupState.set(key,group.open));
    fileGroups(group,rows,depth+1,[...path,label]);parent.append(group);
  }
}
function fileList() {
  $('files').replaceChildren(); const q=$('search').value.trim().toLowerCase();
  const harness=$('filter-harness').value, model=$('filter-model').value, experiment=$('filter-experiment').value;
  const condition=$('filter-condition').value;
  const visible=files.filter(f=>(!harness||f.harness===harness)&&(!model||f.models.includes(model))&&
    (!experiment||f.experiment===experiment)&&(!condition||f.condition===condition)&&
    [f.title,f.task_id,f.name,f.root,f.harness,...f.models,f.experiment,f.condition].join(' ').toLowerCase().includes(q));
  $('file-count').textContent=`${visible.length} / ${files.length} 个 trace · Harness → 模型 → 实验`;
  fileGroups($('files'),visible);
  if(!$('files').childElementCount)$('files').append(el('p','muted',files.length?'没有匹配的文件':'目录中还没有 JSONL 文件'));
}
function showRoots() {
  $('selected-roots').replaceChildren();
  for(const root of activeRoots||[]) {
    const row=el('li',''), label=el('span','',root), remove=el('button','remove-root','移除');
    label.title=root;remove.type='button';remove.setAttribute('aria-label','移除目录 '+root);
    remove.addEventListener('click',()=>refresh(activeRoots.filter(value=>value!==root)));
    row.append(label,remove);$('selected-roots').append(row);
  }
  $('roots').textContent=activeRoots?.join('\n')||'尚未设置';
}
function scanBusy(busy) {
  $('choose-root').disabled=busy;$('refresh').disabled=busy;
  for(const button of document.querySelectorAll('.remove-root'))button.disabled=busy;
}
async function chooseRoot() {
  scanBusy(true);notice('请在系统窗口中选择扫描目录…');
  try {
    const data=await api('/api/choose-directory',{method:'POST',headers:{'X-Trace-Viewer':'1'}});
    if(!data.path) {notice('已取消选择目录');return;}
    await refresh([...new Set([...(activeRoots||[]),data.path])]);
  } catch(e){notice(e.message,true);}
  finally {scanBusy(false);}
}
function rootsQuery(roots) {
  if(roots===null)return '';
  const query=new URLSearchParams();
  for(const root of roots.length?roots:[''])query.append('root',root);
  return query.toString();
}
function clearTrace() {
  selected=null;$('metrics').replaceChildren();$('timeline').replaceChildren();
  $('coverage').textContent='';$('source-section').hidden=true;$('source').textContent='';
  $('trial-summary').hidden=true;
}
async function refresh(roots=activeRoots) {
  const request=++scanVersion;++loadVersion;
  scanBusy(true);notice('正在扫描…');
  try {
    const data=await api('/api/files?'+rootsQuery(roots));
    if(request!==scanVersion)return;
    ++loadVersion;activeRoots=data.roots;files=data.files;experiments=data.experiments||[];
    // Preserve the user's explicit scan roots across refreshes and restarts.
    // An empty root also preserves "remove all" when the CLI has default roots.
    const address=new URL(location.href);
    address.searchParams.delete('root');
    for(const root of activeRoots.length?activeRoots:[''])address.searchParams.append('root',root);
    history.replaceState(null,'',address);
    updateExperiments();
    showRoots();scanBusy(true);updateFilters();fileList();
    if(selected&&files.some(f=>f.id===selected))await load(selected);
    else if(experiments.length)await openComparisonTrace();
    else if(files.length)await load(files[0].id);
    else {
      clearTrace();fileList();
      $('title').textContent=activeRoots.length?'暂无运行记录':'请选择扫描目录';
      $('subtitle').textContent=activeRoots.length?'目录中没有 JSONL 文件，可选择其他目录。':'点击左侧“选择扫描目录…”，在系统窗口中选择日志目录。';
      $('timeline').append(el('div','empty','可添加多个目录；按 Harness、模型和实验查找任务。'));notice('');
    }
  } catch(e){if(request===scanVersion)notice(e.message,true);}
  finally {if(request===scanVersion)scanBusy(false);}
}
async function load(id) {
  selected=id;fileList();const request=++loadVersion;notice('正在读取…');
  try {const trace=await api('/api/trace?id='+encodeURIComponent(id)+'&'+rootsQuery(activeRoots));if(request!==loadVersion)return;render(trace);}
  catch(e){if(request!==loadVersion)return;notice(e.message,true);$('title').textContent='无法读取此日志';$('subtitle').textContent=files.find(f=>f.id===id)?.name||'';$('metrics').replaceChildren();$('coverage').textContent='';$('timeline').replaceChildren();$('source-section').hidden=true;$('trial-summary').hidden=true;}
}

function updateExperiments() {
  const select=$('compare-experiment'), previous=select.value;
  select.replaceChildren();
  for(const experiment of experiments)select.add(new Option(`${experiment.name} · ${experiment.directory}`,experiment.id));
  const preferred=experiments.some(e=>e.id===previous)?previous:files.find(f=>experiments.some(e=>e.id===f.experiment_id))?.experiment_id;
  if(preferred)select.value=preferred;
  $('experiment-section').hidden=!experiments.length;
  updateComparisonTasks();
}
function updateComparisonTasks() {
  const experiment=experiments.find(e=>e.id===$('compare-experiment').value);
  const select=$('compare-task'), previous=select.value;
  select.replaceChildren(new Option('全部任务',''));
  for(const task of [...new Set((experiment?.trials||[]).map(t=>t.task_id))].sort())select.add(new Option(task,task));
  if([...select.options].some(o=>o.value===previous))select.value=previous;
  renderComparison();
}
function openComparisonTrace() {
  const experiment=experiments.find(e=>e.id===$('compare-experiment').value);
  if(!experiment)return;
  const task=$('compare-task').value;
  const candidates=files.filter(f=>f.experiment_id===experiment.id&&(!task||f.task_id===task));
  if(candidates.some(f=>f.id===selected))return;
  if(candidates.length)return load(candidates[0].id);
  // Do not leave another experiment's metrics below a result with no trace.
  ++loadVersion;clearTrace();fileList();notice('');
  $('title').textContent='所选实验没有可读取的 trace';
  $('subtitle').textContent=experiment.directory;
  $('timeline').append(el('div','empty','当前实验或任务没有关联的 trace，请查看上方 trial 状态和错误。'));
}
function comparisonTable(container, headings) {
  container.replaceChildren();const table=el('table','comparison-table'), head=el('thead',''), row=el('tr','');
  for(const heading of headings){const cell=el('th','',heading);cell.scope='col';row.append(cell);}
  head.append(row);const body=el('tbody','');table.append(head,body);container.append(table);return body;
}
function average(rows, value) {
  const known=rows.map(value).filter(v=>typeof v==='number'&&Number.isFinite(v));
  return `${known.length?fmt(known.reduce((a,b)=>a+b,0)/known.length):'未知'} (${known.length}/${rows.length})`;
}
function renderComparison() {
  const experiment=experiments.find(e=>e.id===$('compare-experiment').value);
  if(!experiment)return;
  const task=$('compare-task').value, trials=experiment.trials.filter(t=>!task||t.task_id===task);
  $('experiment-status').textContent=`${experiment.status} · 已记录 ${experiment.trials.length} / ${experiment.planned_trials??'未知'} 个 trial · ${experiment.directory}`;
  $('experiment-warnings').replaceChildren();
  if(experiment.warnings.length)$('experiment-warnings').append(el('div','notice error',experiment.warnings.join('\n')));
  const summary=comparisonTable($('condition-summary'),['Condition','已记录 / 有评分','平均 reward','平均输入 token','平均输出 token','平均费用 USD','平均工具调用','平均耗时 s']);
  for(const condition of experiment.conditions) {
    const rows=trials.filter(t=>t.condition===condition), tr=el('tr','');
    const values=[condition,`${rows.length} / ${rows.filter(t=>t.reward!=null).length}`,average(rows,t=>t.reward),
      average(rows,t=>t.usage.input_tokens),average(rows,t=>t.usage.output_tokens),average(rows,t=>t.usage.cost_usd),
      average(rows,t=>t.tool_calls),average(rows,t=>t.duration_sec)];
    for(const value of values)tr.append(el('td','',value));summary.append(tr);
  }
  const matrix=comparisonTable($('trial-comparison'),['任务 / repeat',...experiment.conditions]);
  const pairs=new Map();
  for(const trial of trials) {
    const key=JSON.stringify([trial.task_id,trial.repeat]);
    if(!pairs.has(key))pairs.set(key,[]);pairs.get(key).push(trial);
  }
  for(const [key,rows] of [...pairs].sort(([a],[b])=>a.localeCompare(b))) {
    const [taskId,repeat]=JSON.parse(key), tr=el('tr','');tr.append(el('th','',`${taskId} / ${repeat}`));
    for(const condition of experiment.conditions) {
      const td=el('td',''), matching=rows.filter(t=>t.condition===condition);
      if(!matching.length)td.append(el('span','muted','尚无结果'));
      for(const trial of matching) {
        const file=files.find(f=>f.id===trial.trace_id);
        const cell=el('div','trial-cell');
        cell.append(badge(trial.status||'未知',trial.status==='completed'?'good':'bad'),el('strong','',`reward ${fmt(trial.reward)}`));
        cell.append(el('small','',`Harness：${file?.harness||'未知'}`));
        cell.append(el('small','',`模型：${trial.model||'未知'}`));
        cell.append(el('small','',`输入 / 输出 token：${fmt(trial.usage.input_tokens)} / ${fmt(trial.usage.output_tokens)}`));
        cell.append(el('small','',`费用 USD：${fmt(trial.usage.cost_usd)} · 工具：${fmt(trial.tool_calls)}`));
        cell.append(el('small','',`耗时：${fmt(trial.duration_sec)} s · 结束：${trial.finish_reason||'未知'}`));
        if(file) {
          const open=el('button','open-trace','查看 trace');open.type='button';
          open.addEventListener('click',async()=>{await load(file.id);$('title').scrollIntoView({block:'start'});});cell.append(open);
          if(file.error)cell.append(el('small','comparison-error',file.error));
        } else cell.append(el('small','comparison-error',trial.trace_error||'扫描范围内没有可读取的 trace'));
        if(trial.errors?.length)cell.append(extra('trial 错误',trial.errors));
        if(matching.length>1)cell.append(el('small','comparison-error',`重复配对：${trial.trial_id}`));
        td.append(cell);
      }
      tr.append(td);
    }
    matrix.append(tr);
  }
  if(!pairs.size)$('trial-comparison').append(el('p','muted','尚无已记录的 trial。刷新可读取后续结果。'));
}
function attemptCard(e) {
  const d=e.data||{}, card=el('details','attempt');card.open=true;
  const summary=el('summary','',`Model attempt ${d.attempt_index} · ${e.model?.model||'模型未提供'} `);
  const labels={committed:'已提交',completed:'已返回',failed:'请求失败',event_only:'由事件推导 / completion 未记录',uncommitted:'未提交 / 失败尝试',interrupted:'已中断'};
  summary.append(badge(labels[d.status]||d.status||'未知',['committed','completed'].includes(d.status)?'good':'bad'));
  card.append(summary);const body=el('div','card-body');
  body.append(el('p','muted',`Provider: ${e.model?.provider||'未提供'} · 结束原因: ${d.finish_reason==null?'未提供':pretty(d.finish_reason)}`));
  if(d.status==='event_only')body.append(el('p','muted','此条记录由模型响应关联的事件推导；日志未保存该次 completion 的 usage 和结束原因，不能从工具结果或 trial 累计用量还原。'));
  const tokens=el('div','token-row');
  for(const [key,label] of Object.entries({total_tokens:'总 token',input_tokens:'输入（原始）',output_tokens:'输出',cache_read_tokens:'缓存读取',reasoning_tokens:'思考'}))tokens.append(el('span','token',`${label} ${fmt(d.usage?.[key])}`));
  body.append(tokens);
  block(body,'思考内容',d.reasoning,'reasoning');block(body,'模型输出',d.text);
  if(d.thought)block(body,'动作说明（thought）',pretty(d.thought));
  if(d.error)body.append(el('div','block-title','请求错误'),el('pre','',pretty(d.error)));
  body.append(extra('查看原始事件与 stream',e.raw||e));card.append(body);return card;
}
function toolCard(call,result) {
  const card=el('details','tool');
  const failed=result?.data?.is_error===true;const status=!result?'等待结果 / 未观测到结果':result?.data?.rejected?'调用被拒绝':failed?'工具错误':'已返回';
  const summary=el('summary','',`Tool · ${call?.data?.name||'未知工具'} `);summary.append(badge(status,failed?'bad':result?'good':''));
  card.append(summary);const body=el('div','card-body');
  body.append(el('p','muted',`callId: ${call?.tool_call_id||result?.tool_call_id||'未提供'} · 调用到结果: ${duration(call?.time_ms,result?.time_ms)}`));
  block(body,'关联模型尝试',call?.attempt_id||result?.attempt_id||'未知');
  if(call?.data?.thought)block(body,'动作说明（thought）',pretty(call.data.thought));
  if(call?.data?.reasoning_fields?.reasoning)block(body,'动作事件中的思考',call.data.reasoning_fields.reasoning,'reasoning');
  const args=call?.data?.arguments;let parsed=args;try{parsed=JSON.parse(args);}catch{}
  body.append(el('div','block-title','工具参数'),el('pre','',pretty(parsed)??'未提供'));
  body.append(el('div','block-title','工具结果'),el('pre','',result?pretty(result.data.content)??'未提供':'尚未记录到结果'));
  if(result?.data?.error)body.append(el('pre','',pretty(result.data.error)));
  body.append(extra('原始调用 / 结果事件',{call:call?.raw,result:result?.raw}));card.append(body);return card;
}
function render(trace) {
  const metadata=trace.metadata;
  $('title').textContent=metadata.title;
  $('subtitle').textContent=`${metadata.harness} · ${metadata.models.join(' / ')} · ${metadata.experiment}${metadata.condition?' · '+metadata.condition+' / repeat '+metadata.repeat:''}\n${trace.format} · ${trace.file} · Session: ${trace.session_id||'未知'}`;
  const trial=trace.trial;
  if(trial&&experiments.some(e=>e.id===trial.experiment_id)) {
    const experimentSelect=$('compare-experiment');
    if(experimentSelect.value!==trial.experiment_id) {
      experimentSelect.value=trial.experiment_id;updateComparisonTasks();
    }
    const taskSelect=$('compare-task');
    if(taskSelect.value&&taskSelect.value!==trial.task_id) {
      taskSelect.value=trial.task_id;renderComparison();
    }
  }
  $('trial-summary').hidden=!trial;
  if(trial)$('trial-summary').textContent=`Trial 汇总：${trial.status} · reward ${fmt(trial.reward)} · 输入 / 输出 token ${fmt(trial.usage.input_tokens)} / ${fmt(trial.usage.output_tokens)} · 费用 USD ${fmt(trial.usage.cost_usd)} · 工具 ${fmt(trial.tool_calls)} · 全流程耗时 ${fmt(trial.duration_sec)} s。此处来自实验结果，不分摊到模型尝试。`;
  const failures=trace.events.filter(e=>e.event==='run.failed').map(e=>`运行异常：${e.data?.error_type||''} ${e.data?.message||''}`);
  const captureWarnings=trace.events.filter(e=>e.event==='capture.warning').map(e=>e.data?.message).filter(Boolean);
  notice([...trace.warnings,...failures,...new Set(captureWarnings)].join('\n'),failures.length>0);const s=trace.summary,t=s.tokens;const partial=s.usage_coverage.total_tokens<s.attempts;
  $('metrics').replaceChildren();
  for(const [label,value,detail] of [['总 token'+(partial?'（已知部分）':''),fmt(t.total_tokens),'只累计每次 attempt 的汇总 usage'],['模型尝试',s.attempts,'包括已观测到的失败 / 重试'],['工具调用',s.tools,'模型请求的工具调用数'],['轮次 / 步骤',`${s.turns} / ${s.steps}`,'Turn / Step']]){const c=el('div','metric');c.append(el('div','label',label),el('strong','',value),el('small','',detail));$('metrics').append(c);}
  $('coverage').textContent=`总 token 覆盖 ${s.usage_coverage.total_tokens}/${s.attempts} 个尝试 · 输入/缓存/思考保留来源口径，不重复相加。工具耗时是日志事件间隔；OpenHands step 为派生分组，耗时未知。`;
  const turns=new Map(),outside=[];
  for(const e of trace.events){if(e.turn_id==null){outside.push(e);continue;}if(!turns.has(e.turn_id))turns.set(e.turn_id,[]);turns.get(e.turn_id).push(e);}
  $('timeline').replaceChildren();
  for(const [id,events] of turns){
    const start=events.find(e=>e.event==='turn/start'),end=events.findLast(e=>e.event==='turn/end');
    const turn=el('details','turn');turn.open=true;const head=el('summary','',`Turn ${id} `);
    const status=end?.data?.reason?.kind||'未结束';head.append(badge(status,status==='completed'?'good':end?'bad':''),el('span','muted',duration(start?.time_ms,end?.time_ms)));turn.append(head);
    const prompts=events.filter(e=>e.event==='user/message').map(e=>{const c=e.data?.content;return Array.isArray(c)?c.filter(b=>b.type==='text').map(b=>b.text).join(''):pretty(c);}).filter(Boolean);
    if(prompts.length)turn.append(el('div','prompt',prompts.join('\n\n')));
    const steps=new Map();for(const e of events){if(e.step_id==null)continue;if(!steps.has(e.step_id))steps.set(e.step_id,[]);steps.get(e.step_id).push(e);}
    const stepsNode=el('div','steps');
    for(const [stepId,rows] of steps){const step=el('section','step');const start=rows.find(e=>e.event==='step/start'),end=rows.findLast(e=>e.event==='step/end');const h=el('div','step-heading',`Step ${stepId}`);h.append(el('span','muted',duration(start?.time_ms,end?.time_ms)));if(!end)h.append(badge('未结束'));step.append(h);
      for(const row of rows.filter(r=>r.event==='model.attempt'))step.append(attemptCard(row));
      const used=new Set();for(const row of rows){if(row.event==='tool.call'){const index=rows.findIndex((r,i)=>!used.has(i)&&r.event==='tool.result'&&r.tool_call_id!=null&&r.tool_call_id===row.tool_call_id);if(index>=0)used.add(index);step.append(toolCard(row,index>=0?rows[index]:null));}}
      rows.forEach((r,i)=>{if(r.event==='tool.result'&&!used.has(i))step.append(toolCard(null,r));});
      step.append(extra('步骤事件',rows));stepsNode.append(step);
    }
    if(!steps.size)stepsNode.append(el('p','muted','本轮未进入 step，或未采集到 step 事件。'));
    turn.append(stepsNode);$('timeline').append(turn);
  }
  if(!turns.size)$('timeline').append(el('div','empty','文件中尚无 turn 事件；下方可查看采集状态。'));
  if(outside.length)$('timeline').append(extra('会话信息 / 运行状态 / 轮次外事件',outside));
  $('source-section').hidden=false;$('source').textContent=trace.events.map(e=>JSON.stringify(e)).join('\n');
}
$('refresh').addEventListener('click',()=>refresh());
$('choose-root').addEventListener('click',chooseRoot);
for(const id of ['filter-harness','filter-model','filter-experiment','filter-condition'])$(id).addEventListener('change',fileList);
$('compare-experiment').addEventListener('change',()=>{updateComparisonTasks();openComparisonTrace();});
$('compare-task').addEventListener('change',()=>{renderComparison();openComparisonTrace();});
$('search').addEventListener('input',fileList);
refresh();
