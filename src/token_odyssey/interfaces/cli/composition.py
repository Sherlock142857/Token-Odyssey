"""CLI composition root with generic driver and Participant factory dictionaries."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from token_odyssey.agents.llm_agent import AgentIdentity, LLMAgent
from token_odyssey.agents.scripted import DemoAgent
from token_odyssey.config.models import BackendConfig, ParticipantConfig, RunConfig
from token_odyssey.inside_act.actions.registry import ActionRegistry
from token_odyssey.inside_act.domain.scenario import Scenario
from token_odyssey.llm.providers import OpenAICompatibleBackend
from token_odyssey.llm.registry import LLMBackendRegistry, LLMProfileRegistry
from token_odyssey.scenario import read_api_key


BackendFactory = Callable[[BackendConfig], object]


def _openai_compatible(config: BackendConfig):
    if config.api_key_env:
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ValueError(f"environment variable {config.api_key_env!r} is not set")
    elif config.api_key_file:
        api_key = read_api_key(Path(config.api_key_file))
    else:
        raise ValueError("backend requires api_key_env or api_key_file")
    return OpenAICompatibleBackend(api_key=api_key, base_url=config.base_url)


BACKEND_FACTORIES: dict[str, BackendFactory] = {
    "openai_compatible": _openai_compatible,
}


def build_backend_registry(config: RunConfig) -> LLMBackendRegistry:
    backends = {}
    for backend_id, backend_config in config.backends.items():
        try:
            factory = BACKEND_FACTORIES[backend_config.driver]
        except KeyError as exc:
            raise ValueError(f"unknown backend driver {backend_config.driver!r}") from exc
        backends[backend_id] = factory(backend_config)
    return LLMBackendRegistry(backends)


def build_participants(
    scenario: Scenario, config: RunConfig, registry: ActionRegistry
) -> dict[str, object]:
    backend_registry = build_backend_registry(config)
    profile_registry = LLMProfileRegistry(config.llm_profiles)

    def build_demo(_actor_id: str, _participant: ParticipantConfig):
        return DemoAgent(registry)

    def build_llm(actor_id: str, participant: ParticipantConfig):
        if participant.mode is None:
            raise ValueError(f"LLM participant {actor_id!r} requires mode")
        return LLMAgent(
            identity=_identity(scenario, actor_id),
            mode=participant.mode,
            action_registry=registry,
            backend_registry=backend_registry,
            profile_registry=profile_registry,
        )

    factories = {"demo": build_demo, "llm": build_llm}
    result = {}
    for actor_id in scenario.world.character_ids:
        participant = config.cast.get(actor_id)
        if participant is None:
            raise ValueError(f"RunConfig missing cast entry for {actor_id!r}")
        try:
            factory = factories[participant.adapter]
        except KeyError as exc:
            raise ValueError(f"unknown Participant adapter {participant.adapter!r}") from exc
        result[actor_id] = factory(actor_id, participant)
    return result


def _identity(scenario: Scenario, actor_id: str) -> AgentIdentity:
    actor = scenario.world.character(actor_id)
    return AgentIdentity(
        actor_id=actor.id,
        name=actor.name,
        personality=actor.personality,
        appearance=actor.appearance,
        traits=tuple(actor.traits),
        pre_act_memory=actor.pre_act_memory,
        act_memories=tuple(actor.act_memories),
        private_goal=actor.private_goal,
        world_history=scenario.world_history,
        act_background=scenario.act_background,
        action_guidance=scenario.action_guidance,
        room_catalog=tuple(
            (room_id, scenario.world.room(room_id).name)
            for room_id in sorted(scenario.world.room_ids)
        ),
    )
