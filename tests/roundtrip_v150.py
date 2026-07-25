"""Destructive v1.5 save/write/read checks; run only against a disposable world copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import SaveSession, TOP_PASSIVE_PRESETS


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: roundtrip_v150.py <disposable-Level.sav>")
    path = Path(sys.argv[1])
    preset = list(TOP_PASSIVE_PRESETS["神仙全能"])

    session = SaveSession(path)
    original_count = len(session.pals_for_player(0))
    changed = session.apply_passives_all(0, preset)
    assert changed == original_count
    sample_ids = [pal.instance_id.lower() for pal in session.pals_for_player(0)[:5]]
    session.save()

    verified = SaveSession(path)
    for instance_id in sample_ids:
        record = next(pal for pal in verified.pals if pal.instance_id.lower() == instance_id)
        assert verified.snapshot(record)["passives"] == preset

    name = verified.add_experimental_world_tree_dragon(0)
    assert name == "枯星龙"
    verified.save()

    final = SaveSession(path)
    dragons = [pal for pal in final.pals_for_player(0) if pal.code_name == "WorldTreeDragon"]
    assert len(dragons) == 1
    snapshot = final.snapshot(dragons[0])
    assert snapshot["name"] == "枯星龙"
    assert snapshot["level"] == 80
    assert snapshot["stars"] == 4
    assert snapshot["passives"] == list(TOP_PASSIVE_PRESETS["神仙战神"])
    print(
        "V150_ROUNDTRIP_OK "
        f"original_pals={original_count} batch_changed={changed} "
        f"experimental={snapshot['name']} level={snapshot['level']} stars={snapshot['stars']}"
    )


if __name__ == "__main__":
    main()
