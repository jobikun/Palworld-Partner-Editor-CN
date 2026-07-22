from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from palworld_save_tools.archive import (
    FArchiveReader,
    FArchiveWriter,
    UUID,
    instance_id_reader,
    instance_id_writer,
)
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import compress_gvas_to_sav, decompress_sav_to_gvas
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

from palworld_pal_edit import PalInfo
from palworld_pal_edit.EmptyObjectHandler import (
    EmptyGotWorkObject,
    EmptyRankObject,
    EmptySoulObject,
    EmptyTalentObject,
    EmptyWorkObject,
)


LOG = logging.getLogger("pal_partner_editor")
PalInfo.RecieveLogger(LOG)


SUITS: list[tuple[str, str]] = [
    ("EmitFlame", "生火"),
    ("Watering", "浇水"),
    ("Seeding", "播种"),
    ("GenerateElectricity", "发电"),
    ("Handcraft", "手工作业"),
    ("Collection", "采集"),
    ("Deforest", "伐木"),
    ("Mining", "采矿"),
    ("OilExtraction", "采油"),
    ("ProductMedicine", "制药"),
    ("Cool", "冷却"),
    ("Transport", "搬运"),
    ("MonsterFarm", "牧场"),
]

PALCALC_SUIT_KEYS = {
    "Kindling": "EmitFlame",
    "Watering": "Watering",
    "Planting": "Seeding",
    "GenerateElectricity": "GenerateElectricity",
    "Handiwork": "Handcraft",
    "Gathering": "Collection",
    "Lumbering": "Deforest",
    "Mining": "Mining",
    "MedicineProduction": "ProductMedicine",
    "Cooling": "Cool",
    "Transporting": "Transport",
    "Farming": "MonsterFarm",
}


