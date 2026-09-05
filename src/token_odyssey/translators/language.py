"""Editable Chinese wording for structured observations and feedback.

These functions only receive already authorized fields and labels. Adding prose
must never require querying canonical world state.
"""

from token_odyssey.kernel.events import Fact, Issue


ACTION_HELP = {
    "move": "沿可通行的出口移动；默认移动成功后结束动作队列。",
    "take": "取出可接触的物品；取下安装件也会解除安装连接。",
    "give": "把持有且可接触的一件物品交给同房角色。",
    "place": "将持有物品放入容器或放在表面；inside 表示内部，attached 表示表面。",
    "hide": "把持有的小物品藏在自己身上。",
    "show": "向同房、能看见你的角色展示持有物品。",
    "say": "说话；listener_ids 是交谈对象，可为空。发言不改变所描述的世界事实。",
    "search": "仔细查看打开的容器；结果中的新对象需要等收到下一次上下文后再引用。",
    "open": "打开未上锁的容器或门。",
    "close": "关闭容器或门，不自动上锁。",
    "lock": "使用持有的匹配钥匙给关闭的容器或门上锁。",
    "unlock": "使用持有的匹配钥匙解锁，不自动打开。",
    "install": "把持有的兼容组件接入插槽；仅 attached 不代表已安装。",
    "operate": "操作可操作的设备；是否发生反应由世界决定。",
    "wait": "等待。",
}

ISSUE_TEXT = {
    "EXPECTED_ITEM": "参数需要一个物品。", "EXPECTED_CHARACTER": "参数需要一个角色。",
    "EXPECTED_ROOM": "目的地需要是房间。", "NOT_HELD": "你没有持有该物品。",
    "NOT_ACCESSIBLE": "目前无法接触该对象，请检查所在房间、容器开闭和物品持有关系。",
    "CLOSED_CONTAINER_BLOCKS_ACCESS": "关闭的容器阻挡了接触路径，需要先打开。",
    "CONTROLLED_BY_OTHER": "该物品在其他角色的控制中，不能直接拿取。",
    "NOT_COLOCATED": "对方不在同一房间，无法直接互动。", "SELF_TARGET": "不能把自己指定为这个动作的对象。",
    "NO_OPEN_PASSAGE": "没有可通行的相邻通道通往该房间。", "NOT_PORTABLE": "该物品不能被搬动。",
    "NOT_CONTAINER": "该对象不是容器。", "CONTAINER_CLOSED": "容器关闭着，需要先打开。",
    "LOCKED": "对象锁着，需要先解锁。", "WRONG_KEY": "这把钥匙不匹配。",
    "CLOSE_BEFORE_LOCK": "需要先关闭，再上锁。", "TOO_LARGE": "物品超过容纳尺寸。",
    "NOT_SLOT": "目标不是安装插槽。", "SLOT_OCCUPIED": "插槽已经安装了组件。",
    "INCOMPATIBLE_COMPONENT": "组件与插槽不兼容。", "NOT_OPERABLE": "该物品不能使用 operate 操作。",
    "UNKNOWN_TO_ACTOR": "动作引用了你在作出本次决定时尚不知道的对象。",
    "INVALID_INTENT": "动作参数格式无效。", "INVALID_OUTPUT": "回复未能解析为动作队列。",
    "WRONG_ACTOR": "只能为自己的角色提交动作。", "BATCH_TOO_LONG": "提交的动作数量超过本回合上限。",
    "ALREADY_THERE": "你已经在该房间，没有发生移动。", "ALREADY_PLACED": "物品已经处于该位置。",
    "ALREADY_SET": "对象已经处于请求的状态。", "PLACEMENT_CYCLE": "放置会形成循环包含。",
    "USE_GIVE_OR_HIDE": "向角色交付使用 give；藏在自己身上使用 hide。",
    "CANNOT_SEE_SHOW": "对方目前看不到你的展示。", "MISSING_CAPABILITY": "对象不具备该动作需要的能力。",
    "MOVE_ENDS_BATCH": "移动已经成功，余下动作未执行；请根据新环境再决定。",
    "BATCH_STOPPED": "队列已停止，此前成功的动作保留，余下动作未执行。",
    "FALLBACK_WAIT": "本回合未能提交可执行动作，已等待。",
}


def render_issue(issue: Issue) -> str:
    text = ISSUE_TEXT.get(issue.code, issue.code)
    if issue.details:
        details = "；".join(f"{k}={v}" for k, v in issue.details.items())
        text += f"（{details}）"
    return text


def render_fact(fact: Fact, labels: dict[str, str]) -> str:
    def name(key, default="某人"):
        value = fact.fields.get(key)
        return labels.get(value, value) if isinstance(value, str) else default

    kind, fields = fact.kind, fact.fields
    actor = name("actor_id", "你")
    obj = name("item_id", "物品")
    target = name("object_id", "对象")
    if kind == "departure": return f"{actor}离开了这里。"
    if kind == "arrival": return f"{actor}来到了这里。"
    if kind == "travel_result": return f"你已到达{name('room_id', '目的地')}。"
    if kind == "handling": return "附近有人在摆弄或交接物品。"
    if kind == "voice": return "你听到了说话声。"
    if kind == "speech": return f"{name('actor_id', '一个声音')}说：{fields['content']}"
    if kind == "speaker": return f"你看到{actor}在说话。"
    if kind == "take": return f"{actor}取出了{obj}。"
    if kind == "give": return f"{actor}把{obj}交给了{name('recipient_id')}。"
    if kind == "place": return f"{actor}把{obj}放到了{name('destination_id', '一处位置')}（{fields['relation']}）。"
    if kind == "hide": return f"{actor}把{obj}藏在身上。"
    if kind == "install": return f"{actor}将{obj}安装到了{name('destination_id', '插槽')}。"
    if kind == "show": return f"{actor}展示了{obj}。"
    if kind == "search": return f"{actor}仔细查看了{target}。"
    if kind == "discovery": return f"你辨认出了{name('entity_id', '一个对象')}。"
    if kind == "item_location": return f"你确认了{obj}的位置。"
    if kind in {"open", "close", "lock", "unlock"}:
        verb = {"open": "打开", "close": "关闭", "lock": "锁上", "unlock": "解锁"}[kind]
        return f"{actor}{verb}了{target}。"
    if kind == "operate": return f"{actor}操作了{target}。"
    if kind in {"mechanism_seen", "mechanism_heard"}: return str(fields["description"])
    # Custom fact kinds still produce readable, safe output before a dedicated
    # language template is registered. Only authorized fields are present here.
    return f"{kind}：" + "；".join(f"{key}={value}" for key, value in fields.items())
