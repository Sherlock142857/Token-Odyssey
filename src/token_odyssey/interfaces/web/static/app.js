"use strict";

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const terminal = new Set(["completed", "limit_reached", "failed", "stopped"]);
let catalog,
  state,
  observer,
  queue = [],
  actorChoice = "",
  currentRequest = "",
  currentSession = "";
let logMode = "player",
  lastLog = "",
  contextKey = "",
  formKey = "",
  posting = false,
  connected = true;
let setupOpen = false,
  refreshSequence = 0;
const fieldNames = {
  destination_room_id: "目的地",
  passage_id: "经过的出口",
  item_id: "物品",
  recipient_id: "交给谁",
  destination_id: "放置位置",
  relation: "放置方式",
  observer_ids: "向谁展示",
  listener_ids: "对谁说（可不选）",
  container_id: "搜索容器",
  openable_id: "门或容器",
  lockable_id: "门或容器",
  key_item_id: "使用的钥匙",
  slot_id: "安装到插槽",
  device_id: "操作的设备",
  content: "说些什么",
};
const fieldsByKind = {
  move: ["destination_room_id", "passage_id"],
  take: ["item_id"],
  give: ["item_id", "recipient_id"],
  place: ["item_id", "destination_id", "relation"],
  hide: ["item_id"],
  show: ["item_id", "observer_ids"],
  say: ["content", "listener_ids"],
  search: ["container_id"],
  open: ["openable_id"],
  close: ["openable_id"],
  lock: ["lockable_id", "key_item_id"],
  unlock: ["lockable_id", "key_item_id"],
  install: ["item_id", "slot_id"],
  operate: ["device_id"],
  wait: [],
};
const actionInfo = (kind) => catalog.actions.find((a) => a.kind === kind);
const actorName = (id) =>
  catalog.characters.find((a) => a.id === id)?.name || id || "角色";
const option = (id, name, selected = false) =>
  `<option value="${esc(id)}"${selected ? " selected" : ""}>${esc(name)}</option>`;
function error(message) {
  $("error").textContent = message || "";
  $("error").hidden = !message;
}
function canAct() {
  return (
    connected &&
    !posting &&
    state?.status === "waiting_for_input" &&
    state.pending?.actor_id === state.selected_actor
  );
}
function view() {
  return state?.request?.view;
}
function entities() {
  const v = view();
  if (!v) return [];
  const known = new Map((state.known_entities || []).map((e) => [e.id, e]));
  for (const e of [...v.inventory, ...v.items, ...v.characters])
    known.set(e.id, {
      ...known.get(e.id),
      ...e,
      description: e.description || known.get(e.id)?.description,
    });
  return [...known.values()];
}
function entityName(id) {
  const v = view();
  return (
    entities().find((e) => e.id === id)?.name ||
    v?.exits.find((e) => e.passage_id === id)?.name ||
    v?.exits.find((e) => e.destination_room_id === id)?.destination_name ||
    (v?.room_id === id ? v.room_name : id)
  );
}

async function api(path, payload) {
  const response = await fetch(
    path,
    payload === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Playtest-Token": catalog.token,
          },
          body: JSON.stringify(payload),
        },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
async function post(operation, payload = {}) {
  if (posting) return false;
  posting = true;
  error("");
  renderControls();
  try {
    await api(`/api/${operation}`, {
      session_id: state?.session_id,
      ...payload,
    });
    return true;
  } catch (e) {
    error(e.message);
    return false;
  } finally {
    posting = false;
    await refresh();
  }
}

