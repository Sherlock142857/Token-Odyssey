"use strict";

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
let catalog, state, queue = [], requestId = "", posting = false, confirmAction = null;
let debugOpen=false, debugCursor=0, debugCampaign="", debugContext=null, debugLoading=false;
const debugExchanges=new Map();
const actionNames = {say:"说话",move:"移动",take:"拿取",give:"交付",place:"放置",hide:"藏起",show:"展示",search:"搜索",inspect:"仔细观察",open:"打开",close:"关闭",lock:"上锁",unlock:"解锁",install:"安装",operate:"操作",wait:"等待"};
const fieldsByKind = {move:["destination_room_id","passage_id"],take:["item_id"],give:["item_id","recipient_id"],place:["item_id","destination_id","relation"],hide:["item_id"],show:["item_id","observer_ids"],say:["content","listener_ids"],search:["container_id"],inspect:["target_id"],open:["openable_id"],close:["openable_id"],lock:["lockable_id","key_item_id"],unlock:["lockable_id","key_item_id"],install:["item_id","slot_id"],operate:["device_id"],wait:[]};
const fieldNames = {destination_room_id:"目的地",passage_id:"经过出口（可选）",item_id:"物品",recipient_id:"交给谁",destination_id:"放置位置",relation:"放置方式",observer_ids:"向谁展示",listener_ids:"对谁说（可不选）",container_id:"搜索容器",target_id:"观察对象",openable_id:"门或容器",lockable_id:"门或容器",key_item_id:"钥匙",slot_id:"插槽",device_id:"设备",content:"说些什么"};
const phases = {creating_world:["导演正在创作","建立世界、重大历史、主角与主线。"],preparing_act:["场景组正在布置","生成并校验可执行的 Scenario v3。"],summarizing:["正在整理 World Log","客观总结本幕已提交的事件与状态变化。"],directing:["导演正在安排后续","回应本幕成败，决定下一幕或终幕。"],remembering:["人物正在形成记忆","每个角色只整理自己实际观察到的内容。"]};

