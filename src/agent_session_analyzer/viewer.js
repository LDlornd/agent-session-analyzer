'use strict';
const $ = id => document.getElementById(id);
const initialQuery = new URLSearchParams(location.search);
let activeView = initialQuery.get('view')==='compare'?'compare':'explorer';
let files = [], selected = null, loadVersion = 0, scanVersion = 0;
let activeRoots = initialQuery.has('root') ? initialQuery.getAll('root') : null;
let experiments = [];
let navigationMode = null;
const groupState = new Map();
const fmt = n => n == null ? '未提供' : Number(n).toLocaleString('zh-CN',{maximumFractionDigits:6});
const TRIAL_STAGES = [['prepare','任务准备'],['environment','环境初始化'],['setup','环境设置'],
  ['agent','Agent 执行'],['evaluate','结果评分'],['cleanup','环境清理']];
const seconds = value => value == null ? '未记录' :
  (value>0&&value<0.001?'<0.001':value.toLocaleString('zh-CN',{maximumFractionDigits:3}))+' s';
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
function el(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text!=null)n.textContent=text; return n; }
function badge(text, kind='') { return el('span','badge '+kind,text); }
function metricCard(label, value, detail, tone) {
  const card=el('div','metric metric-'+tone);
  card.append(el('div','label',label),el('strong','',value),el('small','',detail));return card;
}
function notice(text, error=false) { $('message').replaceChildren(); if(text)$('message').append(el('div','notice'+(error?' error':''),text)); }
function extra(title, value) { const d=el('details','extra');d.append(el('summary','',title),el('pre','',pretty(value)));return d; }
function block(parent,title,value,cls='answer') { parent.append(el('div','block-title',title),el('div',cls,value == null ? '未提供' : value)); }
function duration(start,end) { return start != null && end != null && end>=start ? ((end-start)/1000).toFixed(2)+' s' : '耗时未知'; }
function stageBreakdown(trial) {
  const container=el('div','stage-breakdown'), values=trial.stage_durations_sec||{};
  const known=TRIAL_STAGES.map(([key])=>values[key]).filter(value=>value!=null);
  const longest=known.length?Math.max(...known):null;
  const total=known.length?known.reduce((sum,value)=>sum+value,0):null;
  container.append(el('p','stage-overview',`已记录 ${known.length}/${TRIAL_STAGES.length} 个阶段 · 已记录阶段合计 ${seconds(total)}`));
  for(const [key,label] of TRIAL_STAGES) {
    const value=values[key], isLongest=longest>0&&value===longest;
    const row=el('div','stage-row'+(isLongest?' longest':'')), name=el('span','stage-name',label);
    if(isLongest)name.append(badge('最长'));
    const bar=el(value==null?'span':'meter','stage-meter'+(value==null?' missing':''));
    bar.setAttribute('aria-hidden','true');
    if(value!=null){bar.min=0;bar.max=longest||1;bar.value=value;}
    row.append(name,bar,el('span','stage-value',seconds(value)));container.append(row);
  }
  container.append(el('p','muted',known.length
    ? '条形长度按当前最长的已记录阶段缩放；未记录值不计为零，阶段合计不等同于全流程耗时。'
    : '此记录尚无阶段耗时；旧实验不会从总耗时推算各阶段。'));
  return container;
}
function renderTrialStages(trial) {
  const container=$('trial-stages');container.replaceChildren();container.hidden=!trial;
  if(!trial)return;
  const heading=el('div','section-heading');heading.append(el('h2','','阶段耗时'),el('span','section-label','当前 trial · 单位 s'));
  container.append(heading,stageBreakdown(trial));
}
async function api(url, options) { const r=await fetch(url, options); const data=await r.json(); if(!r.ok)throw new Error(data.error||r.statusText); return data; }
function experimentFor(file) { return experiments.find(experiment=>experiment.id===file.experiment_id); }
function updateView() {
  const comparing=activeView==='compare';
  $('trace-page').hidden=comparing;$('experiment-section').hidden=!comparing;
  $('comparison-empty').hidden=experiments.length>0;$('comparison-content').hidden=!experiments.length;
  $('navigation-mode').hidden=comparing;$('trace-filters').hidden=comparing;
  $('sidebar-heading').textContent=comparing?'选择对比实验':'实验与会话';
  $('search').placeholder=comparing?'搜索实验名称或任务…':'搜索任务、实验、模型或文件名…';
  $('search').setAttribute('aria-label',comparing?'搜索实验':'搜索会话');
  $('files').setAttribute('aria-label',comparing?'可对比的实验':'实验和运行记录');
  document.title=(comparing?'实验对比':'实验查看')+' · Harness Trace';
  for(const view of ['explorer','compare']) {
    const link=$('view-'+view), address=new URL(location.href);
    address.searchParams.set('view',view);link.href=address.href;
    if(view===activeView)link.setAttribute('aria-current','page');
    else link.removeAttribute('aria-current');
  }
}
function switchView(view, {updateHistory=true, focus=false}={}) {
  const changed=activeView!==view;activeView=view;
  if(updateHistory&&changed) {
    const address=new URL(location.href);address.searchParams.set('view',view);
    history.pushState(null,'',address);
  }
  fileList();
  if(changed&&view==='compare')openComparisonTrace({reveal:true});
  if(focus)$(view==='compare'?'comparison-title':'title').focus({preventScroll:true});
}
function navigationFiles() {
  return files.filter(file=>Boolean(experimentFor(file))===(navigationMode==='experiments'));
}
function updateFilters() {
  const items=navigationFiles();
  for(const [id,values,label] of [
    ['filter-harness',items.map(f=>f.harness),'全部 Harness'],
    ['filter-model',items.flatMap(f=>f.models),'全部模型'],
    ['filter-condition',items.map(f=>f.condition).filter(Boolean),'全部 condition']
  ]) {
    const select=$(id), previous=select.value;
    select.replaceChildren(new Option(label,''));
    for(const value of [...new Set(values)].sort((a,b)=>a.localeCompare(b,'zh-CN')))select.add(new Option(value,value));
    if(values.includes(previous))select.value=previous;
  }
}
function fileButton(f, {showRepeat=false}={}) {
  const b=el('button','file'+(selected===f.id?' active':''));
  b.type='button';
  b.setAttribute('aria-pressed',String(selected===f.id));
  b.title=`${f.title}\n${f.root} / ${f.name}`;
  const title=showRepeat?`Repeat ${f.repeat??'未知'}${f.condition?' · '+f.condition:''}`:f.title;
  const heading=el('span','file-heading');heading.append(el('span','file-title',title));
  const status=f.trial?.status;
  const labels={completed:'已完成',error:'失败',cancelled:'已取消',running:'运行中',interrupted:'已中断'};
  if(f.error)heading.append(el('span','file-status error','读取失败'));
  else if(status)heading.append(el('span','file-status',labels[status]||status));
  else if(f.trial?.result_recorded===false)heading.append(el('span','file-status','结果未记录'));
  b.append(heading);
  if(!showRepeat&&f.task_id&&f.task_id!==f.title)b.append(el('small','file-task-id',f.task_id));
  const owners=el('span','file-owners');
  owners.append(el('span','harness-label',f.harness));
  for(const model of f.models)owners.append(el('span','model-label',model));
  b.append(owners);
  if(!showRepeat&&f.condition)b.append(el('small','file-condition',`${f.condition} · repeat ${f.repeat??'未知'}`));
  if(!experimentFor(f)&&f.experiment!=='独立运行')b.append(el('small','file-recording-label',`标签：${f.experiment}`));
  b.append(el('small','file-path',f.name));
  b.append(el('small','',new Date(f.modified_ms).toLocaleString('zh-CN')+' · '+(f.size/1024).toFixed(1)+' KB'));
  if(f.error)b.append(el('small','file-error',f.error));
  b.addEventListener('click',()=>load(f.id,{reveal:true}));return b;
}
function matchingFiles() {
  const query=$('search').value.trim().toLowerCase(), harness=$('filter-harness').value;
  const model=$('filter-model').value, condition=$('filter-condition').value;
  return navigationFiles().filter(f=>(!harness||f.harness===harness)&&(!model||f.models.includes(model))&&
    (!condition||f.condition===condition)&&
    [f.title,f.task_id,f.name,f.root,f.harness,...f.models,f.experiment,f.condition].join(' ').toLowerCase().includes(query));
}
function matchesEmptyExperiment(experiment) {
  if($('filter-harness').value)return false;
  const model=$('filter-model').value, condition=$('filter-condition').value;
  if(model&&!experiment.trials.some(trial=>trial.model===model))return false;
  if(condition&&!experiment.conditions.includes(condition))return false;
  const query=$('search').value.trim().toLowerCase();
  return [experiment.name,experiment.directory,...experiment.trials.map(trial=>trial.task_id)].join(' ').toLowerCase().includes(query);
}
function taskGroupKey(file) {
  return JSON.stringify([file.experiment_id,file.task_id?`task:${file.task_id}`:`trace:${file.id}`]);
}
function taskGroups(parent, items) {
  const groups=new Map();
  for(const file of items) {
    // A missing task ID must not merge unrelated traces with the same title.
    const key=taskGroupKey(file);
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(file);
  }
  for(const [key,rows] of groups) {
    rows.sort((a,b)=>(a.repeat==null)-(b.repeat==null)||
      String(a.repeat??'').localeCompare(String(b.repeat??''),'zh-CN',{numeric:true})||
      (a.condition||'').localeCompare(b.condition||'','zh-CN')||b.modified_ms-a.modified_ms);
    const task=rows[0], group=el('details','task-group'+(rows.some(file=>file.id===selected)?' selected-task':''));
    group.open=groupState.get(key)??false;
    group.setAttribute('aria-label',task.title+'的重复运行');
    const heading=el('summary','task-heading');
    heading.append(el('span','task-name',task.title),el('span','task-count',`${rows.length} 条 trace`));
    group.append(heading);
    if(task.task_id&&task.task_id!==task.title)group.append(el('small','task-id',task.task_id));
    const repeats=el('div','repeat-files');
    for(const file of rows)repeats.append(fileButton(file,{showRepeat:true}));
    heading.addEventListener('click',event=>{
      event.preventDefault();group.open=!group.open;groupState.set(key,group.open);
    });
    group.append(repeats);parent.append(group);
  }
}
function experimentGroups(parent, items) {
  const selectedFile=files.find(file=>file.id===selected);
  const active=selectedFile?.experiment_id||$('compare-experiment').value;
  const groups=experiments.map(experiment=>{
    const all=files.filter(file=>file.experiment_id===experiment.id);
    return {experiment,all,rows:items.filter(file=>file.experiment_id===experiment.id),
      modified:all.reduce((latest,file)=>Math.max(latest,file.modified_ms),0)};
  }).sort((a,b)=>b.modified-a.modified||a.experiment.name.localeCompare(b.experiment.name,'zh-CN'));
  let count=0;
  for(const {experiment,all,rows} of groups) {
    if(!rows.length&&(all.length||!matchesEmptyExperiment(experiment)))continue;
    count++;
    const group=el('details','experiment-group'+(active===experiment.id?' selected-experiment':''));
    group.open=groupState.get(experiment.id)??false;
    const head=el('summary','experiment-heading'), title=el('span','experiment-title');
    title.append(el('span','experiment-name',experiment.name),el('span','experiment-count',all.length?`${rows.length}/${all.length} 条`:'暂无 trace'));
    const metadata=el('span','experiment-owners'), harnesses=[...new Set(all.map(file=>file.harness))];
    const models=[...new Set(all.length?all.flatMap(file=>file.models):experiment.trials.map(trial=>trial.model).filter(Boolean))];
    metadata.append(el('span','harness-label',harnesses.join(' / ')||'尚无执行轨迹'));
    if(models.length){const label=el('span','model-label',models.length===1?models[0]:`${models[0]} +${models.length-1}`);label.title=models.join('\n');metadata.append(label);}
    head.title=experiment.directory;head.append(title,metadata);group.append(head);
    if(experiments.some(other=>other.id!==experiment.id&&other.name===experiment.name))group.append(el('small','experiment-location',experiment.directory));
    const children=el('div','experiment-files');
    taskGroups(children,rows);
    if(!rows.length)children.append(el('p','empty-experiment','尚无可读取的 trace。右侧可查看实验状态与已记录结果。'));
    group.append(children);
    head.addEventListener('click',event=>{
      event.preventDefault();group.open=!group.open;groupState.set(experiment.id,group.open);
      if(group.open)selectExperiment(experiment,rows);
    });
    parent.append(group);
  }
  return count;
}
function comparisonExperimentList(parent) {
  const query=$('search').value.trim().toLowerCase();
  let count=0;
  for(const experiment of experiments) {
    const items=files.filter(file=>file.experiment_id===experiment.id);
    const searchable=[experiment.name,experiment.directory,...experiment.trials.map(trial=>trial.task_id),...items.map(file=>file.title)];
    if(!searchable.join(' ').toLowerCase().includes(query))continue;
    count++;
    const chosen=$('compare-experiment').value===experiment.id;
    const button=el('button','experiment-choice'+(chosen?' active':''));button.type='button';
    button.setAttribute('aria-pressed',String(chosen));button.title=experiment.directory;
    button.append(el('span','experiment-name',experiment.name));
    const metadata=el('span','experiment-choice-meta');
    metadata.append(el('span','',`${experiment.conditions.length} 个条件`),el('span','',`${experiment.trials.length} 个 trial`));
    button.append(metadata,el('small','experiment-choice-path',experiment.directory));
    button.addEventListener('click',()=>selectExperiment(experiment,items));parent.append(button);
  }
  return count;
}
function fileList() {
  $('files').replaceChildren();updateView();
  const independent=files.filter(file=>!experimentFor(file)).length;
  $('experiment-count').textContent=experiments.length;$('standalone-count').textContent=independent;
  for(const [id,mode] of [['nav-experiments','experiments'],['nav-standalone','standalone']])$(id).setAttribute('aria-pressed',String(navigationMode===mode));
  if(activeView==='compare') {
    const count=comparisonExperimentList($('files'));
    $('file-count').textContent=`${count}/${experiments.length} 个实验 · 按条件对比`;
    if(!count)$('files').append(el('p','navigation-empty',experiments.length?'没有匹配的实验，请调整搜索。':'添加实验日志目录后，在这里选择需要对比的实验。'));
    return;
  }
  const visible=matchingFiles(), total=navigationFiles().length;
  if(navigationMode==='experiments') {
    const count=experimentGroups($('files'),visible);
    $('file-count').textContent=`${count} 个实验 · ${visible.length}/${total} 条 trace · 最近运行优先`;
  } else {
    for(const file of visible)$('files').append(fileButton(file));
    $('file-count').textContent=`${visible.length}/${total} 条独立 trace`;
  }
  if(!$('files').childElementCount)$('files').append(el('p','navigation-empty',files.length||experiments.length?'没有匹配的记录，请调整筛选或切换运行类型。':'选择日志目录后，实验和独立运行会显示在这里。'));
}
function showFileInNavigation(file, reveal) {
  const experiment=experimentFor(file), mode=experiment?'experiments':'standalone';
  if(navigationMode!==mode){navigationMode=mode;updateFilters();}
  if(reveal) {
    if(experiment){groupState.set(experiment.id,true);groupState.set(taskGroupKey(file),true);}
    if(!matchingFiles().some(candidate=>candidate.id===file.id)) {
      $('search').value='';
      for(const id of ['filter-harness','filter-model','filter-condition'])$(id).value='';
    }
  }
}
function selectExperiment(experiment, items) {
  $('compare-experiment').value=experiment.id;$('compare-task').value='';updateComparisonTasks();
  if(items.some(file=>file.id===selected)){fileList();return;}
  if(items.length)return load(items[0].id);
  return openComparisonTrace();
}
function switchNavigation(mode) {
  navigationMode=mode;updateFilters();fileList();
  const candidates=matchingFiles();
  if(candidates.some(file=>file.id===selected))return;
  if(candidates.length)return load(candidates[0].id);
  showEmptyTrace('没有匹配的运行记录','可以调整左侧搜索或筛选条件。','当前运行类型下没有可读取的 trace。');
}
function renderTraceContext(metadata) {
  const container=$('trace-context'), experiment=experiments.find(item=>item.id===metadata.experiment_id);
  container.replaceChildren();container.hidden=false;
  const label=experiment?`实验 · ${experiment.name}`:`独立运行${metadata.experiment!=='独立运行'?' · '+metadata.experiment:''}`;
  container.append(el('span','context-experiment',label),
    el('span','context-owner',`Harness · ${metadata.harness}`),el('span','context-owner',`模型 · ${metadata.models.join(' / ')}`));
  if(metadata.condition)container.append(el('span','context-condition',`${metadata.condition} · repeat ${metadata.repeat??'未知'}`));
}
function showRoots() {
  $('selected-roots').replaceChildren();
  for(const root of activeRoots||[]) {
    const row=el('li',''), label=el('span','',root), remove=el('button','remove-root','移除');
    label.title=root;remove.type='button';remove.setAttribute('aria-label','移除目录 '+root);
    remove.addEventListener('click',()=>refresh(activeRoots.filter(value=>value!==root)));
    row.append(label,remove);$('selected-roots').append(row);
  }
  $('root-count').textContent=activeRoots?.length?`${activeRoots.length} 个目录`:'未选择';
  $('root-count').title=activeRoots?.join('\n')||'尚未设置';
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
  renderTrialStages(null);
  $('trace-context').replaceChildren();$('trace-context').hidden=true;
}
function showEmptyTrace(title, subtitle, message) {
  ++loadVersion;clearTrace();fileList();notice('');
  $('title').textContent=title;$('subtitle').textContent=subtitle;
  $('timeline').append(el('div','empty',message));
}
async function refresh(roots=activeRoots) {
  const hadRoots=Boolean(activeRoots?.length);
  const request=++scanVersion;++loadVersion;
  scanBusy(true);notice('正在扫描…');
  try {
    const data=await api('/api/files?'+rootsQuery(roots));
    if(request!==scanVersion)return;
    ++loadVersion;activeRoots=data.roots;files=data.files;experiments=data.experiments||[];
    if(navigationMode===null||!hadRoots)navigationMode=experiments.length?'experiments':'standalone';
    if(!activeRoots.length)$('scan-controls').open=true;
    else if(!hadRoots)$('scan-controls').open=false;
    // Preserve the user's explicit scan roots across refreshes and restarts.
    // An empty root also preserves "remove all" when the CLI has default roots.
    const address=new URL(location.href);
    address.searchParams.delete('root');
    for(const root of activeRoots.length?activeRoots:[''])address.searchParams.append('root',root);
    history.replaceState(null,'',address);
    updateExperiments();
    showRoots();scanBusy(true);updateFilters();fileList();
    if(selected&&files.some(f=>f.id===selected))await load(selected);
    else if(navigationMode==='experiments'&&experiments.length)await openComparisonTrace();
    else if(matchingFiles().length)await load(matchingFiles()[0].id);
    else {
      showEmptyTrace(activeRoots.length?'暂无匹配的运行记录':'请选择扫描目录',
        activeRoots.length?'请调整筛选或切换实验 / 独立运行。':'展开左下方“读取目录”，在系统窗口中选择日志目录。',
        '实验按名称浏览，每条 trace 都会显示 Harness 和模型。');
    }
  } catch(e){if(request===scanVersion)notice(e.message,true);}
  finally {if(request===scanVersion)scanBusy(false);}
}
async function load(id, {reveal=false}={}) {
  const file=files.find(candidate=>candidate.id===id);
  if(file)showFileInNavigation(file,reveal);
  selected=id;fileList();const request=++loadVersion;notice('正在读取…');
  try {const trace=await api('/api/trace?id='+encodeURIComponent(id)+'&'+rootsQuery(activeRoots));if(request!==loadVersion)return;render(trace);}
  catch(e){if(request!==loadVersion)return;notice(e.message,true);$('title').textContent='无法读取此日志';$('subtitle').textContent=files.find(f=>f.id===id)?.name||'';$('metrics').replaceChildren();$('coverage').textContent='';$('timeline').replaceChildren();$('source-section').hidden=true;$('trial-summary').hidden=true;renderTrialStages(null);$('trace-context').replaceChildren();$('trace-context').hidden=true;}
}