function renderSetup() {
  $("scenario-title").textContent = catalog.scenario.title;
  $("background").textContent = catalog.scenario.background;
  $("act-title").textContent = catalog.scenario.title;
  $("rounds").value = catalog.scenario.rounds;
  $("seed").value = catalog.scenario.seed;
  $("cast-fields").innerHTML = catalog.characters
    .map((actor) => {
      const binding = catalog.default_cast[actor.id];
      return `<div class="cast-row"><div class="cast-name">${esc(actor.name)}<small>${esc(actor.id)}</small></div>
      <select data-adapter="${esc(actor.id)}" aria-label="${esc(actor.name)}控制方式">${option("human", "人类", binding.adapter === "human")}${option("llm", "LLM", binding.adapter === "llm")}${option("scripted", "脚本（离线）", binding.adapter === "scripted")}</select>
      <select data-profile="${esc(actor.id)}" aria-label="${esc(actor.name)}模型">${catalog.profiles.map((p) => option(p.id, `${p.id} · ${p.model}`, binding.profile === p.id)).join("")}</select></div>`;
    })
    .join("");
  $("model-note").textContent = catalog.profiles.length
    ? "选择 LLM 并开始测试后会调用已配置的模型 API。凭据由本地服务读取。"
    : "当前未载入模型配置，可先离线测试。使用 --run-config configs/llm.deepseek.yaml 启动服务可启用 LLM。";
  $("cast-fields").addEventListener("change", updateCastFields);
  updateCastFields();
  const preferred = [
    "say",
    "move",
    "open",
    "close",
    "unlock",
    "lock",
    "search",
    "take",
    "give",
    "place",
    "install",
    "operate",
    "show",
    "hide",
    "wait",
  ];
  $("action-kind").innerHTML = preferred
    .filter((k) => actionInfo(k))
    .map((k) => option(k, `${actionInfo(k).name} · ${k}`))
    .join("");
}
function updateCastFields() {
  for (const select of document.querySelectorAll("[data-adapter]")) {
    const profile = [...document.querySelectorAll("[data-profile]")].find(
      (p) => p.dataset.profile === select.dataset.adapter,
    );
    profile.disabled = select.value !== "llm";
    profile.hidden = select.value !== "llm";
    select.querySelector('[value="llm"]').disabled = !catalog.profiles.length;
  }
}

function renderControls() {
  if (!state) return;
  const s = state.status,
    running = s === "running",
    ended = terminal.has(s),
    locked = posting || !connected;
  $("start-button").disabled = locked;
  $("step").hidden = $("advance").hidden = s !== "paused";
  $("pause").hidden = !running;
  $("pause").disabled = locked || state.pause_requested || state.stop_requested;
  $("pause").textContent = state.pause_requested
    ? "将在回合后暂停"
    : "回合后暂停";
  $("stop").hidden = ended;
  $("stop").disabled = locked || state.stop_requested;
  $("stop").textContent = state.stop_requested ? "正在结束…" : "结束测试";
  $("step").disabled = $("advance").disabled = locked;
  $("configure").hidden = !ended;
  $("submit-actions").disabled = !canAct() || !queue.length;
  $("add-action").disabled =
    !canAct() || queue.length >= (view()?.max_actions || 5);
  $("action-area").hidden = !(
    s === "waiting_for_input" &&
    state.pending?.actor_id === state.selected_actor
  );
  $("action-unavailable").hidden = !$("action-area").hidden;
  $("action-unavailable").textContent = ended
    ? "本次 act 已结束。你可以查看日志，或开始新测试。"
    : running
      ? "其他参与者正在行动。轮到你时，动作表单会在这里出现。"
      : s === "paused"
        ? "运行已暂停。点击推进，继续到角色获得行动权。"
        : "轮到另一位人类角色，请切换角色视角后提交动作。";
}

