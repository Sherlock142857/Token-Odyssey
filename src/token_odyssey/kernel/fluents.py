"""Read-only spatial, capability and state predicates.

Knowing, seeing, touching and traversing are separate questions. In particular,
transparency never grants permission to reach through a closed container.
"""

import heapq

from token_odyssey.kernel.definitions import Character, Item, Passage, Predicate, Room
from token_odyssey.kernel.state import Placement, World


class Fluents:
    def __init__(self, world: World):
        self.world = world
        self.definition = world.definition
        self.state = world.state

    def same_room(self, first_id: str, second_id: str) -> bool:
        return self.world.room_of(first_id) == self.world.room_of(second_id)

    def controller(self, entity_id: str) -> str | None:
        for node in self.world.path(entity_id)[1:]:
            if isinstance(self.definition.entities[node], Character):
                return node
        return None

    def open(self, object_id: str) -> bool:
        # Containers without an opening mechanism, such as a tray, are open.
        return self.state.openings.get(object_id, True)

    def locked(self, object_id: str) -> bool:
        return self.state.locks.get(object_id, False)

    def closed_boundary(self, first_id: str, second_id: str) -> str | None:
        """Closed boundaries strictly below the lowest common ancestor matter.

        Two objects inside the same closed container share its interior; they
        are not separated by that container's outer wall.
        """
        first, second = self.world.path(first_id), self.world.path(second_id)
        common = next(node for node in first if node in second)
        for path in (first, second):
            for node in path:
                if node == common:
                    break
                edge = self.state.placements[node]
                if edge.parent_id != common and edge.relation == "inside" and not self.open(edge.parent_id):
                    return edge.parent_id
        return None

    def accessible(self, actor_id: str, object_id: str) -> bool:
        return self.access_problem(actor_id, object_id) is None

    def access_problem(self, actor_id: str, object_id: str) -> str | None:
        """Explain failure without revealing an unseen owner or container ID."""
        obj = self.definition.object(object_id)
        if isinstance(obj, Passage):
            room = self.world.room_of(actor_id)
            if room not in obj.rooms:
                return "NOT_ACCESSIBLE"
            return "CLOSED_CONTAINER_BLOCKS_ACCESS" if self.closed_boundary(actor_id, room) else None
        if not self.same_room(actor_id, object_id):
            return "NOT_ACCESSIBLE"
        if self.controller(object_id) not in {None, actor_id}:
            return "CONTROLLED_BY_OTHER"
        return "CLOSED_CONTAINER_BLOCKS_ACCESS" if self.closed_boundary(actor_id, object_id) else None

    def can_traverse(self, actor_id: str, passage_id: str, destination_room_id: str) -> bool:
        passage = self.definition.passages[passage_id]
        origin = self.world.room_of(actor_id)
        direction = (origin, destination_room_id)
        connects = (passage.forward_travel and direction == passage.rooms) or (
            passage.reverse_travel and direction == tuple(reversed(passage.rooms))
        )
        return connects and self.accessible(actor_id, passage_id) and self.open(passage_id)

    def satisfies(self, predicate: Predicate) -> bool:
        subject = predicate.subject_id
        if predicate.kind in {"inside", "attached"}:
            result = self.state.placements.get(subject) == Placement(
                parent_id=predicate.object_id, relation=predicate.kind
            )
        elif predicate.kind == "installed":
            result = self.state.connections.get(subject) == predicate.object_id
        elif predicate.kind == "open":
            result = self.open(subject)
        elif predicate.kind == "locked":
            result = self.locked(subject)
        else:
            result = self.state.flags[subject]
        return result == predicate.value

    def boundary_transmission(self, parent_id: str, channel: str) -> float:
        parent = self.definition.entities[parent_id]
        if isinstance(parent, Character):
            return parent.concealed_visibility if channel == "visual" else 1.0
        if isinstance(parent, Item):
            if parent.openable:
                if self.open(parent_id):
                    return parent.openable.open_visibility if channel == "visual" else 1.0
                return parent.openable.closed_visibility if channel == "visual" else parent.openable.closed_sound
            if parent.container and channel == "visual":
                return parent.container.visibility
        return 1.0

    def passage_transmission(self, passage: Passage, origin: str, channel: str) -> float:
        base = (passage.forward_visibility if origin == passage.rooms[0] else passage.reverse_visibility)
        if channel == "audio":
            base = passage.sound
        if passage.openable:
            if self.open(passage.id):
                base *= passage.openable.open_visibility if channel == "visual" else 1
            else:
                base *= passage.openable.closed_visibility if channel == "visual" else passage.openable.closed_sound
        return base

    def room_transmission(self, origin: str, destination: str, channel: str) -> float:
        """Max-product path over perception edges, independent of walkability."""
        best = {origin: 1.0}
        pending = [(-1.0, origin)]
        while pending:
            negative, room = heapq.heappop(pending)
            score = -negative
            if score < best.get(room, 0):
                continue
            if room == destination:
                return score
            for passage in self.definition.passages.values():
                if room not in passage.rooms:
                    continue
                neighbor = next(x for x in passage.rooms if x != room)
                candidate = score * self.passage_transmission(passage, room, channel)
                if candidate > best.get(neighbor, 0):
                    best[neighbor] = candidate
                    heapq.heappush(pending, (-candidate, neighbor))
        return 0.0

    def transmission(self, observer_id: str, target_id: str, channel: str = "visual") -> float:
        target = self.definition.object(target_id)
        observer_room = self.world.room_of(observer_id)
        if isinstance(target, Passage):
            # An adjacent door is observable from either endpoint. Looking at its
            # near face does not require looking through that door.
            scores = [self.room_transmission(observer_room, room, channel) for room in target.rooms]
            factor = self._path_transmission(self.world.path(observer_id), observer_room, channel)
            return max(scores) * factor
        target_room = self.world.room_of(target_id)
        first, second = self.world.path(observer_id), self.world.path(target_id)
        if observer_room == target_room:
            common = next(node for node in first if node in second)
            score = self._path_transmission(first, common, channel) * self._path_transmission(second, common, channel)
        else:
            score = (self._path_transmission(first, observer_room, channel)
                     * self.room_transmission(observer_room, target_room, channel)
                     * self._path_transmission(second, target_room, channel))
        if channel == "visual":
            room = self.definition.entities[target_room]
            assert isinstance(room, Room)
            score *= room.light
            if isinstance(target, Item):
                score *= target.visibility
        return max(0.0, min(1.0, score))

    def _path_transmission(self, path: tuple[str, ...], common: str, channel: str) -> float:
        factor = 1.0
        for node in path:
            if node == common:
                break
            edge = self.state.placements[node]
            if edge.parent_id != common and edge.relation == "inside":
                factor *= self.boundary_transmission(edge.parent_id, channel)
        return factor
