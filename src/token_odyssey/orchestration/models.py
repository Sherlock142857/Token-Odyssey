"""Validated contracts exchanged by campaign-level agents."""

from typing import Literal

from pydantic import Field, model_validator

from token_odyssey.common import FrozenModel, Model
from token_odyssey.llm.contracts import ChatMessage


class CampaignCharacter(FrozenModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    personality: str = Field(min_length=1)
    public_role: str = Field(min_length=1)
    inner_life: str = Field(min_length=1)
    historical_tie: str = Field(min_length=1)


class CampaignBible(FrozenModel):
    title: str = Field(min_length=1)
    public_world: str = Field(min_length=1)
    major_history: tuple[str, ...] = Field(min_length=1)
    central_conflict: str = Field(min_length=1)
    protagonist_id: str = Field(min_length=1)
    characters: tuple[CampaignCharacter, ...] = Field(min_length=1)
    protagonist_ties: tuple[str, ...] = Field(min_length=1)
    main_threads: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self):
        ids = [character.id for character in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("campaign character IDs must be unique")
        if self.protagonist_id not in ids:
            raise ValueError("protagonist_id must reference a campaign character")
        return self

    def character(self, actor_id: str) -> CampaignCharacter | None:
        return next((character for character in self.characters if character.id == actor_id), None)


class ActBrief(FrozenModel):
    act_number: int = Field(ge=1)
    title: str = Field(min_length=1)
    dramatic_purpose: str = Field(min_length=1)
    opening: str = Field(min_length=1)
    player_goal: str = Field(min_length=1)
    cast_ids: tuple[str, ...] = Field(min_length=1)
    required_entity_ids: tuple[str, ...] = ()
    continuity_notes: tuple[str, ...] = ()
    is_finale: bool = False

    @model_validator(mode="after")
    def unique_cast(self):
        if len(self.cast_ids) != len(set(self.cast_ids)):
            raise ValueError("act cast_ids must be unique")
        return self


class CampaignGenesis(FrozenModel):
    bible: CampaignBible
    first_act: ActBrief

    @model_validator(mode="after")
    def first_act_rules(self):
        if self.first_act.act_number != 1 or self.first_act.is_finale:
            raise ValueError("the first act must be a non-finale act numbered 1")
        if self.bible.protagonist_id not in self.first_act.cast_ids:
            raise ValueError("the first act must include the protagonist")
        if len(self.first_act.cast_ids) > 3:
            raise ValueError("the first act may contain at most three characters")
        return self


class WorldSummary(FrozenModel):
    summary: str = Field(min_length=1)
    confirmed_changes: tuple[str, ...] = ()
    unresolved_outcomes: tuple[str, ...] = ()


class CharacterInterlude(FrozenModel):
    observer_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=1000)


class DirectorTransition(FrozenModel):
    decision: Literal["continue", "prepare_finale"]
    act_assessment: str = Field(min_length=1)
    resolved_threads: tuple[str, ...] = ()
    remaining_threads: tuple[str, ...] = ()
    public_interlude: str = Field(min_length=1)
    character_interludes: tuple[CharacterInterlude, ...] = ()
    continuity_notes: tuple[str, ...] = ()
    next_act: ActBrief

    @model_validator(mode="after")
    def finale_gate(self):
        if self.decision == "prepare_finale":
            if self.remaining_threads:
                raise ValueError("a finale cannot begin while main threads remain unresolved")
            if not self.next_act.is_finale:
                raise ValueError("prepare_finale requires next_act.is_finale=true")
        elif self.next_act.is_finale:
            raise ValueError("continue requires next_act.is_finale=false")
        return self


class DirectorConclusion(FrozenModel):
    decision: Literal["conclude"]
    final_narration: str = Field(min_length=1)
    ending_summary: str = Field(min_length=1)
    resolved_threads: tuple[str, ...] = ()
    remaining_threads: tuple[str, ...] = ()

    @model_validator(mode="after")
    def no_dangling_threads(self):
        if self.remaining_threads:
            raise ValueError("the campaign cannot conclude with unresolved main threads")
        return self


class CharacterMemory(FrozenModel):
    actor_id: str = Field(min_length=1)
    durable_memories: tuple[str, ...] = Field(default=(), max_length=10)
    inner_state: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def memory_lengths(self):
        if any(len(memory) > 160 for memory in self.durable_memories):
            raise ValueError("each durable memory must be at most 160 characters")
        return self


class EntityCanon(FrozenModel):
    kind: str
    name: str


class ActOutcome(FrozenModel):
    act_number: int
    status: str
    reason: Literal["engine_completed", "player_ended", "budget_exhausted"]
    run_dir: str
    turns: int = 0
    events: int = 0


class CampaignState(Model):
    schema_version: Literal[1] = 1
    campaign_id: str
    phase: Literal[
        "idle", "creating_world", "preparing_act", "interlude", "playing_act",
        "summarizing", "directing", "remembering", "completed", "technical_failed", "abandoned",
    ] = "idle"
    checkpoint_kind: Literal["none", "act_ready", "act_finished", "next_act_ready"] = "none"
    creative_brief: str = ""
    seed: int = 7
    act_number: int = 0
    protagonist_id: str | None = None
    bible: CampaignBible | None = None
    current_act_brief: ActBrief | None = None
    current_scenario: dict | None = None
    current_is_finale: bool = False
    interlude: str = ""
    memories: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    inner_states: dict[str, str] = Field(default_factory=dict)
    entity_canon: dict[str, EntityCanon] = Field(default_factory=dict)
    director_messages: list[ChatMessage] = Field(default_factory=list)
    director_completed_ops: set[str] = Field(default_factory=set)
    remembered_for_act: set[str] = Field(default_factory=set)
    retry_epoch: int = 0
    act_outcome: ActOutcome | None = None
    world_summary: WorldSummary | None = None
    director_transition: DirectorTransition | None = None
    conclusion: DirectorConclusion | None = None
    current_act_run_dir: str | None = None
    error: str | None = None
    retry_phase: str | None = None
    resume_notice: str | None = None