function renderState() {
  if (state.session_id !== currentSession) {
    currentSession = state.session_id;
    currentRequest = formKey = contextKey = lastLog = "";
    observer = null;
    queue = [];
  }
  $("setup").hidden = state.status !== "idle" && !setupOpen;
  $("play").hidden = state.status === "idle" || setupOpen;
  if (state.status === "idle") {
    renderControls();
    return;
  }
  const pendingId = state.pending?.request_id || "";
  if (pendingId && pendingId !== currentRequest) {
    currentRequest = pendingId;
    queue = [];
    try {
      const saved = JSON.parse(
        sessionStorage.getItem(`queue:${state.session_id}:${pendingId}`) ||
          "null",
      );
      if (Array.isArray(saved)) queue = saved;
    } catch {
      /* A missing or malformed draft never blocks a turn. */
    }
    formKey = "";
  }
  const count = state.counts;
  const roundsDone = Math.floor(count.turns / Object.keys(state.cast).length);
  $("counters").innerHTML =
    `<span class="counter"><b>${roundsDone} / ${state.rounds}</b>已完成轮数</span><span class="counter"><b>${count.turns}</b>行动权</span><span class="counter"><b>${count.events}</b>事件</span>`;
  const messages = {
    running: [
      "推进中",
      `${actorName(state.active_actor)}${state.cast[state.active_actor]?.adapter === "llm" ? " · LLM 正在思考" : " · 正在行动"}`,
      "日志会自动更新；可以选择在当前回合结束后暂停。",
    ],
    waiting_for_input: [
      "等待你行动",
      `轮到${actorName(state.pending?.actor_id)}`,
      `选择动作 → 加入队列 → 提交本回合。最多 ${state.pending?.view.max_actions || 5} 个动作。`,
    ],
    paused: [
      "已暂停",
      "等待继续推进",
      "可以推进一个回合，或自动推进到下一个人类回合。",
    ],
    completed: [
      "Act 已完成",
      "剧情结束条件已达成",
      "最终条件与回放结果可在 World Log 测试区查看。",
    ],
    limit_reached: [
      "轮数用尽",
      "尚未达成剧情结束条件",
      "本次测试已保存；可以增加轮数开始新测试。",
    ],
    stopped: ["已结束", "本次测试已手动结束", "已提交的动作与测试记录已保存。"],
    failed: ["运行中断", "需要检查模型或服务配置", state.error],
  }[state.status];
  $("status-pill").textContent = messages?.[0] || state.status;
  $("turn-message").textContent = messages?.[1] || "";
  $("turn-detail").textContent = messages?.[2] || "";
  $("view-actor").innerHTML = state.human_ids
    .map((id) => option(id, actorName(id), id === state.selected_actor))
    .join("");
  $("view-actor").hidden = !state.human_ids.length;
  $("run-path").textContent = `运行记录：${state.run_dir}`;
  $("outcome").hidden = !terminal.has(state.status);
  if (terminal.has(state.status))
    $("outcome").innerHTML =
      `<strong>${esc(messages[1])}</strong><p>${esc(messages[2])}</p>`;
  const nextContext = JSON.stringify([
    state.selected_actor,
    state.request,
    state.known_entities,
    canAct(),
  ]);
  if (contextKey !== nextContext) {
    contextKey = nextContext;
    renderContext();
  }
  const nextForm = `${state.selected_actor}:${state.request?.request_id}`;
  if (nextForm !== formKey) {
    formKey = nextForm;
    renderFields();
  }
  $("feedback").innerHTML = [...new Set(state.feedback)]
    .map((text) => `<p>${esc(text)}</p>`)
    .join("");
  renderQueue();
  renderControls();
  renderLog();
}

