"""Composition root: build controllers and transports without changing world rules."""

import os
from collections.abc import Callable

from token_odyssey.agents.human import HumanAgent
from token_odyssey.agents.llm_agent import LLMAgent
from token_odyssey.agents.scripted import ScriptedAgent
from token_odyssey.config.models import BackendConfig, ParticipantConfig, RunConfig, read_api_key
from token_odyssey.kernel.actions.registry import ActionRegistry
from token_odyssey.llm.providers import OpenAICompatibleBackend
from token_odyssey.perception.models import EntityView
from token_odyssey.recording import NullRecorder
from token_odyssey.scenario import RoleBrief, Scenario
from token_odyssey.translators.human import HumanTranslator
from token_odyssey.translators.llm import LLMIdentity, LLMTranslator


def _openai_compatible(config: BackendConfig):
    key = os.environ.get(config.api_key_env) if config.api_key_env else read_api_key(config.api_key_file)
    if not key:
        raise ValueError(f"missing API key environment variable {config.api_key_env}")
    return OpenAICompatibleBackend(api_key=key, base_url=config.base_url)


BACKEND_FACTORIES: dict[str, Callable] = {"openai_compatible": _openai_compatible}


def build_backend(config: BackendConfig):
    if config.driver not in BACKEND_FACTORIES:
        raise ValueError(f"unknown backend driver {config.driver}")
    return BACKEND_FACTORIES[config.driver](config)


def identity_for(scenario: Scenario, actor_id: str) -> LLMIdentity:
    actor = scenario.world.entities[actor_id]
    brief = scenario.roles.get(actor_id, RoleBrief())
    known = tuple(EntityView(id=obj.id, name=obj.name, description=obj.description,
                             kind=getattr(obj, "kind", "passage"), basis="prior")
                  for obj in (scenario.world.object(key) for key in brief.known_entity_ids))
    return LLMIdentity(actor_id=actor_id, name=actor.name, description=actor.description, act_title=scenario.title,
                       public_background=scenario.public_background,
                       personality=brief.personality, private_goal=brief.private_goal,
                       memories=brief.memories, known_entities=known)


def build_scripted_participants(scenario: Scenario, registry: ActionRegistry):
    return {actor: ScriptedAgent(actor, [registry.parse_batch(raw) for raw in scenario.scripts.get(actor, ())], registry)
            for actor in scenario.world.character_ids}


def build_participants(scenario: Scenario, config: RunConfig, registry: ActionRegistry, *, recorder=None):
    recorder = recorder or NullRecorder()
    cast = {actor: ParticipantConfig() for actor in scenario.world.character_ids}
    cast.update(scenario.cast)
    cast.update(config.cast)
    if set(cast) != set(scenario.world.character_ids):
        raise ValueError("cast includes unknown Characters")
    backends, result = {}, {}
    for actor, binding in cast.items():
        if binding.adapter == "scripted":
            result[actor] = ScriptedAgent(actor, [registry.parse_batch(raw) for raw in scenario.scripts.get(actor, ())], registry)
        elif binding.adapter == "human":
            result[actor] = HumanAgent(actor, HumanTranslator(registry))
        else:
            if binding.profile not in config.profiles:
                raise ValueError(f"{actor}: unknown profile {binding.profile}")
            profile = config.profiles[binding.profile]
            if profile.backend_id not in backends:
                backends[profile.backend_id] = build_backend(config.backends[profile.backend_id])
            result[actor] = LLMAgent(actor, LLMTranslator(registry, identity_for(scenario, actor)),
                                     backends[profile.backend_id], profile,
                                     on_exchange=lambda exchange: recorder.record("llm_exchanges", exchange))
    return result
