"""Run one headless V4 policy-vs-policy episode and log every decision.

``--engine native`` drives a running offline engine (the same transport as
``offline_duel``). ``--engine mock`` replaces the engine with an in-memory
stand-in so the Python loop (reset -> FAIR observation -> mask/candidates ->
V4 policy -> decoder -> step -> reward/terminal) can be exercised on a plain
Linux CPU host without Android, KVM or the game APK. Mock dynamics are NOT the
game: only elixir and hand cycling are simulated, played cards spawn nothing,
towers never take damage, and the episode ends by time. Use it for interface and plumbing
checks, never as training or evaluation data.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch  # noqa: E402

from native_runner.battle_env import BattleEnvV1, _elixir_raw_per_tick  # noqa: E402
from native_runner.contracts import ActionKind, ActionV1  # noqa: E402
from native_runner.match_factory import MatchConfig  # noqa: E402
from native_runner.rich_telemetry_adapter import RuntimeEffectCatalog  # noqa: E402
from native_runner.training.v4.expert import FIRST_POLICY_DECISION_TICK, POLICY_DECISION_TICKS  # noqa: E402
from native_runner.training.v4.factory import production_semantic_bundle  # noqa: E402
from native_runner.training.v4.policy_session import build_policy_session_v4, load_policy_v4  # noqa: E402
from native_runner.training.replay_archive import capture_training_replay, save_training_replay  # noqa: E402

DEFAULT_CHECKPOINT = Path(__file__).resolve().parents[1] / "checkpoints" / "General"
MOCK_RULESET_ID = "0" * 64


def mock_native(card_catalog):
    """Build a deck-consistent in-memory engine on top of the test fake."""

    from native_runner.tests.test_battle_env import FakeNativeEnv, _card

    costs = {item.card_id: int(item.elixir_cost or 0) for item in card_catalog.specs}

    class MockNativeEnv(FakeNativeEnv):
        def __init__(self) -> None:
            super().__init__(initial_entity=False)
            self.end_tick = 0

        def _player(self, owner: int, deck: tuple[int, ...]) -> dict:
            hand = [_card(slot, deck[slot], costs[deck[slot]], deck_slot=slot) for slot in range(4)]
            return {
                "owner": owner,
                "accountId": 70_000_000 + owner,
                "elixirRaw": 50_000,
                "hand": hand,
                "nextCard": _card(4, deck[4], costs[deck[4]], deck_slot=4),
                "deck": [{"cardId": card_id} for card_id in deck],
                "cycle": [{"cardId": card_id} for card_id in deck[4:]],
                "_deck": list(deck),
            }

        def create_match(self, match: MatchConfig) -> dict:
            self.end_tick = int(match.end_tick)
            return super().create_match(match)

        def _apply_queued(self, old_tick: int, new_tick: int) -> None:
            remaining = []
            for record in self.queued:
                if not (old_tick < int(record["execute_tick"]) <= new_tick):
                    remaining.append(record)
                    continue
                action = record["action"]
                player = next(item for item in self.state["players"] if item["owner"] == action.owner)
                played = next(item for item in player["hand"] if int(item["handIndex"]) == action.hand_index)
                if player["elixirRaw"] < 10_000 * int(played["cost"]):
                    continue
                player["elixirRaw"] -= 10_000 * int(played["cost"])
                deck = player["_deck"]
                cycle = [item["cardId"] for item in player["cycle"]]
                incoming = cycle.pop(0)
                cycle.append(int(played["cardId"]))
                slot = deck.index(incoming)
                player["hand"] = [
                    _card(action.hand_index, incoming, costs[incoming], deck_slot=slot) if item is played else item
                    for item in player["hand"]
                ]
                player["cycle"] = [{"cardId": card_id} for card_id in cycle]
                player["nextCard"] = _card(4, cycle[0], costs[cycle[0]], deck_slot=deck.index(cycle[0]))
            self.queued = remaining
            self.state["queuedCommands"] = len(remaining)

        def step(self, ticks: int = 1) -> dict:
            old_tick = int(self.state["tick"])
            for player in self.state["players"]:
                generated = sum(_elixir_raw_per_tick(tick) for tick in range(old_tick, old_tick + int(ticks)))
                player["elixirRaw"] = min(100_000, int(player["elixirRaw"] + generated))
            observed = super().step(ticks)
            if int(self.state["tick"]) >= self.end_tick:
                self.state["ended"] = True
                observed = self.observe()
            return observed

    return MockNativeEnv()


def run_episode(args) -> dict:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    bundle = production_semantic_bundle()
    if args.engine == "mock":
        native = mock_native(bundle.native_card_catalog)
        environment_kwargs = {"ruleset_id": MOCK_RULESET_ID}
    else:
        from native_runner.cr_native_env import NativeClashEnv

        native = NativeClashEnv(args.host, args.port, timeout=30.0)
        native.public_card_play_events_from_combat_ring = True
        environment_kwargs = {}
    environment = BattleEnvV1(
        native=native,
        card_catalog=bundle.native_card_catalog,
        semantic_subset_contract=bundle.semantic_subset_contract,
        effect_catalog=RuntimeEffectCatalog.from_catalog(bundle.native_effect_catalog),
        **environment_kwargs,
    )
    policies = [load_policy_v4(path, device=args.device) for path in (args.checkpoint_0, args.checkpoint_1)]
    match = MatchConfig(seed=args.seed)
    observations, _ = environment.reset(
        match_config=match,
        options={
            "render_mode": "headless",
            "decision_ticks": POLICY_DECISION_TICKS,
            "event_driven_decisions": False,
            "allow_initial_remaining_runtime_rejections": True,
        },
    )
    initial_elixir = {
        owner: next(p.elixir_exact for p in observations[owner].players if p.owner == owner) for owner in (0, 1)
    }
    sessions = []
    for owner in (0, 1):
        session = build_policy_session_v4(
            environment, policies[owner].model, actor_owner=owner, device=args.device, sample=args.sample
        )
        session.start_episode(observations[owner], initial_elixir=initial_elixir)
        sessions.append(session)
    identity = {
        "engine": args.engine,
        "host": platform.platform(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "device": args.device,
        "sample": args.sample,
        "checkpoints": [
            {"path": str(p.checkpoint_path), "id": p.checkpoint_id, "sha256": p.checkpoint_sha256} for p in policies
        ],
        "match": asdict(match),
        "episode": environment.episode_config.to_dict(),
    }
    (output / "identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
    decisions = [0, 0]
    plays = [0, 0]
    rejects = [0, 0]
    returns = [0.0, 0.0]
    inference_ms = []
    terminated = truncated = False
    started = time.perf_counter()
    try:
        observation_log = (output / "observations.jsonl").open("w", encoding="utf-8") if args.dump_observations else None
        with (output / "steps.jsonl").open("w", encoding="utf-8") as log:
            while not (terminated or truncated):
                tick = observations[0].tick
                commands = {owner: (ActionV1.wait(owner, ticks=POLICY_DECISION_TICKS),) for owner in (0, 1)}
                record = {"tick": tick, "decisions": {}}
                for owner in (0, 1):
                    observation = observations[owner]
                    if observation_log is not None:
                        observation_log.write(json.dumps(observation.to_dict(), separators=(",", ":")) + "\n")
                    if observation.tick < FIRST_POLICY_DECISION_TICK:
                        sessions[owner].tensorizer.tensorize(observation, validate=False)
                        continue
                    decision = sessions[owner].decide(observation)
                    selected = tuple(decision.decoded.actions)
                    commands[owner] = selected or commands[owner]
                    decisions[owner] += 1
                    plays[owner] += sum(action.kind == ActionKind.PLAY_CARD for action in selected)
                    inference_ms.append(decision.inference_ms)
                    mask = observation.action_mask
                    record["decisions"][owner] = {
                        "legal_kinds": [kind for kind, legal in mask.kinds.items() if legal],
                        "legal_hand_slots": [slot for slot, legal in enumerate(mask.hand_slots) if legal],
                        "elixir": next(p.elixir_exact for p in observation.players if p.owner == owner),
                        "inference_ms": round(decision.inference_ms, 3),
                        "actions": [action.to_dict() for action in commands[owner]],
                    }
                observations, rewards, terms, truncs, _info = environment.step(commands, record_trace=True)
                terminated = bool(terms["__all__"])
                truncated = bool(truncs["__all__"])
                rejected = {
                    owner: [e.to_dict() for e in observations[owner].events if e.event_type == "action_rejected"]
                    for owner in (0, 1)
                }
                for owner in (0, 1):
                    rejects[owner] += len(rejected[owner])
                    returns[owner] += float(rewards[owner])
                record.update(
                    next_tick=observations[0].tick,
                    rewards={str(owner): rewards[owner] for owner in (0, 1)},
                    rejected={str(owner): rejected[owner] for owner in (0, 1) if rejected[owner]},
                    terminated=terminated,
                    truncated=truncated,
                )
                log.write(json.dumps(record, separators=(",", ":")) + "\n")
        if observation_log is not None:
            observation_log.close()
        replay = capture_training_replay(
            environment, completed_match_index=1, worker_id=0, global_slot=0, local_slot=0,
            final_observation=observations[0],
        )
        replay_path = save_training_replay(replay, output / "training_replays")
    finally:
        for session in sessions:
            session.end_episode()
        if args.engine == "native":
            native.close_transport()
    terminal = observations[0].terminal
    result = {
        "engine": args.engine,
        "seed": args.seed,
        "deck0": list(match.deck0),
        "deck1": list(match.deck1),
        "final_tick": observations[0].tick,
        "terminated": terminated,
        "truncated": truncated,
        "winner": terminal.winner if terminal is not None else None,
        "returns": returns,
        "decisions": decisions,
        "plays": plays,
        "rejects": rejects,
        "mean_inference_ms": sum(inference_ms) / max(1, len(inference_ms)),
        "wall_seconds": time.perf_counter() - started,
        "training_replay": str(replay_path),
    }
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=("mock", "native"), default="mock")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=26789)
    parser.add_argument("--checkpoint-0", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--checkpoint-1", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--sample", action="store_true", help="sample actions instead of deterministic decoding")
    parser.add_argument("--output", type=Path, default=Path("runs/policy-episode"))
    parser.add_argument("--dump-observations", action="store_true", help="also write every FAIR ObservationV1")
    args = parser.parse_args(argv)
    print(json.dumps(run_episode(args), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