function entityCard(entity, inventory = false) {
  const known = entities().find((e) => e.id === entity.id) || entity;
  const placement = entity.placement;
  const location = placement
    ? `${placement.relation === "inside" ? "位于" : "附在"} ${entityName(placement.parent_id)}`
    : "位置尚未确认";
  return `<button class="entity" data-entity="${esc(entity.id)}" data-inventory="${inventory}"${canAct() ? "" : " disabled"}>
    <span class="entity-row"><strong>${esc(entity.name)}</strong>${entity.is_open === null || entity.is_open === undefined ? "" : `<span class="tag">${entity.is_open ? "打开" : "关闭"}</span>`}</span>
    <span class="entity-meta">${esc(location)}${entity.basis === "continuity" ? " · 最近确认" : ""}</span>
    ${known.description ? `<span class="entity-desc">${esc(known.description)}</span>` : ""}</button>`;
}
function renderContext() {
  const v = view(),
    identity = state.identity;
  if (!v) {
    $("context").innerHTML =
      `<div class="empty"><span class="empty-icon">◇</span>${state.human_ids.length ? "尚未轮到此人类角色。<br>首次获得行动权后显示角色信息。" : "本次测试没有人类角色。<br>切换 World Log 观察剧情推进。"}</div>`;
    return;
  }
  const seen = new Set([
    v.actor_id,
    v.room_id,
    ...v.items.map((e) => e.id),
    ...v.inventory.map((e) => e.id),
    ...v.characters.map((e) => e.id),
    ...v.exits.flatMap((e) => [e.passage_id, e.destination_room_id]),
  ]);
  const remembered = entities().filter((e) => !seen.has(e.id));
  $("context").innerHTML =
    `<div class="context-block"><h3>所在场景 · 本次决策时</h3><div class="room-name">${esc(v.room_name)}</div><p class="room-description">${esc(v.room_description)}</p></div>
    <div class="context-block"><h3>${esc(identity.name)} · 私人目标</h3><p class="goal">${esc(identity.private_goal || "没有额外的私人目标。")}</p><details class="remembered"><summary>背景与记忆</summary><p>${esc(identity.public_background)}</p><p>${esc(identity.personality)}</p>${identity.memories.map((m) => `<p>${esc(m)}</p>`).join("")}</details></div>
    <div class="context-block"><h3>随身物品 · ${v.inventory.length}</h3><div class="entity-list">${v.inventory.map((e) => entityCard(e, true)).join("") || '<p class="muted">暂时没有随身物品。</p>'}</div></div>
    <div class="context-block"><h3>当前可见物品 · ${v.items.length}</h3><p class="selection-note">点击物品可填入动作；看得见不一定拿得到。</p><div class="entity-list">${v.items.map((e) => entityCard(e)).join("") || '<p class="muted">没有辨认出的物品。</p>'}</div></div>
    <div class="context-block"><h3>在场角色</h3><div class="entity-list">${v.characters.map((e) => entityCard(e)).join("") || '<p class="muted">没有辨认出的其他角色。</p>'}</div></div>
    <div class="context-block"><h3>出口</h3><div class="entity-list">${v.exits.map((e) => `<button class="entity" data-exit="${esc(e.passage_id)}"${canAct() ? "" : " disabled"}><span class="entity-row"><strong>${esc(e.destination_name)} →</strong><span class="tag">${e.allows_travel ? "可通行" : "不可通行"}</span></span><span class="entity-meta">${esc(e.name)} · ${e.is_open ? "打开" : "关闭"}</span></button>`).join("")}</div></div>
    ${remembered.length ? `<div class="context-block"><details><summary>曾经辨认的对象 · ${remembered.length}</summary><p class="muted">保留身份与最后获知信息，不代表当前可接触。</p><div class="entity-list">${remembered.map((e) => entityCard(e)).join("")}</div></details></div>` : ""}`;
}

