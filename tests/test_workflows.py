"""Every workflow file must load the way GitHub loads it: a duplicate key (like two `run:` in one step) makes GitHub
reject the whole file and email a "No jobs were run" failure. PyYAML alone would quietly keep the last key.
Run: python tests/test_workflows.py   (also runs on every push)."""
from pathlib import Path

import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


class _Strict(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep)


_Strict.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def test_workflows_have_no_duplicate_keys_and_every_step_runs_one_thing():
    assert WORKFLOWS, "no workflow files found"
    for f in WORKFLOWS:
        doc = yaml.load(f.read_text(encoding="utf-8"), Loader=_Strict)   # raises on a duplicate key
        for name, job in (doc.get("jobs") or {}).items():
            for i, step in enumerate(job.get("steps") or []):
                assert ("run" in step) != ("uses" in step), f"{f.name} job {name} step {i}: needs exactly one of run/uses"


if __name__ == "__main__":
    test_workflows_have_no_duplicate_keys_and_every_step_runs_one_thing()
    print(f"{len(WORKFLOWS)} workflow files load cleanly")
