from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FeatureKind(str, Enum):
    TOGGLE = "toggle"
    INTEGER = "integer"
    FLOAT = "float"


@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    label: str
    hotkey: str
    group: str
    kind: FeatureKind = FeatureKind.TOGGLE
    default: int | float | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    increment: int | float | None = None

    @property
    def has_value(self) -> bool:
        return self.kind is not FeatureKind.TOGGLE


def integer(
    feature_id: str,
    label: str,
    hotkey: str,
    group: str,
    default: int,
    minimum: int = 0,
    maximum: int = 999_999_999,
) -> FeatureSpec:
    return FeatureSpec(
        feature_id,
        label,
        hotkey,
        group,
        FeatureKind.INTEGER,
        default,
        minimum,
        maximum,
        1,
    )


def floating(
    feature_id: str,
    label: str,
    hotkey: str,
    group: str,
    default: float,
    minimum: float = 0.01,
    maximum: float = 1000.0,
    increment: float = 0.1,
) -> FeatureSpec:
    return FeatureSpec(
        feature_id,
        label,
        hotkey,
        group,
        FeatureKind.FLOAT,
        default,
        minimum,
        maximum,
        increment,
    )


FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("god_mode", "无敌模式/无视伤害判定", "Num 1", "player_combat"),
    integer("lock_health", "锁定生命", "Num 2", "player_combat", 99_999, 1),
    FeatureSpec("infinite_shield", "无限护盾", "Num 3", "player_combat"),
    FeatureSpec("full_hunger", "满饱腹度", "Num 4", "player_combat"),
    FeatureSpec("infinite_stamina", "无限体力", "Num 5", "player_combat"),
    FeatureSpec("stealth_mode", "隐身模式", "Num 6", "player_combat"),
    FeatureSpec("normal_temperature", "保持正常体温", "Num 7", "player_combat"),
    FeatureSpec("capture_always", "100% 帕鲁捕捉率", "Num 8", "world"),
    FeatureSpec("rare_spawn_always", "100% 稀有帕鲁刷新率", "Num 9", "world"),
    FeatureSpec("drop_always", "100% 掉宝率", "Num 0", "world"),
    floating("drop_multiplier", "掉宝数量倍率", "Num .", "world", 2.0, 0.1, 1000.0),
    FeatureSpec("one_hit_kill", "超级伤害/一击必杀", "Num *", "player_combat"),
    floating("damage_multiplier", "伤害倍率", "Num +", "player_combat", 2.0, 0.1, 1000.0),
    floating("defense_multiplier", "防御倍率", "Num -", "player_combat", 2.0, 0.1, 1000.0),
    integer("edit_money", "编辑金钱", "Ctrl+Num 1", "inventory", 9_999_999, 0),
    integer("edit_item_amount", "编辑物品数量", "Ctrl+Num 2", "inventory", 99, 1),
    FeatureSpec("zero_weight", "负重清零", "Ctrl+Num 3", "inventory"),
    FeatureSpec("infinite_durability", "无限装备耐久度", "Ctrl+Num 4", "inventory"),
    FeatureSpec("no_spoil", "食物不会腐坏", "Ctrl+Num 5", "inventory"),
    FeatureSpec("free_craft", "制作无需材料", "Ctrl+Num 6", "building"),
    FeatureSpec("free_build", "建筑无需材料", "Ctrl+Num 7", "building"),
    FeatureSpec("instant_work", "快速制作与建造", "Ctrl+Num 8", "building"),
    FeatureSpec("freeze_time", "锁定时间", "Ctrl+Num 9", "time"),
    floating("time_speed", "时间流逝速度", "Ctrl+Num 0", "time", 0.5, 0.0, 100.0),
    floating("game_speed", "设置游戏速度", "Ctrl+Num .", "time", 2.5, 0.1, 10.0),
    integer("pal_lock_health", "帕鲁：锁定生命", "Alt+Num 1", "pal", 99_999, 1),
    FeatureSpec("pal_full_hunger", "帕鲁：满饱腹度", "Alt+Num 2", "pal"),
    FeatureSpec("pal_full_san", "帕鲁：满 SAN 值", "Alt+Num 3", "pal"),
    FeatureSpec("pal_infinite_stamina", "帕鲁：无限体力", "Alt+Num 4", "pal"),
    FeatureSpec("pal_instant_cooldown", "帕鲁：技能瞬间冷却", "Alt+Num 5", "pal"),
    FeatureSpec("infinite_ammo", "无限子弹/无需装弹", "Alt+Num 6", "weapons"),
    FeatureSpec("instant_weapon_cooldown", "武器瞬间冷却", "Alt+Num 7", "weapons"),
    FeatureSpec("unlimited_exp", "无限经验", "Alt+Num 8", "progression"),
    floating("exp_multiplier", "经验倍率", "Alt+Num 9", "progression", 2.0, 0.1, 1000.0),
    integer("edit_attribute_points", "编辑属性点", "Alt+Num 0", "progression", 99, 0),
    integer("edit_technology_points", "编辑科技点", "Alt+Num .", "progression", 99, 0),
    integer("edit_ancient_points", "编辑古代科技点", "Alt+Num +", "progression", 99, 0),
    floating("player_speed", "玩家动作速度倍率", "Alt+Num -", "movement", 2.0, 0.1, 100.0),
    floating("ai_speed", "设置 AI 速度", "Alt+Num /", "movement", 0.5, 0.0, 100.0),
    floating("movement_speed", "玩家移动速度倍率", "Alt+Num *", "movement", 2.0, 0.1, 100.0),
    floating("jump_height", "设置跳跃高度", "Alt+Insert", "movement", 2.0, 0.1, 100.0),
    FeatureSpec("infinite_jump", "无限连跳", "Alt+Delete", "movement"),
    integer("max_health", "编辑生命值上限", "Shift+F1", "player_attributes", 9_999, 1),
    integer("max_shield", "编辑护盾上限", "Shift+F2", "player_attributes", 9_999, 0),
    integer("max_hunger", "编辑饱腹度上限", "Shift+F3", "player_attributes", 9_999, 1),
    integer("max_stamina", "编辑体力上限", "Shift+F4", "player_attributes", 9_999, 1),
    integer("craft_speed", "编辑工作速度", "Shift+F5", "player_attributes", 9_999, 1),
    integer("max_weight", "编辑负重上限", "Shift+F6", "player_attributes", 9_999, 1),
)

FEATURE_BY_ID = {feature.feature_id: feature for feature in FEATURES}

if len(FEATURES) != 48:
    raise RuntimeError(f"实时功能契约应为 48 项，实际为 {len(FEATURES)} 项")
if len(FEATURE_BY_ID) != len(FEATURES):
    raise RuntimeError("实时功能 ID 存在重复")