function choices(field) {
  const v = view();
  if (!v) return [];
  const all = entities(),
    items = all.filter((e) => e.kind === "item");
  const named = (arr) =>
    arr.map((e) => [
      e.id,
      `${e.name}${v.inventory.some((i) => i.id === e.id) ? " · 随身" : !v.items.some((i) => i.id === e.id) && e.kind === "item" ? " · 已知" : ""}`,
    ]);
  const has = (cap) => items.filter((e) => e.capabilities?.includes(cap));
  if (field === "destination_room_id")
    return v.exits
      .map((e) => [
        e.destination_room_id,
        `${e.destination_name} · ${e.allows_travel ? "可通行" : "当前不可通行"}`,
      ])
      .filter((e, i, a) => a.findIndex((x) => x[0] === e[0]) === i);
  if (field === "passage_id")
    return [
      ["", "自动选择可通行出口"],
      ...v.exits.map((e) => [e.passage_id, e.name]),
    ];
  if (["item_id", "key_item_id"].includes(field)) return named(items);
  if (["recipient_id", "observer_ids", "listener_ids"].includes(field))
    return named(v.characters);
  if (field === "destination_id")
    return [[v.room_id, `${v.room_name} · 地面`], ...named(items)];
  if (field === "relation")
    return [
      ["inside", "放入内部 / 房间地面"],
      ["attached", "放在表面"],
    ];
  if (field === "container_id") return named(has("container"));
  if (field === "slot_id") return named(has("slot"));
  if (field === "device_id") return named(has("operable"));
  if (["openable_id", "lockable_id"].includes(field))
    return [
      ...named(has(field === "openable_id" ? "openable" : "lockable")),
      ...v.exits.map((e) => [e.passage_id, `${e.name} · 出口`]),
    ];
  return [];
}
function renderFields() {
  const kind = $("action-kind").value,
    info = actionInfo(kind);
  if (!info) return;
  $("action-help").textContent = info.help;
  $("action-fields").innerHTML = (fieldsByKind[kind] || [])
    .map((field) => {
      if (field === "content")
        return `<label>${fieldNames[field]}<textarea name="content" placeholder="输入角色要说的话…" maxlength="4000" required></textarea></label>`;
      const options = choices(field);
      if (field.endsWith("_ids"))
        return `<fieldset><legend>${fieldNames[field]}</legend>${options.map(([id, name]) => `<label class="check"><input type="checkbox" name="${field}" value="${esc(id)}">${esc(name)}</label>`).join("") || '<p class="muted">当前没有在场角色。</p>'}</fieldset>`;
      return `<label>${fieldNames[field] || esc(field)}<select name="${field}" ${field === "passage_id" ? "" : "required"}>${options.length ? options.map(([id, name]) => option(id, name)).join("") : '<option value="">没有已知的可选对象</option>'}</select></label>`;
    })
    .join("");
}

function describeAction(action) {
  const values = (fieldsByKind[action.kind] || [])
    .filter((f) => action[f] && f !== "passage_id")
    .map((f) => {
      if (f === "content") return `“${action[f]}”`;
      if (f === "relation") return action[f] === "inside" ? "内部" : "表面";
      return Array.isArray(action[f])
        ? action[f].map(entityName).join("、")
        : entityName(action[f]);
    })
    .filter(Boolean);
  return `${actionInfo(action.kind)?.name || action.kind}${values.length ? " · " + values.join(" → ") : ""}${action.amplitude !== "normal" ? (action.amplitude === "subtle" ? "（低调）" : "（高调）") : ""}`;
}
function renderQueue() {
  const max = view()?.max_actions || 5;
  $("queue-count").textContent = `${queue.length} / ${max}`;
  $("queue").innerHTML = queue
    .map(
      (a, i) =>
        `<li><span class="queue-text">${esc(describeAction(a))}</span><span class="queue-tools"><button data-up="${i}" aria-label="上移动作 ${i + 1}"${i ? "" : " disabled"}>↑</button><button data-down="${i}" aria-label="下移动作 ${i + 1}"${i < queue.length - 1 ? "" : " disabled"}>↓</button><button data-remove="${i}" aria-label="移除动作 ${i + 1}">×</button></span></li>`,
    )
    .join("");
  const hasMove = queue.some(
    (a, i) => a.kind === "move" && i < queue.length - 1,
  );
  $("queue-policy").textContent =
    `${view()?.continue_after_move ? "移动后可继续执行队列。" : hasMove ? "注意：移动成功后，后面的动作不会执行。" : "移动成功后结束队列。"} 后续动作失败时，前面已成功的动作保留。新发现的物品要等下次行动再选择。`;
  $("submit-actions").disabled = !canAct() || !queue.length;
  $("add-action").disabled = !canAct() || queue.length >= max;
  if (currentRequest) {
    try {
      sessionStorage.setItem(
        `queue:${state.session_id}:${currentRequest}`,
        JSON.stringify(queue),
      );
    } catch {
      /* Draft persistence is optional. */
    }
  }
}

