"""Append-only, per-actor conversational context construction."""

from __future__ import annotations

from pydantic import Field

from airpg.debug import DebugSink, NullDebugSink
from airpg.models import (
    AgentRuntime,
    AgentSession,
    ChatMessage,
    ChatRole,
    Scenario,
    StrictModel,
)
from airpg.recording import NullRunRecorder


class AgentContext(StrictModel):
    actor_id: str
    conversation: list[ChatMessage]
    current_room_id: str
    colocated_actor_ids: list[str] = Field(default_factory=list)
    known_item_ids: list[str] = Field(default_factory=list)
    visible_item_ids: list[str] = Field(default_factory=list)
    controlled_item_ids: list[str] = Field(default_factory=list)
    known_container_ids: list[str] = Field(default_factory=list)
    all_room_ids: list[str] = Field(default_factory=list)

    def messages(self) -> list[dict[str, str]]:
        return [message.model_dump(mode="json") for message in self.conversation]


class ContextBuilder:
    def __init__(
        self,
        scenario: Scenario,
        debug: DebugSink | None = None,
        recorder: object | None = None,
    ) -> None:
        self.scenario = scenario
        self.state = scenario.world
        self.debug = debug or NullDebugSink()
        self.recorder = recorder or NullRunRecorder()

    def begin_turn(
        self,
        session: AgentSession,
        runtime: AgentRuntime,
        round_number: int,
    ) -> AgentContext:
        """Append only new information; never rebuild, summarize or truncate history."""
        if not session.messages:
            self._append(
                session,
                ChatMessage(role=ChatRole.SYSTEM, content=self._system_prompt(runtime.actor_id)),
                round_number=0,
                purpose="static_system_prompt",
            )
            purpose = "initial_projection"
        else:
            purpose = "turn_delta"

        new_observations = runtime.observations[session.observation_cursor :]
        delta = self._turn_delta(runtime, round_number, new_observations)
        self._append(
            session,
            ChatMessage(role=ChatRole.USER, content=delta),
            round_number=round_number,
            purpose=purpose,
        )
        session.observation_cursor = len(runtime.observations)
        runtime.last_validation_error = None
        return self.snapshot(session, runtime)

    def append_assistant(
        self,
        session: AgentSession,
        runtime: AgentRuntime,
        raw_content: str,
        *,
        round_number: int,
        purpose: str = "agent_decision",
    ) -> AgentContext:
        self._append(
            session,
            ChatMessage(role=ChatRole.ASSISTANT, content=raw_content),
            round_number=round_number,
            purpose=purpose,
        )
        session.call_count += 1
        return self.snapshot(session, runtime)

    def append_feedback(
        self,
        session: AgentSession,
        runtime: AgentRuntime,
        reasons: list[str],
        *,
        round_number: int,
    ) -> AgentContext:
        feedback = (
            "【World Harness 私有反馈】\n"
            "你刚才提交的意图没有在世界中发生，也没有被其他角色观察到。\n"
            f"拒绝原因：{'；'.join(reasons)}\n"
            "请保持角色立场，只修正不合法部分，并重新输出完整 JSON。"
        )
        runtime.last_validation_error = "；".join(reasons)
        self._append(
            session,
            ChatMessage(role=ChatRole.USER, content=feedback),
            round_number=round_number,
            purpose="validation_feedback",
        )
        return self.snapshot(session, runtime)

    def snapshot(self, session: AgentSession, runtime: AgentRuntime) -> AgentContext:
        actor = self.state.actors[runtime.actor_id]
        colocated = [
            other
            for other in self.state.actors.values()
            if other.id != actor.id and other.room_id == actor.room_id
        ]
        visible_ids = [
            known.item_id for known in runtime.knowledge.items.values() if known.currently_visible
        ]
        controlled_ids = [
            item.id
            for item in self.state.items.values()
            if item.location.target_id == actor.id
            and item.location.kind.value in {"held", "hidden", "attached"}
        ]
        known_container_ids = [
            item_id
            for item_id in runtime.knowledge.items
            if self.state.items[item_id].is_container
        ]
        context = AgentContext(
            actor_id=actor.id,
            conversation=list(session.messages),
            current_room_id=actor.room_id,
            colocated_actor_ids=[other.id for other in colocated],
            known_item_ids=list(runtime.knowledge.items),
            visible_item_ids=visible_ids,
            controlled_item_ids=controlled_ids,
            known_container_ids=known_container_ids,
            all_room_ids=list(self.state.rooms),
        )
        self.debug.emit(
            "context",
            f"为 {actor.name} 构建 append-only 上下文（{len(session.messages)} 条消息）",
            context,
        )
        return context

    def _system_prompt(self, actor_id: str) -> str:
        actor = self.state.actors[actor_id]
        rooms = "\n".join(
            f"- {room.name} (id: {room.id})" for room in self.state.rooms.values()
        )
        # Keep global rules first so all actors share the longest possible cache prefix.
        return f"""你是话剧式 RPG 世界中的一名角色。所有角色地位完全相同；你不知道，也不应猜测，哪个角色由人类或 AI 控制。
世界事实只能来自系统给你的观察。不要虚构未观察到的物品、位置或事件。你只提交行动和说话意图，程序负责合法性、状态变化和其他角色能否观察。
你应维护自己的利益、偏见和不确定性，不必迎合任何角色，也不要为了配合某个人而放弃立场。

【世界历史】
{self.scenario.world_history}

【当前 Act 背景】
{self.scenario.act_background}

【可移动房间】
{rooms}

【动作规范】
每回合最多选择一个物理动作，并可同时说一段话。若两者都有，对话先发生，物理动作随后发生。
- move: destination_room_id
- search: target_item_id
- take: target_item_id
- give: target_item_id + recipient_id
- place: target_item_id + container_id
- show: target_item_id + audience_ids
- hide: target_item_id
- wait: 无额外参数
动作 JSON 必须只包含该动作需要的字段，不要为其他动作字段填写 null。例如：
- {{"kind":"move","mode":"normal","destination_room_id":"study"}}
- {{"kind":"search","mode":"normal","target_item_id":"box"}}
- {{"kind":"take","mode":"secretive","target_item_id":"key"}}
- {{"kind":"give","mode":"normal","target_item_id":"key","recipient_id":"actor"}}
- {{"kind":"place","mode":"normal","target_item_id":"key","container_id":"box"}}
- {{"kind":"show","mode":"conspicuous","target_item_id":"letter","audience_ids":["actor"]}}
- {{"kind":"hide","mode":"secretive","target_item_id":"letter"}}
- {{"kind":"wait","mode":"normal"}}
动作和对话各自可选择 normal、secretive、conspicuous。对话、展示、给予对象只能是当前同房间角色，且不会使任何角色移动。
{self.scenario.action_guidance}

只输出一个 JSON 对象，不要 Markdown 或额外解释：
{{
  "private_thought": "只属于自己的想法",
  "action": {{"kind": "动作名", "mode": "normal|secretive|conspicuous", "该动作所需参数": "..."}},
  "dialogue": {{
    "target_actor_ids": ["角色 id"],
    "content": "说的话",
    "mode": "normal|secretive|conspicuous"
  }}
}}
不做动作或不说话时，对应字段为 null。两者都不做时必须明确选择 wait。

【你的角色身份】
姓名：{actor.name}（id: {actor.id}）
性格：{actor.personality}
外貌：{actor.appearance}
特性：{'、'.join(actor.traits) if actor.traits else '无额外说明'}
Act 前记忆：{actor.pre_act_memory or '无'}
已完成 Act 记忆：{'；'.join(actor.act_memories) if actor.act_memories else '无'}
私人目标：{actor.private_goal or '按自己的性格对局势作出反应。'}"""

    def _turn_delta(self, runtime: AgentRuntime, round_number: int, observations: list) -> str:
        actor = self.state.actors[runtime.actor_id]
        room = self.state.rooms[actor.room_id]
        colocated = [
            other
            for other in self.state.actors.values()
            if other.id != actor.id and other.room_id == actor.room_id
        ]
        visible = [
            known for known in runtime.knowledge.items.values() if known.currently_visible
        ]
        controlled = [
            item
            for item in self.state.items.values()
            if item.location.target_id == actor.id
            and item.location.kind.value in {"held", "hidden", "attached"}
        ]
        observation_lines = [
            f"- [第{obs.round_number}轮/{obs.level.value}] {obs.text}" for obs in observations
        ]
        actor_lines = [
            f"- {other.name} (id: {other.id})：{other.appearance or '外貌没有特别说明'}"
            for other in colocated
        ]
        item_lines = [
            f"- {known.name} (id: {known.item_id})；位置="
            f"{known.last_known_location.kind.value}:{known.last_known_location.target_id}"
            for known in visible
        ]
        controlled_lines = [f"- {item.name} (id: {item.id})" for item in controlled]
        return f"""【第 {round_number} 轮：你获得行动权】

【自上次行动权以来的新观察】
{chr(10).join(observation_lines) if observation_lines else '- 没有新观察'}

【当前行动条件快照】
位置：{room.name} (id: {room.id})
同房间角色：
{chr(10).join(actor_lines) if actor_lines else '- 无'}
当前明确可见/可互动的物品：
{chr(10).join(item_lines) if item_lines else '- 无'}
由你持有、藏匿或附着在你身上的物品：
{chr(10).join(controlled_lines) if controlled_lines else '- 无'}

请结合完整对话历史和以上新增信息决定本回合意图。"""

    def _append(
        self,
        session: AgentSession,
        message: ChatMessage,
        *,
        round_number: int,
        purpose: str,
    ) -> None:
        session.messages.append(message)
        self.recorder.record_agent_message(
            session.actor_id,
            message,
            round_number=round_number,
            purpose=purpose,
            message_index=len(session.messages) - 1,
        )
