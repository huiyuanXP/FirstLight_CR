"""Run tools/policy_episode.py with decks/forms from a MatchConfig JSON file.
Usage: policy_episode_with_config.py CONFIG_JSON [policy_episode args...]"""
import json
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from native_runner import match_factory  # noqa: E402

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
config.pop("seed", None)
base = match_factory.MatchConfig
match_factory.MatchConfig = lambda seed, **kw: base(seed=seed, **{**config, **kw})
sys.argv = [str(root / "tools/policy_episode.py"), *sys.argv[2:]]
runpy.run_path(sys.argv[0], run_name="__main__")