function renderLog() {
  const world = logMode === "world";
  $("player-tab").setAttribute("aria-selected", String(!world));
  $("world-tab").setAttribute("aria-selected", String(world));
  $("log-caption").classList.toggle("observer", world);
  $("log-caption").textContent = world
    ? "测试观察视角 · 包含全世界事件和机关信息，超出角色所知范围。"
    : `仅显示${actorName(state.selected_actor)}实际获知的信息；状态栏保留最近一次决策时的视图。`;
  const entries = world ? observer?.entries || [] : state.observations || [];
  const relevantResults = world
    ? []
    : state.action_results.filter((r) => !r.accepted || r.notices.length);
  const key = JSON.stringify([
    logMode,
    state.selected_actor,
    entries,
    relevantResults,
    observer?.report,
    observer?.usage,
  ]);
  if (key === lastLog) return;
  lastLog = key;
  const scrollTop = $("log").scrollTop;
  let html = entries
    .map((row) => {
      if (!world)
        return `<article class="log-entry"><div class="log-meta"><span>事件 ${row.event_sequence}</span><span>·</span><span>记录 ${row.id}</span></div>${row.texts.map((text) => `<p>${esc(text)}</p>`).join("")}</article>`;
      if (row.type === "feedback")
        return `<article class="log-entry ${row.accepted ? "" : "failed"}"><div class="log-meta">动作反馈</div><p>${esc(row.text)}</p></article>`;
      const e = row.event;
      return `<article class="log-entry ${e.source === "world" ? "world-event" : ""}"><div class="log-meta"><span>事件 ${e.sequence}</span><span>· 事务 ${e.transaction_id}</span><span>${e.source === "world" ? "机关反应" : esc(actorName(e.actor_id))}</span>${e.caused_by ? `<span>↳ 事件 ${e.caused_by}</span>` : ""}</div><p>${esc(row.text)}</p><details><summary>查看结构化事件</summary><pre>${esc(JSON.stringify({ kind: e.kind, data: e.data, changes: e.changes, caused_by: e.caused_by }, null, 2))}</pre></details></article>`;
    })
    .join("");
  html += relevantResults
    .map(
      (row) =>
        `<article class="log-entry failed"><div class="log-meta">${esc(row.request_id)} · 动作 ${row.action_index + 1} · ${esc(actionInfo(row.kind)?.name || row.kind)}</div>${[...row.issues, ...row.notices].map((i) => `<p>${esc(state.issue_texts[i.code] || i.code)}</p>`).join("")}</article>`,
    )
    .join("");
  $("log-count").textContent = `${entries.length} 条`;
  $("log").innerHTML =
    html ||
    `<div class="empty"><span class="empty-icon">≋</span>${world ? "世界尚未产生事件。" : "故事从下一次行动开始。<br>角色获知的事件会按顺序显示在这里。"}</div>`;
  if ($("follow-log").checked) $("log").scrollTop = $("log").scrollHeight;
  else $("log").scrollTop = scrollTop;
  renderReport();
}
function renderReport() {
  $("observer-report").hidden = logMode !== "world" || !observer;
  if (!observer) return;
  const report = observer.report;
  const tokens = Object.entries(observer.usage)
    .map(
      ([actor, usage]) => `${actorName(actor)}：${usage.total_tokens} tokens`,
    )
    .join(" · ");
  let html = tokens ? `<p class="muted">模型用量 · ${esc(tokens)}</p>` : "";
  if (report) {
    html += `<details open><summary>${report.success ? "✓ 全流程验收通过" : "本次测试验收未全部通过"} · 回放${report.replay_matches ? "一致" : "不一致"}</summary>`;
    html +=
      report.expected
        .map(({ condition: c, met }) => {
          const label = (id) => observer.labels[id] || id;
          const condition =
            c.kind === "inside"
              ? `${label(c.subject_id)}位于${label(c.object_id)}`
              : c.kind === "installed"
                ? `${label(c.subject_id)}安装在${label(c.object_id)}`
                : c.kind === "locked"
                  ? `${label(c.subject_id)}${c.value === false ? "未上锁" : "已上锁"}`
                  : c.kind === "open"
                    ? `${label(c.subject_id)}${c.value === false ? "关闭" : "打开"}`
                    : `${label(c.subject_id)} · ${c.kind} = ${c.value ?? true}`;
          return `<div class="condition ${met ? "pass" : ""}">${met ? "✓" : "○"} ${esc(condition)}</div>`;
        })
        .join("") + "</details>";
  }
  $("observer-report").innerHTML =
    html || '<p class="muted">结束后在此显示最终条件、模型用量与回放检查。</p>';
}

