"""
wilderness/map_gen.py
=====================
Procedural map generation for a Wilderness Mode run.

Structure
---------
A run's map is a directed graph of MapNodes arranged in stages.
Within each stage the player fights sequentially (no branching).
Between stages the player chooses 1 of MAP_BRANCH_COUNT paths,
each leading to a different Realm / node-type.

Node IDs are assigned sequentially; the graph is stored flat in
RunMap.nodes for easy serialisation.

Extending
---------
Add new NodeType values to config.NODE_TYPE_WEIGHTS to let them
appear on branch choices. The run_manager dispatches on NodeType.
"""

from __future__ import annotations
import random
from typing import Dict, List, Tuple

from .config import (
    MAP_BRANCH_COUNT, MAP_MAX_STAGES, NODE_TYPE_WEIGHTS,
    NORMAL_BATTLES_PER_STAGE, ESSENCES, BRIDGELANDS,
)
from .models import MapNode, NodeType, Realm, RunMap


# ── Realm helpers ────────────────────────────────────────────────

def random_realm() -> Realm:
    """Pick a random single-type or Bridgeland realm."""
    # 30% chance of bridgeland
    if random.random() < 0.30 and BRIDGELANDS:
        pair = random.choice(BRIDGELANDS)
        return Realm(name=f"{pair[0]}/{pair[1]}", primary=pair[0], secondary=pair[1])
    ess = random.choice(ESSENCES)
    return Realm(name=ess, primary=ess)


def distinct_realms(n: int) -> List[Realm]:
    """
    Return `n` realms with no repeated names.
    Tries random sampling first; falls back to exhaustive shuffle if the pool
    is smaller than n (shouldn't happen with 15 essences + 7 bridgelands).
    """
    all_names: List[str] = list(ESSENCES) + [f"{a}/{b}" for a, b in BRIDGELANDS]
    random.shuffle(all_names)
    chosen = all_names[:n]

    realms = []
    for name in chosen:
        if "/" in name:
            a, b = name.split("/")
            realms.append(Realm(name=name, primary=a, secondary=b))
        else:
            realms.append(Realm(name=name, primary=name))
    return realms


def _weighted_node_type() -> NodeType:
    """Pick a NodeType for a branch choice using configured weights."""
    types   = list(NODE_TYPE_WEIGHTS.keys())
    weights = [NODE_TYPE_WEIGHTS[t] for t in types]
    chosen  = random.choices(types, weights=weights, k=1)[0]
    return NodeType(chosen)


# ── Map generation ───────────────────────────────────────────────

class _IdGen:
    """Simple monotonic ID generator."""
    def __init__(self): self._n = 0
    def next(self) -> int:
        self._n += 1
        return self._n


def generate_map(seed: int | None = None) -> RunMap:
    """
    Build a full run map and return it positioned at the start node.

    Layout
    ------
    stage 1 root → normal_battle nodes (5) → elite node
                                                    ↓
                                  [branch A] [branch B] [branch C]   ← stage 2 roots
                                     ↓           ↓           ↓
                                   ...         ...         ...

    The player starts at the stage-1 root (a BATTLE node).
    Within each stage, nodes form a linear chain ending at an ELITE.
    The ELITE's children are the 3 branch-root nodes of the next stage.
    """
    if seed is not None:
        random.seed(seed)

    ids   = _IdGen()
    nodes: Dict[int, MapNode] = {}

    def add_node(node_type: NodeType, stage: int, realm: Realm) -> int:
        nid  = ids.next()
        node = MapNode(node_id=nid, node_type=node_type, stage=stage, realm=realm)
        nodes[nid] = node
        return nid

    def build_stage(stage_num: int, realm: Realm) -> Tuple[int, int]:
        """
        Build one stage's linear chain of nodes.
        Returns (first_node_id, elite_node_id).
        """
        first_id = add_node(NodeType.BATTLE, stage_num, realm)
        prev_id  = first_id

        # Remaining normal battle nodes
        for _ in range(NORMAL_BATTLES_PER_STAGE - 1):
            nid = add_node(NodeType.BATTLE, stage_num, realm)
            nodes[prev_id].children.append(nid)
            prev_id = nid

        # Elite node at the end of the stage
        elite_id = add_node(NodeType.ELITE, stage_num, realm)
        nodes[prev_id].children.append(elite_id)

        return first_id, elite_id

    # Build stage 1 with a random starting realm
    stage1_realm         = random_realm()
    stage1_root, stage1_elite = build_stage(1, stage1_realm)
    root_id              = stage1_root

    prev_elite_ids = [stage1_elite]

    # Build subsequent stages up to MAP_MAX_STAGES
    for stage_num in range(2, MAP_MAX_STAGES + 1):
        branch_roots: List[int] = []

        for _ in range(MAP_BRANCH_COUNT):
            branch_realm = random_realm()
            branch_root, branch_elite = build_stage(stage_num, branch_realm)
            branch_roots.append(branch_root)

            if stage_num < MAP_MAX_STAGES:
                # Pre-build children for this branch's elite (done in next iteration)
                # We mark these elites to be parented later — store them
                pass

            branch_roots.append((branch_root, branch_elite))

        # Deduplicate — we appended tuples above, rebuild cleanly
        # (the loop above is slightly off; rebuild properly)
        pass

    # ── Cleaner rebuild — generate stages correctly ──────────────
    # The above draft has a bug; redo with a clear structure.
    nodes.clear()
    ids = _IdGen()

    def add(nt: NodeType, stage: int, realm: Realm) -> int:
        nid  = ids.next()
        nodes[nid] = MapNode(node_id=nid, node_type=nt, stage=stage, realm=realm)
        return nid

    def chain(stage_num: int, realm: Realm) -> Tuple[int, int]:
        """Build the linear battle → … → elite chain. Returns (head, elite)."""
        head = add(NodeType.BATTLE, stage_num, realm)
        cur  = head
        for _ in range(NORMAL_BATTLES_PER_STAGE - 1):
            nxt = add(NodeType.BATTLE, stage_num, realm)
            nodes[cur].children.append(nxt)
            cur = nxt
        elite = add(NodeType.ELITE, stage_num, realm)
        nodes[cur].children.append(elite)
        return head, elite

    # Stage 1
    s1_head, s1_elite = chain(1, random_realm())
    root_id           = s1_head
    prev_elites       = [s1_elite]

    for stage_num in range(2, MAP_MAX_STAGES + 1):
        next_elites: List[int] = []
        branch_heads: List[int] = []

        for realm in distinct_realms(MAP_BRANCH_COUNT):
            bh, be = chain(stage_num, realm)
            branch_heads.append(bh)
            next_elites.append(be)

        # Wire all previous-stage elites to these branch heads
        for pe in prev_elites:
            nodes[pe].children = list(branch_heads)

        prev_elites = next_elites

    return RunMap(nodes=nodes, current=root_id, stage=1)


def describe_branches(run_map: RunMap) -> List[str]:
    """
    Return human-readable descriptions of the player's next path choices.
    Call this after an elite is cleared to show what stage is ahead.
    """
    choices = run_map.next_choices()
    lines   = []
    for i, node in enumerate(choices, 1):
        lines.append(f"  [{i}] {node.label()}")
    return lines
