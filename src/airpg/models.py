"""Validated domain models used by every layer of the engine."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PerformanceMode(StrEnum):
    NORMAL = "normal"
    SECRETIVE = "secretive"
    CONSPICUOUS = "conspicuous"


class ActionKind(StrEnum):
    MOVE = "move"
    SEARCH = "search"
    TAKE = "take"
    GIVE = "give"
    PLACE = "place"
    SHOW = "show"
    HIDE = "hide"
    WAIT = "wait"


class LocationKind(StrEnum):
    ROOM = "room"
    CONTAINER = "container"
    HELD = "held"
    HIDDEN = "hidden"
    ATTACHED = "attached"


class EventKind(StrEnum):
    ACTION = "action"
    DIALOGUE = "dialogue"


class ObservationLevel(StrEnum):
    PARTIAL = "partial"
    FULL = "full"


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Location(StrictModel):
    kind: LocationKind
    target_id: str = Field(min_length=1)


class ModelSettings(StrictModel):
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    max_tokens: int = Field(default=900, ge=64, le=8192)
    thinking_enabled: bool = False


class Room(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str


class Actor(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    room_id: str = Field(min_length=1)
    personality: str
    appearance: str = ""
    traits: list[str] = Field(default_factory=list)
    pre_act_memory: str = ""
    act_memories: list[str] = Field(default_factory=list)
    private_goal: str = ""
    hide_capacity: int = Field(default=2, ge=0, le=10)
    model: ModelSettings = Field(default_factory=ModelSettings)


class Item(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    detailed_description: str
    size: int = Field(ge=1, le=10)
    location: Location
    owner_id: str | None = None
    container_capacity: int | None = Field(default=None, ge=1, le=20)
    content_visibility: float = Field(default=0.0, ge=0.0, le=1.0)
    base_visibility: float = Field(default=1.0, ge=0.0, le=1.0)

    @property
    def is_container(self) -> bool:
        return self.container_capacity is not None


class WorldState(StrictModel):
    rooms: dict[str, Room]
    actors: dict[str, Actor]
    items: dict[str, Item]
    # Matrix direction is observer room -> source room.
    space_visibility: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_references(self) -> "WorldState":
        for key, room in self.rooms.items():
            if key != room.id:
                raise ValueError(f"room mapping key {key!r} does not match id {room.id!r}")
        for key, actor in self.actors.items():
            if key != actor.id:
                raise ValueError(f"actor mapping key {key!r} does not match id {actor.id!r}")
            if actor.room_id not in self.rooms:
                raise ValueError(f"actor {actor.id!r} references unknown room {actor.room_id!r}")
        for key, item in self.items.items():
            if key != item.id:
                raise ValueError(f"item mapping key {key!r} does not match id {item.id!r}")
            self._validate_location_reference(item)
            if item.owner_id is not None and item.owner_id not in self.actors:
                raise ValueError(f"item {item.id!r} has unknown owner {item.owner_id!r}")
        for item_id in self.items:
            self._assert_no_containment_cycle(item_id)
        for observer_room, row in self.space_visibility.items():
            if observer_room not in self.rooms:
                raise ValueError(f"visibility matrix has unknown room {observer_room!r}")
            for source_room, value in row.items():
                if source_room not in self.rooms:
                    raise ValueError(f"visibility matrix has unknown room {source_room!r}")
                if not 0.0 <= value <= 1.0:
                    raise ValueError("space visibility values must be between 0 and 1")
        return self

    def _validate_location_reference(self, item: Item) -> None:
        location = item.location
        if location.kind == LocationKind.ROOM and location.target_id not in self.rooms:
            raise ValueError(f"item {item.id!r} references unknown room {location.target_id!r}")
        if location.kind == LocationKind.CONTAINER:
            container = self.items.get(location.target_id)
            if container is None:
                raise ValueError(f"item {item.id!r} references unknown container {location.target_id!r}")
            if not container.is_container:
                raise ValueError(f"item {item.id!r} is inside non-container {container.id!r}")
        if location.kind in {LocationKind.HELD, LocationKind.HIDDEN, LocationKind.ATTACHED}:
            if location.target_id not in self.actors:
                raise ValueError(f"item {item.id!r} references unknown actor {location.target_id!r}")

    def _assert_no_containment_cycle(self, item_id: str) -> None:
        seen = {item_id}
        current = self.items[item_id]
        while current.location.kind == LocationKind.CONTAINER:
            parent_id = current.location.target_id
            if parent_id in seen:
                raise ValueError(f"containment cycle involving item {item_id!r}")
            seen.add(parent_id)
            current = self.items[parent_id]

    def effective_room_of_item(self, item_id: str) -> str:
        item = self.items[item_id]
        location = item.location
        if location.kind == LocationKind.ROOM:
            return location.target_id
        if location.kind == LocationKind.CONTAINER:
            return self.effective_room_of_item(location.target_id)
        return self.actors[location.target_id].room_id

    def item_exposure(self, item_id: str) -> float:
        item = self.items[item_id]
        exposure = item.base_visibility
        location = item.location
        if location.kind == LocationKind.CONTAINER:
            container = self.items[location.target_id]
            return exposure * container.content_visibility * self.item_exposure(container.id)
        if location.kind == LocationKind.HIDDEN:
            return exposure * 0.05
        if location.kind == LocationKind.HELD:
            return exposure * 0.85
        if location.kind == LocationKind.ATTACHED:
            return exposure * 0.95
        return exposure

    def spatial_visibility(self, observer_room: str, source_room: str) -> float:
        if observer_room == source_room:
            return 1.0
        return self.space_visibility.get(observer_room, {}).get(source_room, 0.0)


class BaseActionIntent(StrictModel):
    mode: PerformanceMode = PerformanceMode.NORMAL


class MoveActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.MOVE] = ActionKind.MOVE
    destination_room_id: str


class SearchActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.SEARCH] = ActionKind.SEARCH
    target_item_id: str


class TakeActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.TAKE] = ActionKind.TAKE
    target_item_id: str


class GiveActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.GIVE] = ActionKind.GIVE
    target_item_id: str
    recipient_id: str


class PlaceActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.PLACE] = ActionKind.PLACE
    target_item_id: str
    container_id: str


class ShowActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.SHOW] = ActionKind.SHOW
    target_item_id: str
    audience_ids: list[str] = Field(min_length=1)


class HideActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.HIDE] = ActionKind.HIDE
    target_item_id: str


class WaitActionIntent(BaseActionIntent):
    kind: Literal[ActionKind.WAIT] = ActionKind.WAIT


ActionIntent = Annotated[
    MoveActionIntent
    | SearchActionIntent
    | TakeActionIntent
    | GiveActionIntent
    | PlaceActionIntent
    | ShowActionIntent
    | HideActionIntent
    | WaitActionIntent,
    Field(discriminator="kind"),
]


class DialogueIntent(StrictModel):
    target_actor_ids: list[str] = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4000)
    mode: PerformanceMode = PerformanceMode.NORMAL


class TurnIntent(StrictModel):
    actor_id: str
    private_thought: str = ""
    action: ActionIntent | None = None
    dialogue: DialogueIntent | None = None


class ChatMessage(StrictModel):
    role: ChatRole
    content: str


class TokenUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class AgentDecision(StrictModel):
    actor_id: str
    raw_content: str
    intent: TurnIntent | None = None
    output_error: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str | None = None
    response_id: str | None = None


class WorldEvent(StrictModel):
    sequence: int = Field(ge=1)
    round_number: int = Field(ge=1)
    actor_id: str
    kind: EventKind
    mode: PerformanceMode
    action_kind: ActionKind | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    detail_visibility: float = Field(default=1.0, ge=0.0, le=1.0)
    direct_observer_ids: list[str] = Field(default_factory=list)


class Observation(StrictModel):
    observer_id: str
    level: ObservationLevel
    text: str
    round_number: int = Field(ge=0)
    source_event_sequence: int | None = None
    is_system_update: bool = False


class KnownItem(StrictModel):
    item_id: str
    name: str
    description: str
    last_known_location: Location
    currently_visible: bool = True


class AgentKnowledge(StrictModel):
    items: dict[str, KnownItem] = Field(default_factory=dict)
    known_actor_rooms: dict[str, str] = Field(default_factory=dict)


class AgentRuntime(StrictModel):
    actor_id: str
    knowledge: AgentKnowledge = Field(default_factory=AgentKnowledge)
    observations: list[Observation] = Field(default_factory=list)
    private_thoughts: list[str] = Field(default_factory=list)
    last_validation_error: str | None = None


class AgentSession(StrictModel):
    actor_id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    observation_cursor: int = Field(default=0, ge=0)
    call_count: int = Field(default=0, ge=0)


class Scenario(StrictModel):
    id: str
    title: str
    world_history: str
    act_background: str
    action_guidance: str = ""
    max_rounds: int = Field(default=50, ge=1, le=10000)
    seed: int = 7
    world: WorldState