def _resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def _load_pal_database() -> dict[str, dict[str, Any]]:
    path = _resource_path("palcalc_db.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EditorError(f"无法加载 Palworld 1.0 伙伴数据：{exc}") from exc
    result: dict[str, dict[str, Any]] = {}
    for pal in payload.get("Pals", []):
        code = pal.get("InternalName")
        if code:
            result[str(code)] = pal
    # 剧情随行形态沿用对应物种的名称和基础数据。
    if "KingWhale" in result:
        result["KingWhale_otomo"] = result["KingWhale"]
    return result


PAL_DATABASE: dict[str, dict[str, Any]] = {}


class EditorError(RuntimeError):
    pass


def _skip_decode(reader: FArchiveReader, type_name: str, size: int, path: str):
    if type_name == "ArrayProperty":
        return {
            "skip_type": type_name,
            "array_type": reader.fstring(),
            "id": reader.optional_guid(),
            "value": reader.read(size),
        }
    if type_name == "MapProperty":
        return {
            "skip_type": type_name,
            "key_type": reader.fstring(),
            "value_type": reader.fstring(),
            "id": reader.optional_guid(),
            "value": reader.read(size),
        }
    if type_name == "StructProperty":
        return {
            "skip_type": type_name,
            "struct_type": reader.fstring(),
            "struct_id": reader.guid(),
            "id": reader.optional_guid(),
            "value": reader.read(size),
        }
    raise EditorError(f"无法跳过未知属性 {type_name}: {path}")


def _skip_encode(writer: FArchiveWriter, property_type: str, properties: dict) -> int:
    # PalEdit 原实现会删除传入字典中的辅助键；这里复制后再处理，确保重复保存安全。
    props = dict(properties)
    if "skip_type" not in props:
        custom_type = props.get("custom_type")
        parent = PALWORLD_CUSTOM_PROPERTIES.get(custom_type)
        if parent:
            return parent[1](writer, property_type, props)
        return writer.property_inner(writer, property_type, props)

    props.pop("custom_type", None)
    props.pop("skip_type", None)
    if property_type == "ArrayProperty":
        writer.fstring(props["array_type"])
        writer.optional_guid(props.get("id"))
    elif property_type == "MapProperty":
        writer.fstring(props["key_type"])
        writer.fstring(props["value_type"])
        writer.optional_guid(props.get("id"))
    elif property_type == "StructProperty":
        writer.fstring(props["struct_type"])
        writer.guid(props["struct_id"])
        writer.optional_guid(props.get("id"))
    else:
        raise EditorError(f"无法写回未知属性 {property_type}")
    writer.write(props["value"])
    return len(props["value"])


def _group_prefix_decode(reader: FArchiveReader, type_name: str, size: int, path: str):
    """Decode only the stable group prefix and preserve every newer tail byte."""
    if type_name != "MapProperty":
        raise EditorError(f"公会数据类型异常: {type_name}")
    value = reader.property(type_name, size, path, nested_caller_path=path)
    for group in value["value"]:
        raw_values = group["value"]["RawData"]["value"]["values"]
        inner = reader.internal_copy(bytes(raw_values), debug=False)
        group["value"]["RawData"]["value"] = {
            "group_id": inner.guid(),
            "group_name": inner.fstring(),
            "individual_character_handle_ids": inner.tarray(instance_id_reader),
            "preserved_tail": inner.read_to_end(),
        }
    return value


def _group_prefix_encode(writer: FArchiveWriter, property_type: str, properties: dict) -> int:
    if property_type != "MapProperty":
        raise EditorError(f"公会数据类型异常: {property_type}")
    props = dict(properties)
    props.pop("custom_type", None)
    groups = copy.deepcopy(props["value"])
    for group in groups:
        raw = group["value"]["RawData"]["value"]
        if "values" in raw:
            continue
        inner = FArchiveWriter()
        inner.guid(raw["group_id"])
        inner.fstring(raw["group_name"])
        inner.tarray(instance_id_writer, raw["individual_character_handle_ids"])
        inner.write(raw["preserved_tail"])
        group["value"]["RawData"]["value"] = {"values": list(inner.bytes())}
    props["value"] = groups
    return writer.property_inner(property_type, props)


CUSTOM_PROPERTIES = copy.deepcopy(PALWORLD_CUSTOM_PROPERTIES)
for _path in (
    ".worldSaveData.MapObjectSaveData",
    ".worldSaveData.FoliageGridSaveDataMap",
    ".worldSaveData.MapObjectSpawnerInStageSaveData",
    ".worldSaveData.DynamicItemSaveData",
    ".worldSaveData.ItemContainerSaveData",
    # Palworld 1.0 added Progress_MultiType work records that older decoders
    # cannot interpret. Partner editing never needs this array, so preserve it
    # byte-for-byte instead of risking a lossy decode/re-encode cycle.
    ".worldSaveData.WorkSaveData",
):
    CUSTOM_PROPERTIES[_path] = (_skip_decode, _skip_encode)
CUSTOM_PROPERTIES[".worldSaveData.GroupSaveDataMap"] = (_group_prefix_decode, _group_prefix_encode)


@dataclass(frozen=True)
class SaveFingerprint:
    size: int
    mtime_ns: int
    sha256: str


@dataclass
class PlayerRecord:
    name: str
    guid: str
    travel_container: str
    storage_container: str
    group_id: str


@dataclass
class PalRecord:
    entity: Any
    instance_id: str
    owner_container: str

    @property
    def display_name(self) -> str:
        return self.entity.GetFullName()

    @property
    def code_name(self) -> str:
        return self.entity.GetCodeName()


class PartnerEntity:
    """Minimal, side-effect-free view of the partner fields we edit."""

    def __init__(self, data: dict):
        self._data = data
        self._obj = data["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
        if "IsPlayer" in self._obj:
            raise EditorError("PLAYER")

        self.owner = ""
        slot = self._obj.get("SlotId")
        if slot:
            self.owner = str(slot["value"]["ContainerId"]["value"]["ID"]["value"])

        raw_code = str(self._obj.get("CharacterID", {}).get("value", "Unknown"))
        self.is_boss = raw_code.lower().startswith("boss_")
        code = raw_code[5:] if self.is_boss else raw_code
        if code.lower() == "sheepball":
            code = "SheepBall"
        if code not in PalInfo.PalSpecies and raw_code in PalInfo.PalSpecies:
            code = raw_code
        self._code = code
        self._species = PAL_DATABASE.get(code)
        self._legacy_species = PalInfo.PalSpecies.get(code)

    def IsHuman(self) -> bool:
        return bool(self._legacy_species and self._legacy_species._human)

    def GetCodeName(self) -> str:
        return self._code

    def GetName(self) -> str:
        if not self._species:
            return self._code
        if self._code == "KingWhale_otomo":
            return "奥沧鲸"
        names = self._species.get("LocalizedNames") or {}
        return str(names.get("zh-Hans") or names.get("zh-CN") or self._species.get("Name") or self._code)

    def GetFullName(self) -> str:
        nickname = str(self._obj.get("NickName", {}).get("value", "")).strip()
        return f"{nickname}（{self.GetName()}）" if nickname else self.GetName()

    def GetLevel(self) -> int:
        return self._byte_value("Level", 1)

    def GetRank(self) -> int:
        return self._byte_value("Rank", 1)

    def GetStars(self) -> int:
        rank = self.GetRank()
        return min(max(rank - 1, 0), 4)

    def GetCondenserLevel(self) -> int:
        return min(max(self.GetRank() - 1, 0), 254)

    def GetRankWarning(self) -> int | None:
        rank = self.GetRank()
        return rank if rank < 1 else None

    def SetRank(self, value: int):
        self._set_byte("Rank", value, EmptyRankObject)

    def SetStars(self, value: int):
        self.SetRank(value + 1)

    def SetCondenserLevel(self, value: int):
        self.SetRank(value + 1)

    def GetTalentHP(self) -> int:
        return self._byte_value("Talent_HP", 0)

    def SetTalentHP(self, value: int):
        self._set_byte("Talent_HP", value, EmptyTalentObject)

    def GetAttackMelee(self) -> int:
        return self._byte_value("Talent_Melee", 0)

    def SetAttackMelee(self, value: int):
        self._set_byte("Talent_Melee", value, EmptyTalentObject)

    def GetAttackRanged(self) -> int:
        return self._byte_value("Talent_Shot", 0)

    def SetAttackRanged(self, value: int):
        self._set_byte("Talent_Shot", value, EmptyTalentObject)

    def GetTalentDefense(self) -> int:
        return self._byte_value("Talent_Defense", 0)

    def SetTalentDefense(self, value: int):
        self._set_byte("Talent_Defense", value, EmptyTalentObject)

    def GetRankHP(self) -> int:
        return self._byte_value("Rank_HP", 0)

    def SetRankHP(self, value: int):
        self._set_byte("Rank_HP", value, EmptySoulObject)

    def GetRankAttack(self) -> int:
        return self._byte_value("Rank_Attack", 0)

    def SetRankAttack(self, value: int):
        self._set_byte("Rank_Attack", value, EmptySoulObject)

    def GetRankDefense(self) -> int:
        return self._byte_value("Rank_Defence", 0)

    def SetRankDefense(self, value: int):
        self._set_byte("Rank_Defence", value, EmptySoulObject)

    def GetRankCraftSpeed(self) -> int:
        return self._byte_value("Rank_CraftSpeed", 0)

    def SetRankCraftSpeed(self, value: int):
        self._set_byte("Rank_CraftSpeed", value, EmptySoulObject)

    def GetSuit(self, suit: str) -> int:
        array = self._obj.get("GotWorkSuitabilityAddRankList")
        if not array:
            return 0
        for item in array["value"]["values"]:
            if item["WorkSuitability"]["value"]["value"] == f"EPalWorkSuitability::{suit}":
                return int(item["Rank"]["value"])
        return 0

    def SetSuit(self, suit: str, value: int):
        if value <= 0:
            container = self._obj.get("GotWorkSuitabilityAddRankList")
            if not container:
                return
            values = container["value"]["values"]
            container["value"]["values"] = [
                item
                for item in values
                if item["WorkSuitability"]["value"]["value"] != f"EPalWorkSuitability::{suit}"
            ]
            if not container["value"]["values"]:
                self._obj.pop("GotWorkSuitabilityAddRankList", None)
            return
        if "GotWorkSuitabilityAddRankList" not in self._obj:
            self._obj["GotWorkSuitabilityAddRankList"] = copy.deepcopy(EmptyGotWorkObject)
        array = self._obj["GotWorkSuitabilityAddRankList"]["value"]["values"]
        for item in array:
            if item["WorkSuitability"]["value"]["value"] == f"EPalWorkSuitability::{suit}":
                item["Rank"]["value"] = value
                return
        item = copy.deepcopy(EmptyWorkObject)
        item["WorkSuitability"]["value"]["value"] = f"EPalWorkSuitability::{suit}"
        item["Rank"]["value"] = value
        array.append(item)

    def base_suits(self) -> dict[str, int]:
        if not self._species:
            return {}
        source = self._species.get("WorkSuitability") or {}
        return {
            PALCALC_SUIT_KEYS[key]: int(value)
            for key, value in source.items()
            if key in PALCALC_SUIT_KEYS and int(value) > 0
        }

    def GetSuitTotal(self, suit: str) -> int:
        base = self.base_suits().get(suit, 0)
        if base <= 0:
            return 0
        condenser_bonus = 1 if self.GetRank() >= 5 and base < 5 else 0
        return base + condenser_bonus + self.GetSuit(suit)

    def SetSuitTotal(self, suit: str, total: int):
        base = self.base_suits().get(suit, 0)
        if base <= 0:
            raise EditorError(f"{suit} 不是该物种拥有的工作适应性")
        condenser_bonus = 1 if self.GetRank() >= 5 and base < 5 else 0
        natural = base + condenser_bonus
        if total < natural:
            total = natural
        self.SetSuit(suit, total - natural)

    def RemoveUnsupportedSuits(self):
        supported = set(self.base_suits())
        container = self._obj.get("GotWorkSuitabilityAddRankList")
        if not container:
            return
        values = container["value"]["values"]
        container["value"]["values"] = [
            item
            for item in values
            if item["WorkSuitability"]["value"]["value"].split("::")[-1] in supported
        ]
        if not container["value"]["values"]:
            self._obj.pop("GotWorkSuitabilityAddRankList", None)

    def CalculateIngameStats(self) -> dict[str, int | str]:
        if not self._species:
            return {"HP": "—", "PHY": "—", "MAG": "—", "DEF": "—", "WORK": "—"}
        level = self.GetLevel()
        hp_scale = int(self._species.get("Hp") or 0)
        attack_scale = int(self._species.get("Attack") or 0)
        defense_scale = int(self._species.get("Defense") or 0)
        craft_speed = int(self._species.get("CraftSpeed") or 100)
        if not hp_scale or not attack_scale or not defense_scale:
            return {"HP": "—", "PHY": "—", "MAG": "—", "DEF": "—", "WORK": "—"}
        hp = math.floor(500 + 5 * level + hp_scale * 0.5 * level * (1 + self.GetTalentHP() * 0.003))
        condenser = 1 + self.GetCondenserLevel() * 0.05
        hp_soul = min(max(self.GetRankHP(), 0), 255)
        attack_soul = min(max(self.GetRankAttack(), 0), 255)
        defense_soul = min(max(self.GetRankDefense(), 0), 255)
        craft_soul = min(max(self.GetRankCraftSpeed(), 0), 255)
        hp = math.floor(hp * (1 + hp_soul * 0.03) * condenser)
        phy = math.floor(100 + attack_scale * 0.075 * level * (1 + self.GetAttackMelee() * 0.003))
        phy = math.floor(phy * (1 + attack_soul * 0.03) * condenser)
        mag = math.floor(100 + attack_scale * 0.075 * level * (1 + self.GetAttackRanged() * 0.003))
        mag = math.floor(mag * (1 + attack_soul * 0.03) * condenser)
        defense = math.floor(50 + math.ceil(defense_scale * 0.075 * level) * (1 + self.GetTalentDefense() * 0.003))
        defense = math.floor(defense * (1 + defense_soul * 0.03) * condenser)
        work = math.floor(craft_speed * (1 + craft_soul * 0.03))
        return {"HP": hp, "PHY": phy, "MAG": mag, "DEF": defense, "WORK": work}

    def _byte_value(self, key: str, default: int) -> int:
        prop = self._obj.get(key)
        if not prop:
            return default
        value = prop.get("value", default)
        if isinstance(value, dict):
            value = value.get("value", default)
        return int(value)

    def _set_byte(self, key: str, value: int, template: dict):
        if key not in self._obj:
            self._obj[key] = copy.deepcopy(template)
        target = self._obj[key]["value"]
        if isinstance(target, dict):
            target["value"] = value
        else:
            self._obj[key]["value"] = value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> SaveFingerprint:
    stat = path.stat()
    return SaveFingerprint(stat.st_size, stat.st_mtime_ns, sha256_file(path))


def palworld_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FI", "IMAGENAME eq Palworld.exe", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "Palworld.exe" in result.stdout
    except OSError:
        return False


def find_world_saves() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    root = Path(local) / "Pal" / "Saved" / "SaveGames"
    if not root.exists():
        return []
    saves = [p for p in root.glob("*/*/Level.sav") if p.is_file()]
    return sorted(saves, key=lambda p: p.stat().st_mtime_ns, reverse=True)


def _load_gvas(path: Path, custom_properties=CUSTOM_PROPERTIES) -> GvasFile:
    raw_gvas, _ = decompress_sav_to_gvas(path.read_bytes())
    return GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, custom_properties)


class SaveSession:
    def __init__(self, level_path: Path):
        self.level_path = level_path.resolve()
        if self.level_path.name.lower() != "level.sav" or not self.level_path.is_file():
            raise EditorError("请选择有效的 Level.sav")

        PalInfo.LoadPals("zh-CN")
        global PAL_DATABASE
        if not PAL_DATABASE:
            PAL_DATABASE = _load_pal_database()
        self.original_fingerprint = fingerprint(self.level_path)
        raw_gvas, self.save_type = decompress_sav_to_gvas(self.level_path.read_bytes())
        self.gvas = GvasFile.read(raw_gvas, PALWORLD_TYPE_HINTS, CUSTOM_PROPERTIES)
        self.data = {"gvas_file": self.gvas, "properties": self.gvas.properties}
        self.players = self._load_players()
        self.pals, self.unknown_count = self._load_pals()
        self.dirty = False
        self.changed_ids: set[str] = set()
        self.expected_signatures: dict[str, dict[str, Any]] = {}
        self.added_species: dict[str, str] = {}

    def _load_players(self) -> list[PlayerRecord]:
        records: list[PlayerRecord] = []
        players = self._player_guid_map()
        groups = self._player_group_map()
        for name, guid in players.items():
            player_path = self.level_path.parent / "Players" / f"{str(guid).upper().replace('-', '')}.sav"
            if not player_path.is_file():
                LOG.warning("找不到玩家存档: %s", player_path)
                continue
            try:
                player = PalInfo.PalPlayerEntity(_load_gvas(player_path, PALWORLD_CUSTOM_PROPERTIES).dump())
                records.append(
                    PlayerRecord(
                        name=name,
                        guid=str(player.GetPlayerGuid()),
                        travel_container=str(player.GetTravelPalInventoryGuid()),
                        storage_container=str(player.GetPalStorageGuid()),
                        group_id=groups.get(str(guid).lower(), ""),
                    )
                )
            except Exception:
                LOG.exception("无法读取玩家存档 %s", player_path)
        if not records:
            raise EditorError("没有找到可读取的 Steam 玩家存档")
        return records

    def _player_group_map(self) -> dict[str, str]:
        source = self.data["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
        result: dict[str, str] = {}
        for item in source:
            raw = item["value"]["RawData"]["value"]
            value = raw["object"]["SaveParameter"]["value"]
            if "IsPlayer" in value:
                result[str(item["key"]["PlayerUId"]["value"]).lower()] = str(raw["group_id"])
        return result

    def _player_guid_map(self) -> dict[str, str]:
        source = self.data["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
        result: dict[str, str] = {}
        for item in source:
            value = item["value"]["RawData"]["value"]["object"]["SaveParameter"]["value"]
            if "IsPlayer" not in value:
                continue
            guid = str(item["key"]["PlayerUId"]["value"])
            name = str(value.get("NickName", {}).get("value", "玩家"))
            if guid.replace("0", "").replace("-", "") == "1":
                name += "（主机）"
            original = name
            suffix = 2
            while name in result:
                name = f"{original} #{suffix}"
                suffix += 1
            result[name] = guid
        return result

    def _load_pals(self) -> tuple[list[PalRecord], int]:
        source = self.data["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
        records: list[PalRecord] = []
        unknown = 0
        for item in source:
            try:
                entity = PartnerEntity(item)
                if entity.IsHuman():
                    continue
                instance = str(item["key"]["InstanceId"]["value"])
                records.append(PalRecord(entity, instance, str(entity.owner)))
            except Exception as exc:
                if str(exc) != "PLAYER":
                    unknown += 1
                    LOG.debug("跳过无法识别的伙伴条目", exc_info=True)
        return records, unknown

    def pals_for_player(self, index: int) -> list[PalRecord]:
        player = self.players[index]
        containers = {player.travel_container, player.storage_container}
        result = [pal for pal in self.pals if pal.owner_container in containers]
        return sorted(result, key=lambda pal: (pal.display_name, pal.instance_id))

    @staticmethod
    def snapshot(pal: PalRecord) -> dict[str, Any]:
        entity = pal.entity
        base_suits = entity.base_suits()
        raw_fields = {
            "hp_iv": entity.GetTalentHP(),
            "melee_iv": entity.GetAttackMelee(),
            "ranged_iv": entity.GetAttackRanged(),
            "defense_iv": entity.GetTalentDefense(),
            "hp_soul": entity.GetRankHP(),
            "attack_soul": entity.GetRankAttack(),
            "defense_soul": entity.GetRankDefense(),
            "craft_soul": entity.GetRankCraftSpeed(),
        }
        normal_limits = {
            "hp_iv": 100,
            "melee_iv": 100,
            "ranged_iv": 100,
            "defense_iv": 100,
            "hp_soul": 10,
            "attack_soul": 10,
            "defense_soul": 10,
            "craft_soul": 10,
        }
        overcap_fields = {key: value for key, value in raw_fields.items() if value > normal_limits[key]}
        try:
            stats = entity.CalculateIngameStats()
        except Exception:
            stats = {"HP": "—", "PHY": "—", "MAG": "—", "DEF": "—", "WORK": "—"}
        return {
            "name": pal.display_name,
            "code_name": pal.code_name,
            "level": entity.GetLevel(),
            "stars": entity.GetStars(),
            "condenser": entity.GetCondenserLevel(),
            "rank_warning": entity.GetRankWarning(),
            **{key: min(max(value, 0), 255) for key, value in raw_fields.items()},
            "field_warnings": {},
            "overcap_fields": overcap_fields,
            "overcap": entity.GetCondenserLevel() > 4 or bool(overcap_fields),
            "stats": stats,
            "suits": {
                key: {
                    "base": int(base_suits.get(key, 0)),
                    "extra": int(entity.GetSuit(key)),
                    "total": int(entity.GetSuitTotal(key)),
                    "supported": key in base_suits,
                }
                for key, _ in SUITS
            },
        }

    def apply(self, pal: PalRecord, values: dict[str, Any]) -> dict[str, Any]:
        entity = pal.entity
        advanced = bool(values.get("advanced"))
        iv_max = 255 if advanced else 100
        soul_max = 255 if advanced else 10
        entity.SetTalentHP(_bounded(values["hp_iv"], 0, iv_max, "生命 IV"))
        entity.SetAttackMelee(_bounded(values["melee_iv"], 0, iv_max, "近战攻击 IV"))
        entity.SetAttackRanged(_bounded(values["ranged_iv"], 0, iv_max, "远程攻击 IV"))
        entity.SetTalentDefense(_bounded(values["defense_iv"], 0, iv_max, "防御 IV"))
        entity.SetRankHP(_bounded(values["hp_soul"], 0, soul_max, "生命灵魂强化"))
        entity.SetRankAttack(_bounded(values["attack_soul"], 0, soul_max, "攻击灵魂强化"))
        entity.SetRankDefense(_bounded(values["defense_soul"], 0, soul_max, "防御灵魂强化"))
        entity.SetRankCraftSpeed(_bounded(values["craft_soul"], 0, soul_max, "工作速度灵魂强化"))
        if advanced:
            entity.SetCondenserLevel(_bounded(values["condenser"], 0, 254, "超限浓缩等级"))
        else:
            entity.SetStars(_bounded(values["stars"], 0, 4, "星级"))
        entity.RemoveUnsupportedSuits()
        for key in entity.base_suits():
            entity.SetSuitTotal(key, _bounded(values["suits"][key], 1, 10, f"{key} 等级"))
        self.dirty = True
        self.changed_ids.add(pal.instance_id)
        snapshot = self.snapshot(pal)
        self.expected_signatures[pal.instance_id.lower()] = self._signature(snapshot)
        return snapshot

    def apply_all_max(self, player_index: int) -> int:
        pals = self.pals_for_player(player_index)
        for pal in pals:
            snapshot = self.snapshot(pal)
            values = {
                "hp_iv": 255,
                "melee_iv": 255,
                "ranged_iv": 255,
                "defense_iv": 255,
                "hp_soul": 255,
                "attack_soul": 255,
                "defense_soul": 255,
                "craft_soul": 255,
                "stars": 4,
                "condenser": 254,
                "advanced": True,
                "suits": {
                    key: (10 if item["supported"] else 0)
                    for key, item in snapshot["suits"].items()
                },
            }
            self.apply(pal, values)
        return len(pals)

    def missing_obtainable_species(self, player_index: int) -> list[str]:
        owned = {pal.code_name for pal in self.pals_for_player(player_index)}
        # PalCalc's 1.0 database contains regular obtainable Pal species and
        # variants. Runtime-only follower aliases are added separately and are
        # deliberately not candidates here.
        return sorted(
            (
                code
                for code, data in PAL_DATABASE.items()
                if code != "KingWhale_otomo"
                and code not in owned
                and not code.lower().startswith(("boss_", "gym_", "raid_", "npc_", "test_"))
            ),
            key=lambda code: (int(PAL_DATABASE[code].get("InternalIndex") or 99999), code),
        )

    def add_all_missing_obtainable(self, player_index: int) -> list[str]:
        player = self.players[player_index]
        missing = self.missing_obtainable_species(player_index)
        if not missing:
            return []
        if not player.group_id:
            raise EditorError("找不到当前玩家的公会归属，无法安全新增伙伴")

        container = self._find_character_container(player.storage_container)
        capacity = int(container["value"]["SlotNum"]["value"])
        slots = container["value"]["Slots"]["value"]["values"]
        occupied = {int(slot["SlotIndex"]["value"]) for slot in slots}
        available = [idx for idx in range(capacity) if idx not in occupied]
        if len(available) < len(missing):
            raise EditorError(
                f"帕鲁终端空位不足：需要 {len(missing)} 格，当前只有 {len(available)} 格。"
                "请先扩容或清理终端。"
            )

        group = self._find_group(player.group_id)
        handles = group["value"]["RawData"]["value"]["individual_character_handle_ids"]
        template_pal = self.pals_for_player(player_index)
        if not template_pal:
            raise EditorError("当前玩家没有可用作 1.0 结构模板的伙伴，无法安全批量生成")
        pal_template = template_pal[0].entity._data
        slot_template = slots[0] if slots else None
        if slot_template is None:
            raise EditorError("帕鲁终端没有可用的槽位结构模板")

        entities = self.data["properties"]["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
        owner_uid = UUID.from_str(player.guid)
        group_uid = UUID.from_str(player.group_id)
        empty_uid = UUID.from_str("00000000-0000-0000-0000-000000000000")
        added_names: list[str] = []
        for code, slot_index in zip(missing, available):
            instance_id = UUID.from_str(str(uuid.uuid4()))
            new_slot = copy.deepcopy(slot_template)
            new_slot["SlotIndex"]["value"] = slot_index
            slot_raw = new_slot["RawData"]["value"]
            slot_raw["player_uid"] = empty_uid
            slot_raw["instance_id"] = instance_id
            slot_raw["permission_tribe_id"] = 0
            slots.append(new_slot)

            item = self._new_pal_from_template(
                pal_template, code, instance_id, owner_uid, group_uid,
                UUID.from_str(player.storage_container), slot_index,
            )
            entities.append(item)
            handles.append({"guid": empty_uid, "instance_id": instance_id})
            entity = PartnerEntity(item)
            record = PalRecord(entity, str(instance_id), player.storage_container)
            self.pals.append(record)
            self.added_species[str(instance_id).lower()] = code
            self.changed_ids.add(str(instance_id))
            added_names.append(entity.GetName())

        self.dirty = True
        return added_names

    def _find_character_container(self, container_id: str) -> dict:
        containers = self.data["properties"]["worldSaveData"]["value"]["CharacterContainerSaveData"]["value"]
        for item in containers:
            if str(item["key"]["ID"]["value"]).lower() == container_id.lower():
                return item
        raise EditorError(f"找不到帕鲁终端容器：{container_id}")

    def _find_group(self, group_id: str) -> dict:
        groups = self.data["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]
        for item in groups:
            raw = item["value"]["RawData"]["value"]
            if str(raw["group_id"]).lower() == group_id.lower():
                return item
        raise EditorError(f"找不到玩家公会：{group_id}")

    @staticmethod
    def _new_pal_from_template(template: dict, code: str, instance_id: UUID,
                               owner_uid: UUID, group_uid: UUID,
                               container_uid: UUID, slot_index: int) -> dict:
        item = copy.deepcopy(template)
        empty_uid = UUID.from_str("00000000-0000-0000-0000-000000000000")
        item["key"]["PlayerUId"]["value"] = empty_uid
        item["key"]["InstanceId"]["value"] = instance_id
        item["key"]["DebugName"]["value"] = ""
        raw = item["value"]["RawData"]["value"]
        raw["group_id"] = group_uid
        source = raw["object"]["SaveParameter"]["value"]
        keep = (
            "CharacterID", "Gender", "EquipWaza", "MasteredWaza", "Hp",
            "Talent_HP", "Talent_Shot", "Talent_Defense", "FullStomach",
            "PassiveSkillList", "OwnedTime", "OwnerPlayerUId",
            "OldOwnerPlayerUIds", "SlotId", "GotStatusPointList",
            "GotExStatusPointList", "LastNickNameModifierPlayerUid",
        )
        clean = {key: copy.deepcopy(source[key]) for key in keep if key in source}
        clean["CharacterID"]["value"] = code
        if "Gender" in clean:
            clean["Gender"]["value"]["value"] = "EPalGenderType::Female"
        for key in ("EquipWaza", "MasteredWaza", "PassiveSkillList"):
            if key in clean:
                clean[key]["value"]["values"] = []
        if "Hp" in clean:
            clean["Hp"]["value"]["Value"]["value"] = 100000
        for key in ("Talent_HP", "Talent_Shot", "Talent_Defense"):
            if key in clean:
                target = clean[key]["value"]
                if isinstance(target, dict):
                    target["value"] = 100
                else:
                    clean[key]["value"] = 100
        clean["OwnerPlayerUId"]["value"] = owner_uid
        clean["OldOwnerPlayerUIds"]["value"]["values"] = [owner_uid]
        clean["SlotId"]["value"]["ContainerId"]["value"]["ID"]["value"] = container_uid
        clean["SlotId"]["value"]["SlotIndex"]["value"] = slot_index
        if "LastNickNameModifierPlayerUid" in clean:
            clean["LastNickNameModifierPlayerUid"]["value"] = owner_uid
        raw["object"]["SaveParameter"]["value"] = clean
        return item

    def save(self) -> Path:
        if not self.dirty:
            raise EditorError("没有已应用的修改")
        if palworld_running():
            raise EditorError("检测到 Palworld 正在运行。请先正常退出游戏，再保存存档。")
        if fingerprint(self.level_path) != self.original_fingerprint:
            raise EditorError("Level.sav 在加载后发生了变化。请重新加载，避免覆盖新进度。")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = self.level_path.parent / "PalPartnerEditor_Backups" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(self.level_path, backup_dir / "Level.sav")
        meta = self.level_path.parent / "LevelMeta.sav"
        if meta.is_file():
            shutil.copy2(meta, backup_dir / "LevelMeta.sav")
        (backup_dir / "backup-info.json").write_text(
            json.dumps(
                {
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "source": str(self.level_path),
                    "source_sha256": self.original_fingerprint.sha256,
                    "changed_pal_ids": sorted(self.changed_ids),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # Preserve the exact compression/save type used by the source file.
        payload = compress_gvas_to_sav(self.gvas.write(CUSTOM_PROPERTIES), self.save_type)
        fd, temp_name = tempfile.mkstemp(prefix=".partner-editor-", suffix=".sav.tmp", dir=self.level_path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            verified = _load_gvas(temp_path)
            world = verified.properties.get("worldSaveData")
            if not world:
                raise EditorError("临时存档回读验证失败：缺少 worldSaveData")
            self._verify_targets(verified)
            os.replace(temp_path, self.level_path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        self.original_fingerprint = fingerprint(self.level_path)
        self.dirty = False
        self.changed_ids.clear()
        self.expected_signatures.clear()
        self.added_species.clear()
        return backup_dir

    @staticmethod
    def _signature(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
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
            "suits": {key: value["extra"] for key, value in snapshot["suits"].items()},
        }

    def _verify_targets(self, verified: GvasFile):
        source = verified.properties["worldSaveData"]["value"]["CharacterSaveParameterMap"]["value"]
        found: dict[str, dict[str, Any]] = {}
        for item in source:
            try:
                instance_id = str(item["key"]["InstanceId"]["value"]).lower()
                if instance_id not in self.expected_signatures:
                    continue
                entity = PartnerEntity(item)
                found[instance_id] = self._signature(self.snapshot(PalRecord(entity, instance_id, entity.owner)))
            except Exception as exc:
                raise EditorError(f"临时存档回读验证失败：{exc}") from exc
        if found != self.expected_signatures:
            missing = sorted(set(self.expected_signatures) - set(found))
            detail = f"，缺少伙伴 {', '.join(missing)}" if missing else "，字段回读结果不一致"
            raise EditorError("临时存档未通过目标字段验证" + detail)


        if self.added_species:
            added_found: dict[str, tuple[str, str]] = {}
            for item in source:
                instance_id = str(item["key"]["InstanceId"]["value"]).lower()
                if instance_id not in self.added_species:
                    continue
                entity = PartnerEntity(item)
                added_found[instance_id] = (entity.GetCodeName(), entity.owner.lower())
            expected = {
                instance_id: (code, self._owner_container_for_added(instance_id).lower())
                for instance_id, code in self.added_species.items()
            }
            if added_found != expected:
                raise EditorError("临时存档回读验证失败：新增伙伴实体或终端归属不一致")

            world = verified.properties["worldSaveData"]["value"]
            container_instances = {
                str(slot["RawData"]["value"]["instance_id"]).lower()
                for container in world["CharacterContainerSaveData"]["value"]
                for slot in container["value"]["Slots"]["value"]["values"]
                if slot["RawData"]["value"] is not None
            }
            group_instances = {
                str(handle["instance_id"]).lower()
                for group in world["GroupSaveDataMap"]["value"]
                for handle in group["value"]["RawData"]["value"]["individual_character_handle_ids"]
            }
            new_ids = set(self.added_species)
            if not new_ids <= container_instances or not new_ids <= group_instances:
                raise EditorError("临时存档回读验证失败：新增伙伴的终端槽位或公会成员记录缺失")

    def _owner_container_for_added(self, instance_id: str) -> str:
        for pal in self.pals:
            if pal.instance_id.lower() == instance_id:
                return pal.owner_container
        return ""


def _bounded(value: Any, minimum: int, maximum: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise EditorError(f"{label} 必须是整数") from exc
    if number < minimum or number > maximum:
        raise EditorError(f"{label} 必须在 {minimum}～{maximum} 之间")
    return number
