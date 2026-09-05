"""Presentation helpers: action forms use only the participant's supplied DTO."""

from token_odyssey.kernel.events import Fact, Issue
from token_odyssey.translators.language import ACTION_HELP, render_fact, render_issue


ACTION_NAMES = dict(zip(
    ("move", "take", "give", "place", "hide", "show", "say", "search", "open", "close",
     "lock", "unlock", "install", "operate", "wait"),
    ("移动", "拿取", "交付", "放置", "藏起", "展示", "说话", "搜索", "打开", "关闭",
     "上锁", "解锁", "安装", "操作", "等待"), strict=True,
))


def action_catalog(registry):
    return [{"kind": kind, "name": ACTION_NAMES.get(kind, kind), "help": ACTION_HELP.get(kind, ""),
             "schema": registry.get(kind).intent_type.model_json_schema()} for kind in registry.kinds]


def issue_text(raw):
    return render_issue(Issue.model_validate(raw))


def event_text(event, labels):
    actor = labels.get(event.get("actor_id"), "世界")
    data, kind = event["data"], event["kind"]
    if event["source"] == "world":
        descriptions = [cue["fact"]["fields"]["description"] for cue in event["cues"]
                        if "description" in cue["fact"]["fields"]]
        return " ".join(dict.fromkeys(descriptions)) or f"机关 {event['mechanic_id']} 触发。"
    if kind == "wait":
        return f"{actor}等待。"
    if kind == "move":
        return f"{actor}移动到{labels.get(data.get('destination_room_id'), data.get('destination_room_id', '另一房间'))}。"
    if kind == "say":
        return f"{actor}说：{data['content']}"
    fields = {"actor_id": event["actor_id"], **data}
    if kind == "search":
        fields["object_id"] = data.get("container_id")
    if kind == "operate":
        fields["object_id"] = data.get("device_id")
    return render_fact(Fact(kind=kind, fields=fields), labels)
