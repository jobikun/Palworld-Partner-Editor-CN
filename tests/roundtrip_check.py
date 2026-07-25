"""Run a destructive save/write/read check against a disposable world copy."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import SaveSession, TOP_PASSIVE_PRESETS


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: roundtrip_check.py <disposable-Level.sav>")
    path = Path(sys.argv[1])
    session = SaveSession(path)
    pal = session.pals_for_player(0)[0]
    pal_id = pal.instance_id.lower()
    snapshot = session.snapshot(pal)
    values = {
        "hp_iv": snapshot["hp_iv"],
        "melee_iv": snapshot["melee_iv"],
        "ranged_iv": snapshot["ranged_iv"],
        "defense_iv": snapshot["defense_iv"],
        "hp_soul": snapshot["hp_soul"],
        "attack_soul": snapshot["attack_soul"],
        "defense_soul": snapshot["defense_soul"],
        "craft_soul": snapshot["craft_soul"],
        "stars": snapshot["stars"],
        "condenser": snapshot["condenser"],
        "advanced": snapshot["overcap"],
        "suits": {key: item["total"] for key, item in snapshot["suits"].items()},
        "passives": list(TOP_PASSIVE_PRESETS["顶级战斗"]),
    }
    session.apply(pal, values)
    session.max_level(pal)
    session.save()

    verified = SaveSession(path)
    record = next(item for item in verified.pals if item.instance_id.lower() == pal_id)
    final = verified.snapshot(record)
    assert final["level"] == 80, final["level"]
    assert final["passives"] == list(TOP_PASSIVE_PRESETS["顶级战斗"]), final["passives"]
    print(
        f"ROUNDTRIP_OK players={len(session.players)} pals={len(session.pals)} "
        f"target={final['name']} level={final['level']} passives={final['passives']}"
    )


if __name__ == "__main__":
    main()
