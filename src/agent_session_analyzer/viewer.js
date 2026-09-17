'use strict';
const $ = id => document.getElementById(id);
let files = [], selected = null, loadVersion = 0;
const fmt = n => n == null ? '未提供' : Number(n).toLocaleString('zh-CN');
const pretty = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
function el(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text!=null)n.textContent=text; return n; }
function badge(text, kind='') { return el('span','badge '+kind,text); }
function notice(text, error=false) { $('message').replaceChildren(); if(text)$('message').append(el('div','notice'+(error?' error':''),text)); }
function extra(title, value) { const d=el('details','extra');d.append(el('summary','',title),el('pre','',pretty(value)));return d; }
function block(parent,title,value,cls='answer') { parent.append(el('div','block-title',title),el('div',cls,value == null ? '未提供' : value)); }
function duration(start,end) { return start != null && end != null && end>=start ? ((end-start)/1000).toFixed(2)+' s' : '耗时未知'; }
async function api(url) { const r=await fetch(url); const data=await r.json(); if(!r.ok)throw new Error(data.error||r.statusText); return data; }
function fileList() {
  $('files').replaceChildren(); const q=$('search').value.toLowerCase();
  for(const f of files.filter(f=>f.name.toLowerCase().includes(q))) {
    const b=el('button','file'+(selected===f.id?' active':''),f.name);
    b.title=f.root+' / '+f.name; b.append(el('small','',new Date(f.modified_ms).toLocaleString('zh-CN')+' · '+(f.size/1024).toFixed(1)+' KB'));
    b.addEventListener('click',()=>load(f.id)); $('files').append(b);
  }
  if(!$('files').childElementCount)$('files').append(el('p','muted',files.length?'没有匹配的文件':'目录中还没有 JSONL 文件'));
}
async function refresh() {
  try { const data=await api('/api/files');files=data.files;$('roots').textContent=data.roots.join('\n');fileList();
    if(selected&&files.some(f=>f.id===selected))await load(selected);
    else if(files.length)await load(files[0].id);
    else {selected=null;$('metrics').replaceChildren();$('timeline').replaceChildren(el('div','empty','运行记录器后，在这里点击刷新。'));$('title').textContent='暂无运行记录';$('subtitle').textContent='也可以通过 --root 指定已有 dsh 日志目录。';$('coverage').textContent='';$('source-section').hidden=true;notice('');}
  } catch(e){notice(e.message,true);}
}
async function load(id) {
  selected=id;fileList();const request=++loadVersion;notice('正在读取…');
  try {const trace=await api('/api/trace?id='+encodeURIComponent(id));if(request!==loadVersion)return;render(trace);}
  catch(e){if(request!==loadVersion)return;notice(e.message,true);$('title').textContent='无法读取此日志';$('subtitle').textContent=files.find(f=>f.id===id)?.name||'';$('metrics').replaceChildren();$('coverage').textContent='';$('timeline').replaceChildren();$('source-section').hidden=true;}
}
function attemptCard(e) {
  const d=e.data||{}, card=el('details','attempt');card.open=true;
  const summary=el('summary','',`Model attempt ${d.attempt_index} · ${e.model?.model||'模型未提供'} `);
  const labels={committed:'已提交',completed:'已返回',failed:'请求失败',event_only:'仅有事件 / usage 缺失',uncommitted:'未提交 / 失败尝试',interrupted:'已中断'};
  summary.append(badge(labels[d.status]||d.status||'未知',['committed','completed'].includes(d.status)?'good':'bad'));
  card.append(summary);const body=el('div','card-body');
  body.append(el('p','muted',`Provider: ${e.model?.provider||'未提供'} · 结束原因: ${pretty(d.finish_reason)||'未提供'}`));
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
  $('title').textContent=trace.session_id||'未知 session';$('subtitle').textContent=trace.format+' · '+trace.file;
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
      const used=new Set();for(const row of rows){if(row.event==='model.attempt')step.append(attemptCard(row));if(row.event==='tool.call'){const index=rows.findIndex((r,i)=>!used.has(i)&&r.event==='tool.result'&&r.tool_call_id!=null&&r.tool_call_id===row.tool_call_id);if(index>=0)used.add(index);step.append(toolCard(row,index>=0?rows[index]:null));}}
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
$('refresh').addEventListener('click',refresh);$('search').addEventListener('input',fileList);refresh();