async function refresh() {
  const sequence = ++refreshSequence;
  try {
    let next = await api(
      `/api/state${actorChoice ? `?actor=${encodeURIComponent(actorChoice)}` : ""}`,
    );
    if (next.session_id !== currentSession && actorChoice) {
      actorChoice = "";
      next = await api("/api/state");
    }
    if (sequence !== refreshSequence) return;
    state = next;
    connected = true;
    $("connection").textContent = "LOCALHOST · 已连接";
    if (logMode === "world" && state.session_id) {
      const snapshot = await api("/api/observer");
      if (sequence !== refreshSequence) return;
      if (snapshot.session_id === state.session_id) observer = snapshot;
    }
    renderState();
  } catch (e) {
    if (sequence !== refreshSequence) return;
    // A different browser tab can start a run with a different human cast.
    if (actorChoice) actorChoice = "";
    connected = false;
    $("connection").textContent = "连接中断 · 正在重试";
    renderControls();
  }
}
async function poll() {
  await refresh();
  setTimeout(poll, 900);
}

$("start-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const cast = {};
  for (const select of document.querySelectorAll("[data-adapter]")) {
    const profile = [...document.querySelectorAll("[data-profile]")].find(
      (p) => p.dataset.profile === select.dataset.adapter,
    );
    cast[select.dataset.adapter] = {
      adapter: select.value,
      profile: select.value === "llm" ? profile.value : null,
    };
  }
  if (posting) return;
  posting = true;
  error("");
  $("start-button").disabled = true;
  try {
    await api("/api/start", {
      cast,
      rounds: Number($("rounds").value),
      seed: Number($("seed").value),
      auto: $("start-auto").checked,
    });
    setupOpen = false;
    actorChoice = "";
    $("continue-auto").checked = $("start-auto").checked;
  } catch (e) {
    error(e.message);
  } finally {
    posting = false;
    $("start-button").disabled = false;
    await refresh();
  }
});
$("action-kind").addEventListener("change", renderFields);
$("action-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!canAct() || queue.length >= view().max_actions) return;
  error("");
  const kind = $("action-kind").value,
    action = { kind, amplitude: $("amplitude").value };
  const form = new FormData(event.currentTarget);
  for (const field of fieldsByKind[kind]) {
    if (field.endsWith("_ids")) action[field] = form.getAll(field);
    else if (field === "passage_id" && !form.get(field)) continue;
    else action[field] = form.get(field);
  }
  if (kind === "say" && !action.content.trim()) {
    error("请填写要说的话。");
    return;
  }
  if (kind === "show" && !action.observer_ids.length) {
    error("请选择至少一位展示对象。");
    return;
  }
  queue.push(action);
  renderQueue();
});
$("context").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-entity],button[data-exit]");
  if (!button || !canAct()) return;
  const id = button.dataset.entity || button.dataset.exit;
  let select = [...$("action-fields").querySelectorAll("select")].find((s) =>
    [...s.options].some((o) => o.value === id),
  );
  if (select) {
    select.value = id;
    select.focus();
    return;
  }
  const v = view(),
    exit = v.exits.find((e) => e.passage_id === id),
    entity = entities().find((e) => e.id === id);
  let kind = "take",
    field = "item_id",
    value = id;
  if (exit) {
    kind = exit.allows_travel ? "move" : "open";
    field = exit.allows_travel ? "destination_room_id" : "openable_id";
    value = exit.allows_travel ? exit.destination_room_id : id;
  } else if (entity.kind === "character") {
    kind = "say";
    field = "listener_ids";
  } else if (entity.capabilities.includes("openable") && !entity.is_open) {
    kind = "open";
    field = "openable_id";
  } else if (entity.capabilities.includes("container")) {
    kind = "search";
    field = "container_id";
  } else if (entity.capabilities.includes("operable")) {
    kind = "operate";
    field = "device_id";
  } else if (button.dataset.inventory === "true") {
    kind = "place";
  }
  $("action-kind").value = kind;
  renderFields();
  const control = [
    ...$("action-fields").querySelectorAll(`[name="${field}"]`),
  ].find((e) => e.tagName === "SELECT" || e.value === value);
  if (control?.type === "checkbox") control.checked = true;
  else if (control) control.value = value;
  control?.focus();
});
$("queue").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || !canAct()) return;
  if (button.dataset.remove !== undefined)
    queue.splice(Number(button.dataset.remove), 1);
  if (button.dataset.up !== undefined) {
    const i = Number(button.dataset.up);
    [queue[i - 1], queue[i]] = [queue[i], queue[i - 1]];
  }
  if (button.dataset.down !== undefined) {
    const i = Number(button.dataset.down);
    [queue[i + 1], queue[i]] = [queue[i], queue[i + 1]];
  }
  renderQueue();
});
$("clear-queue").addEventListener("click", () => {
  if (canAct()) {
    queue = [];
    renderQueue();
  }
});
$("submit-actions").addEventListener("click", async () => {
  if (!canAct() || !queue.length) return;
  const key = `queue:${state.session_id}:${state.pending.request_id}`;
  if (
    await post("submit", {
      actor_id: state.pending.actor_id,
      request_id: state.pending.request_id,
      actions: queue,
      auto: $("continue-auto").checked,
    })
  ) {
    // A retry request may already be available; never erase its newly edited draft.
    try {
      sessionStorage.removeItem(key);
    } catch {
      /* Optional storage. */
    }
  }
});
$("advance").addEventListener("click", () => post("advance", { auto: true }));
$("step").addEventListener("click", () => post("advance", { auto: false }));
$("pause").addEventListener("click", () => post("pause"));
$("stop").addEventListener("click", () => $("stop-dialog").showModal());
$("cancel-stop").addEventListener("click", () => $("stop-dialog").close());
$("confirm-stop").addEventListener("click", () => {
  $("stop-dialog").close();
  post("stop");
});
$("configure").addEventListener("click", () => {
  setupOpen = true;
  $("setup").hidden = false;
  $("play").hidden = true;
  $("setup").scrollIntoView({ behavior: "smooth" });
});
$("view-actor").addEventListener("change", async () => {
  actorChoice = $("view-actor").value;
  lastLog = "";
  await refresh();
});
$("player-tab").addEventListener("click", () => {
  logMode = "player";
  lastLog = "";
  renderLog();
});
$("world-tab").addEventListener("click", async () => {
  logMode = "world";
  lastLog = "";
  await refresh();
});
$("latest").addEventListener("click", () => {
  $("follow-log").checked = true;
  $("log").scrollTop = $("log").scrollHeight;
});
$("follow-log").addEventListener("change", () => {
  if ($("follow-log").checked) $("log").scrollTop = $("log").scrollHeight;
});
$("log").addEventListener(
  "wheel",
  (event) => {
    if (event.deltaY < 0) $("follow-log").checked = false;
  },
  { passive: true },
);

async function boot() {
  try {
    catalog = await api("/api/catalog");
    renderSetup();
    await poll();
  } catch (e) {
    error(`无法连接本地服务：${e.message}。请确认服务已经启动，再刷新页面。`);
  }
}
boot();