async function api(path, payload) {
  const options = payload === undefined ? {cache:"no-store"} : {method:"POST",headers:{"Content-Type":"application/json","X-Playtest-Token":catalog.token},body:JSON.stringify(payload)};
  const response = await fetch(path, options), result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
function showError(message) { $("error").textContent = message || ""; $("error").hidden = !message; }
async function post(operation, payload={}) {
  if (posting) return;
  posting = true; showError("");
  try { await api(`/api/${operation}`, {campaign_id:state?.campaign_id, ...payload}); }
  catch (error) { showError(error.message); }
  finally { posting=false; await refresh(); }
}
function option(value, label) { return `<option value="${esc(value)}">${esc(label)}</option>`; }
function view() { return state?.act?.request?.view; }
function canAct() { return state?.phase === "playing_act" && state.act?.status === "waiting_for_input" && state.act.pending; }
function allEntities() {
  const v=view(); if(!v) return [];
  const map=new Map((state.act.known_entities||[]).map((e)=>[e.id,e]));
  for(const e of [...v.inventory,...v.items,...v.characters]) {
    const known=map.get(e.id)||{};
    map.set(e.id,{...known,...e,description:e.description||known.description});
  }
  return [...map.values()];
}
function entityName(id) {
  const v=view();
  return allEntities().find((e)=>e.id===id)?.name || (v?.actor_id===id?state.act.identity?.name:null) || (v?.room_id===id?v.room_name:null) || v?.exits.find((e)=>e.passage_id===id)?.name || v?.exits.find((e)=>e.destination_room_id===id)?.destination_name || id;
}
function locationText(entity){
  const v=view(), placement=entity.placement;
  if(!placement)return "位置尚未确认";
  if(placement.parent_id===v?.room_id)return "位于当前房间";
  const parent=allEntities().find((e)=>e.id===placement.parent_id);
  const name=entityName(placement.parent_id);
  if(parent?.kind==="character"||placement.parent_id===v?.actor_id){
    return placement.relation==="attached"?`由 ${name} 携带`:`在 ${name} 身上`;
  }
  if(!parent)return "位置尚未确认";
  return placement.relation==="attached"?`在 ${name} 上`:`在 ${name} 内`;
}

function render() {
  const idle=!state || state.phase==="idle";
  $("setup").hidden=!idle; $("campaign").hidden=idle;
  if(idle) return;
  $("title").textContent=state.title || "一局新的故事";
  $("phase-label").textContent=state.phase.replaceAll("_"," ");
  $("status-text").textContent=state.act_number ? `第 ${state.act_number} 幕${state.current_is_finale?" · 终幕":""}` : "导演正在准备";
  $("run-dir").textContent=`Campaign 存档：${state.run_dir}`;
  $("resume-notice").hidden=!state.resume_notice; $("resume-notice").textContent=state.resume_notice||"";
  const processing=phases[state.phase];
  $("progress").hidden=!processing; if(processing){$("progress-title").textContent=processing[0];$("progress-text").textContent=processing[1];}
  $("interlude").hidden=state.phase!=="interlude";
  $("play").hidden=state.phase!=="playing_act";
  $("ending").hidden=state.phase!=="completed";
  $("technical").hidden=state.phase!=="technical_failed";
  $("abort").hidden=["completed","abandoned"].includes(state.phase);
  if(state.phase==="interlude") renderInterlude();
  if(state.phase==="playing_act") renderPlay();
  if(state.phase==="completed") {$("final-narration").textContent=state.conclusion?.final_narration||state.interlude;$("ending-summary").textContent=state.conclusion?.ending_summary||"";}
  if(state.phase==="technical_failed") $("technical-error").textContent=state.error;
}
function renderInterlude(){
  $("act-number").textContent=`ACT ${String(state.act_number).padStart(2,"0")}${state.current_is_finale?" · FINALE":""}`;
  $("act-title").textContent=state.act_title; $("interlude-text").textContent=state.interlude;
  $("world-intro").innerHTML=state.act_number===1?`<p>${esc(state.public_world)}</p><p><strong>重大历史</strong> · ${esc(state.major_history.join("；"))}</p>`:"";
  const p=state.protagonist;
  $("protagonist").innerHTML=p?`<h3>你将扮演 ${esc(p.name)}</h3><p>${esc(p.description)}</p><p><strong>性格</strong> · ${esc(p.personality)}</p><p><strong>内心想法与当前牵挂</strong> · ${esc(p.inner_life)}</p>`:"";
}
function renderPlay(){
  const act=state.act, v=view(), ended=["completed","limit_reached","stopped","failed"].includes(act.status);
  const labels={running:"其他角色正在行动",waiting_for_input:"轮到你行动",paused:"本幕已暂停",completed:"结束条件已达成",limit_reached:"行动预算已用尽",stopped:"本幕已结束",failed:"本幕技术中断"};
  $("turn-status").textContent=labels[act.status]||act.status;
  $("turn-detail").textContent=`${act.counts.turns} 次行动权 · ${act.counts.events} 个事件`;
  $("advance").hidden=act.status!=="paused"; $("pause").hidden=act.status!=="running"; $("end-act").disabled=ended;
  $("action-area").hidden=!canAct(); $("unavailable").hidden=canAct();
  $("unavailable").textContent=act.status==="running"?"NPC 正在行动，轮到你时表单会出现。":act.status==="paused"?"点击继续推进。":"等待你的下一次行动权。";
  if(canAct() && requestId!==act.pending.request_id){requestId=act.pending.request_id;queue=[];renderFields();}
  const identity=act.identity;
  $("identity").innerHTML=identity?`<h3>${esc(identity.name)}</h3><p>${esc(identity.description)}</p><p><b>性格</b> · ${esc(identity.personality)}</p><p><b>内心想法与当前牵挂</b> · ${esc(identity.private_goal)}</p><p><b>记忆</b> · ${esc(identity.memories.join("；")||"暂无")}</p>`:"";
  $("context").innerHTML=v?`<h3>${esc(v.room_name)}</h3><p>${esc(v.room_description)}</p>${contextGroup("随身物品",v.inventory)}${contextGroup("可见物品",v.items)}${contextGroup("在场角色",v.characters)}<h3>出口</h3>${v.exits.map((e)=>`<div class="entity"><b>${esc(e.destination_name)}</b><small>${esc(e.name)} · ${e.allows_travel?"可通行":"不可通行"}</small></div>`).join("")}`:"<p>首次获得行动权后显示当前环境。</p>";
  $("log").innerHTML=(act.observations||[]).map((o)=>`<article>${o.texts.map((t)=>`<div>${esc(t)}</div>`).join("")}</article>`).join("")||"<p class=\"muted\">还没有新的角色观测。</p>";
  $("feedback").innerHTML=(act.feedback||[]).map((x)=>`<p>${esc(x)}</p>`).join("");
  renderQueue();
}
function contextGroup(title,items){return `<h3>${title}</h3>${items.map((e)=>{const known=allEntities().find((x)=>x.id===e.id)||e;return `<div class="entity"><b>${esc(e.name)}</b><small>${esc(locationText(e))}</small>${known.description?`<span class="entity-description">${esc(known.description)}</span>`:""}</div>`;}).join("")||'<p class="muted">无</p>'}`;}
function choices(field){
  const v=view(); if(!v)return[]; const all=allEntities(),items=all.filter((e)=>e.kind==="item"),named=(xs)=>xs.map((e)=>[e.id,e.name]),has=(c)=>items.filter((e)=>e.capabilities?.includes(c));
  if(field==="destination_room_id")return v.exits.map((e)=>[e.destination_room_id,e.destination_name]);
  if(field==="passage_id")return [["","自动选择"],...v.exits.map((e)=>[e.passage_id,e.name])];
  if(["item_id","key_item_id"].includes(field))return named(items);
  if(["recipient_id","observer_ids","listener_ids"].includes(field))return named(v.characters);
  if(field==="destination_id")return [[v.room_id,`${v.room_name} · 地面`],...named(items)];
  if(field==="relation")return [["inside","内部 / 地面"],["attached","表面"]];
  if(field==="container_id")return named(has("container")); if(field==="target_id")return named([...v.inventory,...v.items,...v.characters]);
  if(field==="slot_id")return named(has("slot")); if(field==="device_id")return named(has("operable"));
  if(["openable_id","lockable_id"].includes(field))return [...named(has(field==="openable_id"?"openable":"lockable")),...v.exits.map((e)=>[e.passage_id,e.name])]; return[];
}
function renderFields(){
  const kind=$("action-kind").value, fields=fieldsByKind[kind]||[];
  $("action-fields").innerHTML=fields.map((field)=>{
    if(field==="content")return `<label>${fieldNames[field]}<textarea name="${field}" required></textarea></label>`;
    const multiple=field.endsWith("_ids"); return `<label>${fieldNames[field]}<select name="${field}"${multiple?" multiple":""}>${choices(field).map(([v,n])=>option(v,n)).join("")}</select></label>`;
  }).join("");
}
function renderQueue(){
  $("queue").innerHTML=queue.map((a,i)=>`<li class="queue-item"><b>${esc(actionNames[a.kind]||a.kind)}</b> · ${esc(Object.entries(a).filter(([k])=>!['kind','amplitude'].includes(k)).map(([k,v])=>`${fieldNames[k]||k}: ${Array.isArray(v)?v.map(entityName).join("、"):entityName(v)}`).join("；"))} <button data-remove="${i}" class="quiet">移除</button></li>`).join("");
  $("submit").disabled=!canAct()||!queue.length;
}
function openConfirm(title,text,action){$("confirm-title").textContent=title;$("confirm-text").textContent=text;confirmAction=action;$("confirm").showModal();}

function messageBlock(message){return `<div class="debug-message"><span>${esc(message.role)}</span><pre>${esc(message.content)}</pre></div>`;}
function exchangeCard(exchange){
  const messages=exchange.request?.messages||[], latest=[...messages].reverse().find((m)=>m.role==="user")||messages.at(-1);
  const response=exchange.response, usage=response?.usage?.total_tokens;
  const status={pending:"等待回复",replied:"已回复",error:"调用失败"}[exchange.status]||exchange.status;
  return `<article class="exchange ${esc(exchange.status)}"><div class="exchange-head"><div><span class="stage">${esc(exchange.stage)}</span><b>${exchange.act_number?`ACT ${exchange.act_number} · `:""}${esc(exchange.agent_id)}</b></div><div><span class="status">${esc(status)}</span>${usage!==undefined?`<small>${esc(usage)} tokens</small>`:""}</div></div>
    <div class="exchange-columns"><section><h4>发送给模型</h4>${latest?messageBlock(latest):'<p class="muted">没有消息内容。</p>'}<details><summary>完整发送上下文 · ${messages.length} 条</summary>${messages.map(messageBlock).join("")}</details></section>
    <section><h4>${exchange.status==="error"?"错误":"模型回复"}</h4>${exchange.status==="pending"?'<div class="pending-line"><span></span>模型正在处理请求…</div>':exchange.error?`<pre class="response error-text">${esc(exchange.error)}</pre>`:`<pre class="response">${esc(response?.content||"")}</pre>`}${response?`<div class="response-meta">${esc(response.model||exchange.model||"")}${response.finish_reason?` · ${esc(response.finish_reason)}`:""}</div>`:""}</section></div>
    <details><summary>${esc(exchange.operation_id)} · 原始交换</summary><pre>${esc(JSON.stringify({request:exchange.request,response:exchange.response,error:exchange.error},null,2))}</pre></details></article>`;
}
function renderDebug(){
  const filter=$("debug-filter").value;
  const rows=[...debugExchanges.values()].filter((x)=>!filter||x.stage===filter).sort((a,b)=>a.firstRevision-b.firstRevision);
  $("debug-count").textContent=`${rows.length} / ${debugExchanges.size} 次调用`;
  $("debug-status").textContent=debugExchanges.size?"面板展开期间实时同步；发送与回复按同一调用配对。":"等待第一条模型调用…";
  $("debug-content").innerHTML=rows.map(exchangeCard).join("")||'<div class="debug-empty">当前筛选下没有模型调用。</div>';
  if(debugContext){
    $("debug-state").textContent=JSON.stringify({phase:debugContext.phase,bible:debugContext.bible,current_act_brief:debugContext.current_act_brief,world_summary:debugContext.world_summary,director_transition:debugContext.director_transition,memories:debugContext.memories,inner_states:debugContext.inner_states},null,2);
    $("debug-scenario").textContent=JSON.stringify(debugContext.current_scenario,null,2);
    $("debug-world").textContent=JSON.stringify(debugContext.act,null,2);
  }
}
async function refreshDebug(reset=false){
  if(!debugOpen||debugLoading)return;
  if(reset){debugCursor=0;debugExchanges.clear();debugContext=null;}
  debugLoading=true;
  try{
    let data=await api(`/api/debug?cursor=${debugCursor}`);
    if(debugCampaign&&data.campaign_id!==debugCampaign){
      debugCursor=0;debugExchanges.clear();data=await api("/api/debug?cursor=0");
    }
    debugCampaign=data.campaign_id||"";
    for(const row of data.exchanges||[]){const old=debugExchanges.get(row.id);debugExchanges.set(row.id,{...old,...row,firstRevision:old?.firstRevision||row.revision});}
    debugCursor=data.cursor||debugCursor;debugContext=data;renderDebug();
  }catch(e){$("debug-status").textContent=`调试信息刷新失败：${e.message}`;}
  finally{debugLoading=false;}
}
async function refresh(){try{state=await api("/api/state");$("connection").textContent="LOCALHOST · 已连接";render();if(debugOpen)await refreshDebug();}catch(e){$("connection").textContent="连接中断";showError(e.message);}}
async function boot(){
  try{catalog=await api("/api/catalog");$("profiles").innerHTML=Object.entries(catalog.profiles).map(([k,v])=>`<span>${esc(k)}</span><b>${esc(v.profile)} · ${esc(v.model)}</b>`).join("");
    $("resume-list").innerHTML=catalog.resumable.length?`<h3>幕边界存档</h3>${catalog.resumable.map((x)=>`<button class="quiet" data-resume="${esc(x.campaign_id)}">${esc(x.title||x.campaign_id)} · Act ${x.act_number} · ${esc(x.phase)}</button>`).join("")}`:"";
    $("action-kind").innerHTML=Object.entries(actionNames).map(([k,n])=>option(k,`${n} · ${k}`)).join(""); await refresh(); setInterval(refresh,900);
  }catch(e){showError(`无法连接本地服务：${e.message}`);}
}
$("start-form").addEventListener("submit",async(e)=>{e.preventDefault();posting=true;try{await api("/api/start",{creative_brief:$("creative-brief").value,seed:Number($("seed").value)});}catch(error){showError(error.message);}finally{posting=false;await refresh();}});
$("resume-list").addEventListener("click",async(e)=>{const id=e.target.dataset.resume;if(id){await api("/api/resume",{campaign_id:id});await refresh();}});
$("enter-act").addEventListener("click",()=>post("enter-act")); $("advance").addEventListener("click",()=>post("advance",{auto:true})); $("pause").addEventListener("click",()=>post("pause"));
$("action-kind").addEventListener("change",renderFields); $("add-action").addEventListener("click",(e)=>{e.preventDefault();if(!canAct()||queue.length>=view().max_actions)return;const kind=$("action-kind").value,action={kind,amplitude:"normal"};for(const field of fieldsByKind[kind]){const control=$("action-fields").querySelector(`[name="${field}"]`);action[field]=field.endsWith("_ids")?[...control.selectedOptions].map((o)=>o.value):control.value;if(field==="passage_id"&&!action[field])delete action[field];}if(kind==="say"&&!action.content.trim())return showError("请填写台词。");if(kind==="show"&&!action.observer_ids.length)return showError("请选择展示对象。");queue.push(action);renderQueue();});
$("queue").addEventListener("click",(e)=>{if(e.target.dataset.remove!==undefined){queue.splice(Number(e.target.dataset.remove),1);renderQueue();}});
$("submit").addEventListener("click",()=>post("submit",{actor_id:state.act.pending.actor_id,request_id:state.act.pending.request_id,actions:queue,auto:true}));
$("end-act").addEventListener("click",()=>openConfirm("结束本幕？","当前正在进行的行动权会先安全完成。未完成任务与失败会由导演在下一幕现实回应。",()=>post("end-act")));
$("abort").addEventListener("click",()=>openConfirm("放弃整局？","这不会生成叙事结局；已有记录仍会保留。",()=>post("abort")));
$("confirm-cancel").addEventListener("click",()=>$("confirm").close()); $("confirm-ok").addEventListener("click",()=>{$("confirm").close();confirmAction?.();confirmAction=null;});
$("retry").addEventListener("click",()=>post("retry"));
$("debug-toggle").addEventListener("click",async()=>{debugOpen=!debugOpen;$("debug").hidden=!debugOpen;if(debugOpen)await refreshDebug(true);});
$("debug-close").addEventListener("click",()=>{debugOpen=false;$("debug").hidden=true;});
$("debug-filter").addEventListener("change",renderDebug);
boot();
