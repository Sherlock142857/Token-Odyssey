"""Deterministic full and redacted event rendering."""

from airpg.models import ActionKind, EventKind, ObservationLevel, WorldEvent, WorldState


def render_event(state: WorldState, event: WorldEvent, level: ObservationLevel) -> str:
    actor_name = state.actors[event.actor_id].name
    if level == ObservationLevel.PARTIAL:
        if event.kind == EventKind.DIALOGUE:
            return f"你察觉到{actor_name}在和人说话，但没有听清内容。"
        if event.action_kind == ActionKind.MOVE:
            return f"你察觉到{actor_name}移动了位置，但没看清去向。"
        if event.action_kind == ActionKind.WAIT:
            return f"你隐约注意到{actor_name}停顿了一会儿。"
        return f"你察觉到{actor_name}做了什么，但没看清具体动作或对象。"

    if event.kind == EventKind.DIALOGUE:
        targets = "、".join(
            state.actors[target_id].name for target_id in event.data["target_actor_ids"]
        )
        return f"{actor_name}对{targets}说：“{event.data['content']}”"

    action = event.action_kind
    if action == ActionKind.MOVE:
        old_room = state.rooms[str(event.data["from_room_id"])].name
        new_room = state.rooms[str(event.data["to_room_id"])].name
        return f"{actor_name}从「{old_room}」移动到「{new_room}」。"
    if action == ActionKind.SEARCH:
        item = state.items[str(event.data["container_id"])]
        return f"{actor_name}搜索了「{item.name}」。"
    if action == ActionKind.TAKE:
        item = state.items[str(event.data["item_id"])]
        return f"{actor_name}拿起了「{item.name}」。"
    if action == ActionKind.GIVE:
        item = state.items[str(event.data["item_id"])]
        recipient = state.actors[str(event.data["recipient_id"])]
        return f"{actor_name}把「{item.name}」交给了{recipient.name}。"
    if action == ActionKind.PLACE:
        item = state.items[str(event.data["item_id"])]
        container = state.items[str(event.data["container_id"])]
        return f"{actor_name}把「{item.name}」放进了「{container.name}」。"
    if action == ActionKind.SHOW:
        item = state.items[str(event.data["item_id"])]
        targets = "、".join(
            state.actors[target_id].name for target_id in event.data["audience_ids"]
        )
        return f"{actor_name}向{targets}展示了「{item.name}」。"
    if action == ActionKind.HIDE:
        item = state.items[str(event.data["item_id"])]
        return f"{actor_name}把「{item.name}」藏在了自己身上。"
    return f"{actor_name}暂时没有采取行动。"