function updateExperiments() {
  const select=$('compare-experiment'), previous=select.value;
  select.replaceChildren();
  for(const experiment of experiments)select.add(new Option(`${experiment.name} · ${experiment.directory}`,experiment.id));
  const preferred=experiments.some(e=>e.id===previous)?previous:files.find(f=>experiments.some(e=>e.id===f.experiment_id))?.experiment_id;
  if(preferred)select.value=preferred;
  updateView();
  updateComparisonTasks();
}
function updateComparisonTasks() {
  const experiment=experiments.find(e=>e.id===$('compare-experiment').value);
  const select=$('compare-task'), previous=select.value;
  select.replaceChildren(new Option('全部任务',''));
  const traceTasks=files.filter(f=>f.experiment_id===experiment?.id).map(f=>f.task_id);
  for(const task of [...new Set([...(experiment?.trials||[]).map(t=>t.task_id),...traceTasks].filter(Boolean))].sort())select.add(new Option(task,task));
  if([...select.options].some(o=>o.value===previous))select.value=previous;
  renderComparison();
}
function openComparisonTrace({reveal=false}={}) {
  const experiment=experiments.find(e=>e.id===$('compare-experiment').value);
  if(!experiment)return;
  const task=$('compare-task').value;
  const candidates=files.filter(f=>f.experiment_id===experiment.id&&(!task||f.task_id===task));
  const current=candidates.find(file=>file.id===selected);
  if(current){if(reveal){showFileInNavigation(current,true);fileList();}return;}
  if(candidates.length)return load(candidates[0].id,{reveal});
  // Do not leave another experiment's metrics below a result with no trace.
  showEmptyTrace('所选实验没有可读取的 trace',experiment.directory,
    '当前实验或任务没有关联的 trace，请查看上方 trial 状态和错误。');
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
function renderStageComparison(trials, conditions) {
  const body=comparisonTable($('stage-comparison'),['阶段',...conditions]);
  for(const [key,label] of TRIAL_STAGES) {
    const row=el('tr',''), heading=el('th','',label);heading.scope='row';row.append(heading);
    for(const condition of conditions) {
      const rows=trials.filter(trial=>trial.condition===condition);
      const known=rows.map(trial=>trial.stage_durations_sec?.[key]).filter(value=>value!=null);
      const total=known.length?known.reduce((sum,value)=>sum+value,0):null;
      const cell=el('td','stage-stat');
      cell.append(el('strong','',`平均 ${seconds(known.length?total/known.length:null)}`),
        el('small','',`已记录 ${known.length}/${rows.length}`));
      if(known.length)cell.append(el('small','',`合计 ${seconds(total)} · 最大 ${seconds(Math.max(...known))}`));
      row.append(cell);
    }
    body.append(row);
  }
}
function renderComparison() {
  const experiment=experiments.find(e=>e.id===$('compare-experiment').value);
  $('compare-experiment').disabled=!experiments.length;$('compare-task').disabled=!experiment;
  if(!experiment) {
    for(const id of ['comparison-metrics','condition-summary','stage-comparison','trial-comparison','experiment-warnings'])$(id).replaceChildren();
    $('experiment-status').textContent='';return;
  }
  const task=$('compare-task').value, trials=experiment.trials.filter(t=>!task||t.task_id===task);
  const recorded=trials.filter(trial=>trial.result_recorded!==false);
  const finalizedCount=experiment.trials.filter(trial=>trial.result_recorded!==false).length;
  const snapshots=trials.length-recorded.length;
  const taskIds=new Set([...trials.map(trial=>trial.task_id),
    ...files.filter(file=>file.experiment_id===experiment.id&&(!task||file.task_id===task)).map(file=>file.task_id)].filter(Boolean));
  $('comparison-metrics').replaceChildren(
    metricCard('对比条件',experiment.conditions.length,'所选实验的运行条件','blue'),
    metricCard('可查看任务',taskIds.size,task?'已筛选到单个任务':'结果或 trace 中已识别的任务','cyan'),
    metricCard('已记录 trial',recorded.length,task?'当前任务的最终结果':'所选实验的最终结果','violet'),
    metricCard('已有评分',recorded.filter(trial=>trial.reward!=null).length,'缺失评分不计为零','amber'));
  $('experiment-status').textContent=`${experiment.status} · 已记录 ${finalizedCount} / ${experiment.planned_trials??'未知'} 个 trial${snapshots?` · 当前范围另有 ${snapshots} 个阶段快照`:''} · ${experiment.directory}`;
  $('experiment-warnings').replaceChildren();
  if(experiment.warnings.length)$('experiment-warnings').append(el('div','notice error',experiment.warnings.join('\n')));
  const summary=comparisonTable($('condition-summary'),['Condition','已记录 / 有评分','平均 reward','平均输入 token','平均输出 token','平均费用 USD','平均工具调用','平均耗时 s']);
  for(const condition of experiment.conditions) {
    const rows=recorded.filter(t=>t.condition===condition), tr=el('tr','');
    const values=[condition,`${rows.length} / ${rows.filter(t=>t.reward!=null).length}`,average(rows,t=>t.reward),
      average(rows,t=>t.usage.input_tokens),average(rows,t=>t.usage.output_tokens),average(rows,t=>t.usage.cost_usd),
      average(rows,t=>t.tool_calls),average(rows,t=>t.duration_sec)];
    for(const value of values)tr.append(el('td','',value));summary.append(tr);
  }
  renderStageComparison(trials,experiment.conditions);
  const matrix=comparisonTable($('trial-comparison'),['任务 / repeat',...experiment.conditions]);
  const pairs=new Map();
  for(const trial of trials) {
    const key=JSON.stringify([trial.task_id,trial.repeat]);
    if(!pairs.has(key))pairs.set(key,[]);pairs.get(key).push(trial);
  }
  for(const [key,rows] of [...pairs].sort(([a],[b])=>a.localeCompare(b))) {
    const [taskId,repeat]=JSON.parse(key), tr=el('tr',''), heading=el('th','',`${taskId} / ${repeat}`);
    heading.scope='row';tr.append(heading);
    for(const condition of experiment.conditions) {
      const td=el('td',''), matching=rows.filter(t=>t.condition===condition);
      if(!matching.length)td.append(el('span','muted','尚无结果'));
      for(const trial of matching) {
        const file=files.find(f=>f.id===trial.trace_id);
        const cell=el('div','trial-cell');
        cell.append(badge(trial.status||'未知',trial.status==='completed'?'good':'bad'),el('strong','',`reward ${fmt(trial.reward)}`));
        if(trial.result_recorded===false)cell.append(el('small','','阶段快照 · 评分和用量尚未计入最终汇总'));
        cell.append(el('small','',`Harness：${file?.harness||'未知'}`));
        cell.append(el('small','',`模型：${trial.model||'未知'}`));
        cell.append(el('small','',`输入 / 输出 token：${fmt(trial.usage.input_tokens)} / ${fmt(trial.usage.output_tokens)}`));
        cell.append(el('small','',`费用 USD：${fmt(trial.usage.cost_usd)} · 工具：${fmt(trial.tool_calls)}`));
        cell.append(el('small','',`耗时：${fmt(trial.duration_sec)} s · 结束：${trial.finish_reason||'未知'}`));
        const stages=el('details','extra');stages.append(el('summary','','阶段耗时'),stageBreakdown(trial));cell.append(stages);
        if(file) {
          const open=el('button','open-trace','查看 trace');open.type='button';
          open.addEventListener('click',async()=>{
            switchView('explorer');await load(file.id,{reveal:true});
            if(selected===file.id&&activeView==='explorer'){$('title').focus({preventScroll:true});$('title').scrollIntoView({block:'start'});}
          });cell.append(open);
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
  renderTraceContext(metadata);
  $('subtitle').textContent=`${trace.format} · ${trace.file} · Session: ${trace.session_id||'未知'}`;
  const trial=trace.trial;
  if(experiments.some(e=>e.id===metadata.experiment_id)) {
    const experimentSelect=$('compare-experiment');
    if(experimentSelect.value!==metadata.experiment_id) {
      experimentSelect.value=metadata.experiment_id;updateComparisonTasks();
    }
    const taskSelect=$('compare-task');
    if(taskSelect.value&&taskSelect.value!==metadata.task_id) {
      taskSelect.value=metadata.task_id;renderComparison();
    }
  }
  $('trial-summary').hidden=!trial;
  if(trial)$('trial-summary').textContent=trial.result_recorded===false
    ? `Trial ${trial.trial_id}：尚无最终结果记录，当前展示已保存的阶段快照和 trace；评分和累计用量不参与最终结果汇总。`
    : `Trial 汇总：${trial.status} · reward ${fmt(trial.reward)} · 输入 / 输出 token ${fmt(trial.usage.input_tokens)} / ${fmt(trial.usage.output_tokens)} · 费用 USD ${fmt(trial.usage.cost_usd)} · 工具 ${fmt(trial.tool_calls)} · 全流程耗时 ${fmt(trial.duration_sec)} s。此处来自实验结果，不分摊到模型尝试。`;
  renderTrialStages(trial);
  const failures=trace.events.filter(e=>e.event==='run.failed').map(e=>`运行异常：${e.data?.error_type||''} ${e.data?.message||''}`);
  const captureWarnings=trace.events.filter(e=>e.event==='capture.warning').map(e=>e.data?.message).filter(Boolean);
  notice([...trace.warnings,...failures,...new Set(captureWarnings)].join('\n'),failures.length>0);const s=trace.summary,t=s.tokens;const partial=s.usage_coverage.total_tokens<s.attempts;
  $('metrics').replaceChildren();
  for(const [label,value,detail,tone] of [['总 token'+(partial?'（已知部分）':''),fmt(t.total_tokens),'只累计每次 attempt 的汇总 usage','blue'],['模型尝试',s.attempts,'包括已观测到的失败 / 重试','violet'],['工具调用',s.tools,'模型请求的工具调用数','cyan'],['轮次 / 步骤',`${s.turns} / ${s.steps}`,'Turn / Step','amber']])$('metrics').append(metricCard(label,value,detail,tone));
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
for(const id of ['filter-harness','filter-model','filter-condition'])$(id).addEventListener('change',fileList);
$('nav-experiments').addEventListener('click',()=>switchNavigation('experiments'));
$('nav-standalone').addEventListener('click',()=>switchNavigation('standalone'));
$('compare-experiment').addEventListener('change',()=>{updateComparisonTasks();openComparisonTrace({reveal:true});});
$('compare-task').addEventListener('change',()=>{renderComparison();openComparisonTrace({reveal:true});});
$('search').addEventListener('input',fileList);
for(const view of ['explorer','compare'])$('view-'+view).addEventListener('click',event=>{
  if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
  event.preventDefault();switchView(view,{focus:true});
});
window.addEventListener('popstate',()=>{
  const query=new URLSearchParams(location.search);
  switchView(query.get('view')==='compare'?'compare':'explorer',{updateHistory:false,focus:true});
  const roots=query.has('root')?query.getAll('root').filter(Boolean):null;
  if(JSON.stringify(roots)!==JSON.stringify(activeRoots))refresh(roots);
});
updateView();
refresh();
