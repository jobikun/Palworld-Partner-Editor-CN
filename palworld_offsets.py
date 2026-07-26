from __future__ import annotations

import struct
from dataclasses import dataclass

from unreal_reflection import UnrealReflection


@dataclass(frozen=True)
class PalworldOffsets:
    fixed_point_value: int

    player_character_player_state: int
    player_character_parameter_component: int
    player_character_custom_time_dilation: int
    player_character_jump_max_count: int
    player_character_jump_current_count: int
    player_character_movement: int
    world_settings_time_dilation: int
    movement_jump_z_velocity: int
    movement_max_walk_speed: int
    movement_max_walk_speed_crouched: int
    movement_max_swim_speed: int
    movement_max_fly_speed: int
    movement_max_custom_speed: int

    parameter_individual_parameter: int
    parameter_sp: int

    individual_actor: int
    individual_save_parameter: int

    save_hp: int
    save_full_stomach: int
    save_is_player: int
    save_owner_player_uid: int
    save_max_hp: int
    save_craft_speed: int
    save_shield_hp: int
    save_shield_max_hp: int
    save_max_sp: int
    save_hunger_type: int
    save_sanity_value: int
    save_max_full_stomach: int
    save_unused_status_point: int

    player_state_inventory_data: int
    player_state_technology_data: int
    player_state_player_uid: int
    inventory_max_weight: int
    inventory_cached_max_weight: int
    inventory_now_weight: int
    inventory_info: int
    inventory_money_data: int
    money_value: int
    item_container_id: int
    item_container_slots: int
    item_slot_stack_count: int
    technology_points: int
    ancient_technology_points: int

    option_capture_rate: int
    option_player_damage_attack: int
    option_player_damage_defense: int
    option_collection_drop_rate: int
    option_enemy_drop_rate: int
    option_work_speed_rate: int
    option_is_multiplay: int
    recipe_material_counts: tuple[int, int, int, int, int]
    build_material_counts: tuple[int, int, int, int]

    @classmethod
    def resolve(cls, reflection: UnrealReflection) -> "PalworldOffsets":
        prop = reflection.property_offset
        return cls(
            fixed_point_value=prop("FixedPoint64", "Value"),
            player_character_player_state=prop("PalPlayerCharacter", "PlayerState"),
            player_character_parameter_component=prop(
                "PalPlayerCharacter",
                "CharacterParameterComponent",
            ),
            player_character_custom_time_dilation=prop(
                "PalPlayerCharacter",
                "CustomTimeDilation",
            ),
            player_character_jump_max_count=prop(
                "PalPlayerCharacter",
                "JumpMaxCount",
            ),
            player_character_jump_current_count=prop(
                "PalPlayerCharacter",
                "JumpCurrentCount",
            ),
            player_character_movement=prop(
                "PalPlayerCharacter",
                "CharacterMovement",
            ),
            world_settings_time_dilation=prop(
                "PalWorldSettings",
                "TimeDilation",
            ),
            movement_jump_z_velocity=prop(
                "CharacterMovementComponent",
                "JumpZVelocity",
            ),
            movement_max_walk_speed=prop(
                "CharacterMovementComponent",
                "MaxWalkSpeed",
            ),
            movement_max_walk_speed_crouched=prop(
                "CharacterMovementComponent",
                "MaxWalkSpeedCrouched",
            ),
            movement_max_swim_speed=prop(
                "CharacterMovementComponent",
                "MaxSwimSpeed",
            ),
            movement_max_fly_speed=prop(
                "CharacterMovementComponent",
                "MaxFlySpeed",
            ),
            movement_max_custom_speed=prop(
                "CharacterMovementComponent",
                "MaxCustomMovementSpeed",
            ),
            parameter_individual_parameter=prop(
                "PalCharacterParameterComponent",
                "IndividualParameter",
            ),
            parameter_sp=prop("PalCharacterParameterComponent", "SP"),
            individual_actor=prop(
                "PalIndividualCharacterParameter",
                "IndividualActor",
            ),
            individual_save_parameter=prop(
                "PalIndividualCharacterParameter",
                "SaveParameter",
            ),
            save_hp=prop("PalIndividualCharacterSaveParameter", "Hp"),
            save_full_stomach=prop(
                "PalIndividualCharacterSaveParameter",
                "FullStomach",
            ),
            save_is_player=prop(
                "PalIndividualCharacterSaveParameter",
                "IsPlayer",
            ),
            save_owner_player_uid=prop(
                "PalIndividualCharacterSaveParameter",
                "OwnerPlayerUId",
            ),
            save_max_hp=prop("PalIndividualCharacterSaveParameter", "MaxHP"),
            save_craft_speed=prop(
                "PalIndividualCharacterSaveParameter",
                "CraftSpeed",
            ),
            save_shield_hp=prop(
                "PalIndividualCharacterSaveParameter",
                "ShieldHP",
            ),
            save_shield_max_hp=prop(
                "PalIndividualCharacterSaveParameter",
                "ShieldMaxHP",
            ),
            save_max_sp=prop("PalIndividualCharacterSaveParameter", "MaxSP"),
            save_hunger_type=prop(
                "PalIndividualCharacterSaveParameter",
                "HungerType",
            ),
            save_sanity_value=prop(
                "PalIndividualCharacterSaveParameter",
                "SanityValue",
            ),
            save_max_full_stomach=prop(
                "PalIndividualCharacterSaveParameter",
                "MaxFullStomach",
            ),
            save_unused_status_point=prop(
                "PalIndividualCharacterSaveParameter",
                "UnusedStatusPoint",
            ),
            player_state_inventory_data=prop("PalPlayerState", "InventoryData"),
            player_state_technology_data=prop("PalPlayerState", "TechnologyData"),
            player_state_player_uid=prop("PalPlayerState", "PlayerUId"),
            inventory_max_weight=prop(
                "PalPlayerInventoryData",
                "MaxInventoryWeight",
            ),
            inventory_cached_max_weight=prop(
                "PalPlayerInventoryData",
                "MaxInventoryWeight_Cached",
            ),
            inventory_now_weight=prop(
                "PalPlayerInventoryData",
                "NowItemWeight",
            ),
            inventory_info=prop(
                "PalPlayerInventoryData",
                "MyInventoryInfo",
            ),
            inventory_money_data=prop(
                "PalPlayerInventoryData",
                "MyMoneyData",
            ),
            money_value=_resolve_money_value_offset(reflection),
            item_container_id=prop("PalItemContainer", "ID"),
            item_container_slots=prop(
                "PalItemContainer",
                "ItemSlotArray",
            ),
            item_slot_stack_count=prop("PalItemSlot", "StackCount"),
            technology_points=prop("PalTechnologyData", "TechnologyPoint"),
            ancient_technology_points=prop(
                "PalTechnologyData",
                "bossTechnologyPoint",
            ),
            option_capture_rate=prop("PalOptionWorldSettings", "PalCaptureRate"),
            option_player_damage_attack=prop(
                "PalOptionWorldSettings",
                "PlayerDamageRateAttack",
            ),
            option_player_damage_defense=prop(
                "PalOptionWorldSettings",
                "PlayerDamageRateDefense",
            ),
            option_collection_drop_rate=prop(
                "PalOptionWorldSettings",
                "CollectionDropRate",
            ),
            option_enemy_drop_rate=prop(
                "PalOptionWorldSettings",
                "EnemyDropItemRate",
            ),
            option_work_speed_rate=prop(
                "PalOptionWorldSettings",
                "WorkSpeedRate",
            ),
            option_is_multiplay=prop(
                "PalOptionWorldSettings",
                "bIsMultiplay",
            ),
            recipe_material_counts=tuple(
                prop("PalItemRecipe", f"Material{index}_Count")
                for index in range(1, 6)
            ),
            build_material_counts=tuple(
                prop("PalBuildObjectData", f"Material{index}_Count")
                for index in range(1, 5)
            ),
        )

    def as_dict(self) -> dict[str, int | tuple[int, ...]]:
        return dict(vars(self))


def _resolve_money_value_offset(reflection: UnrealReflection) -> int:


    process = reflection.process
    for obj in reflection.iter_objects():
        if obj.name != "GetNowMoney":
            continue
        try:
            if reflection.object_name(obj.class_address) != "Function":
                continue
            if reflection.object_name(obj.outer_address) != "PalMoneyData":
                continue
            wrapper = reflection.read_u64(obj.address + 0xD8)
            code = process.read(wrapper, 0x60)
        except Exception:
            continue
        for index, opcode in enumerate(code[:-5]):
            if opcode != 0xE8:
                continue
            displacement = struct.unpack_from("<i", code, index + 1)[0]
            target = wrapper + index + 5 + displacement
            candidate = process.read(target, 5)
            if (
                candidate[:3] == bytes.fromhex("48 8B 41")
                and candidate[4] == 0xC3
            ):
                return candidate[3]
    raise RuntimeError("无法从 PalMoneyData.GetNowMoney 解析当前金钱偏移")
