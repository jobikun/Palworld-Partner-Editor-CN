from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass, field

from memory_trainer import (
    AobPattern,
    GameFingerprint,
    MemoryPatch,
    MemoryReadError,
    PatchTransaction,
    ProcessMemory,
    TrainerError,
    relative_jump,
)
from palworld_offsets import PalworldOffsets
from trainer_features import FEATURE_BY_ID, FeatureKind
from unreal_reflection import (
    UOBJECT_CLASS_PRIVATE,
    USTRUCT_SUPER_STRUCT,
    UnrealReflection,
)


CURRENT_GAME_SHA256 = "2FF94A03BC777661BE100249B4940242F70661D890C6B8F8ACA4D6DCE79EE5A5"
CURRENT_GAME_SIZE = 161_348_096

PATCH_ONLY_FEATURES = frozenset(
    {
        "stealth_mode",
        "normal_temperature",
        "rare_spawn_always",
        "drop_always",
        "infinite_durability",
        "no_spoil",
        "free_craft",
        "free_build",
        "pal_instant_cooldown",
        "infinite_ammo",
        "instant_weapon_cooldown",
    }
)


@dataclass(frozen=True)
class PalTarget:
    actor: int
    component: int
    individual: int
    save: int


@dataclass(frozen=True)
class OverheatWeaponLayout:
    base_class: int
    meta_classes: tuple[int, ...]
    weapon_classes: tuple[int, ...]
    instances: tuple[int, ...]
    heat_value: int
    heat_per_shot: int
    cooling_speed: int
    is_overheated: int
    displayed_heat: int
    is_in_cool_time: int


@dataclass(frozen=True)
class LiveTargets:
    player: int
    player_component: int
    player_individual: int
    player_save: int
    player_movement: int
    player_state: int
    player_uid: bytes
    inventory: int
    money: int
    item_slots: tuple[int, ...]
    item_slot_class: int
    individual_class: int
    object_scan_index: int
    technology: int
    persistent_level: int
    world: int
    world_settings: int
    stealth_branch: int
    overheat_weapon: OverheatWeaponLayout | None
    pals: tuple[PalTarget, ...] = field(default_factory=tuple)
    all_pals: tuple[PalTarget, ...] = field(default_factory=tuple)


@dataclass
class FeatureState:
    enabled: bool
    value: int | float | None


def _filter_owned_pals(
    process: ProcessMemory,
    pals: list[PalTarget],
    owner_uid_offset: int,
    player_uid: bytes,
) -> list[PalTarget]:


    if len(player_uid) != 16 or not any(player_uid):
        return []
    result = []
    for target in pals:
        try:
            owner_uid = process.read(target.save + owner_uid_offset, 16)
        except MemoryReadError:
            continue
        if owner_uid == player_uid:
            result.append(target)
    return result


@dataclass(frozen=True)
class ExpHookControls:
    unlimited_flag: int
    multiplier: int
    patch: MemoryPatch


def _first_call_target_with_prefix(
    process: ProcessMemory,
    wrapper: int,
    prefix: bytes,
    *,
    search_size: int,
) -> int:


    if not wrapper:
        return 0
    module = process.module()
    module_end = module.base + module.size
    code = process.read(wrapper, search_size)
    for index in range(len(code) - 5):
        if code[index] != 0xE8:
            continue
        displacement = struct.unpack_from("<i", code, index + 1)[0]
        target = wrapper + index + 5 + displacement
        if not module.base <= target < module_end:
            continue
        try:
            if process.read(target, len(prefix)) == prefix:
                return target
        except MemoryReadError:
            continue
    return 0


def _is_build_material_copy_constructor(
    process: ProcessMemory,
    address: int,
) -> bool:
    try:
        return (
            process.read(address - 0x9A, 3)
            == bytes.fromhex("48 8D 05")
            and process.read(address - 0x93, 11)
            == bytes.fromhex(
                "48 89 01 48 8B 42 08 48 89 41 08"
            )
            and process.read(address - 0x90, 8)
            == bytes.fromhex("48 8B 42 08 48 89 41 08")
        )
    except MemoryReadError:
        return False


def _resolve_stealth_branch(
    process: ProcessMemory,
    sight_check_native: int,
    cone_check_native: int,
) -> int:


    if not sight_check_native or not cone_check_native:
        return 0
    module = process.module()
    module_end = module.base + module.size
    sight_code = process.read(sight_check_native, 0x240)
    matches: list[int] = []
    for index in range(len(sight_code) - 5):
        if sight_code[index] != 0xE8:
            continue
        displacement = struct.unpack_from("<i", sight_code, index + 1)[0]
        helper = sight_check_native + index + 5 + displacement
        if not module.base <= helper < module_end:
            continue
        helper_size = min(0x1000, module_end - helper)
        try:
            helper_code = process.read(helper, helper_size)
        except MemoryReadError:
            continue
        for call_index in range(len(helper_code) - 8):
            if helper_code[call_index] != 0xE8:
                continue
            call_displacement = struct.unpack_from(
                "<i",
                helper_code,
                call_index + 1,
            )[0]
            target = helper + call_index + 5 + call_displacement
            branch_index = call_index + 5
            if (
                target == cone_check_native
                and helper_code[branch_index : branch_index + 3]
                == bytes.fromhex("84 C0 74")
            ):
                matches.append(helper + branch_index)
    return matches[0] if len(set(matches)) == 1 else 0


class TargetLocator:
    def __init__(self, reflection: UnrealReflection, offsets: PalworldOffsets):
        self.reflection = reflection
        self.process = reflection.process
        self.offsets = offsets
        self._inheritance_cache: dict[tuple[int, int], bool] = {}

    def _is_child_of(self, class_address: int, parent_address: int) -> bool:
        key = (class_address, parent_address)
        cached = self._inheritance_cache.get(key)
        if cached is not None:
            return cached
        current = class_address
        seen = set()
        while current and current not in seen:
            if current == parent_address:
                self._inheritance_cache[key] = True
                return True
            seen.add(current)
            try:
                current = self.reflection.read_u64(current + USTRUCT_SUPER_STRUCT)
            except MemoryReadError:
                break
        self._inheritance_cache[key] = False
        return False

    def locate(self) -> LiveTargets:
        o = self.offsets
        character_class = self.reflection.find_class("PalCharacter")
        individual_class = self.reflection.find_class(
            "PalIndividualCharacterParameter"
        )
        item_container_class = self.reflection.find_class("PalItemContainer")
        item_slot_class = self.reflection.find_class("PalItemSlot")
        overheat_base_class = 0
        overheat_meta_classes: set[int] = set()
        overheat_weapon_classes: set[int] = set()
        overheat_weapon_instances: list[int] = []
        overheat_offsets: tuple[int, int, int, int, int, int] | None = None
        try:
            overheat_base_class = self.reflection.find_class(
                "BP_OverheatRifle_C"
            )
            overheat_meta_classes = {
                self.reflection.find_class("Class"),
                self.reflection.find_class("BlueprintGeneratedClass"),
            }
            overheat_offsets = (
                self.reflection.property_offset(
                    "BP_OverheatRifle_C",
                    "HeatValue",
                ),
                self.reflection.property_offset(
                    "BP_OverheatRifle_C",
                    "Const_HeatUpOneShot",
                ),
                self.reflection.property_offset(
                    "BP_OverheatRifle_C",
                    "Const_HeatDownSpeed",
                ),
                self.reflection.property_offset(
                    "BP_OverheatRifle_C",
                    "IsOverHeatMode",
                ),
                self.reflection.property_offset(
                    "BP_OverheatRifle_C",
                    "Heat Value",
                ),
                self.reflection.property_offset(
                    "BP_AssaultRifleBase_C",
                    "IsInCoolTime",
                ),
            )
        except TrainerError:
            overheat_base_class = 0
            overheat_meta_classes.clear()
            overheat_offsets = None
        object_scan_index = self.reflection.object_array_stats()[2]
        player = None
        live_pals: list[PalTarget] = []
        individual_addresses: list[int] = []
        item_containers: list[int] = []
        sight_check_wrapper = 0
        cone_check_wrapper = 0
        for obj in self.reflection.iter_objects():
            if obj.name.startswith("Default__"):
                continue
            if (
                overheat_base_class
                and obj.class_address in overheat_meta_classes
                and self._is_child_of(
                    obj.address,
                    overheat_base_class,
                )
            ):
                overheat_weapon_classes.add(obj.address)
            if obj.class_address in overheat_weapon_classes:
                overheat_weapon_instances.append(obj.address)
            if obj.class_address == individual_class:
                individual_addresses.append(obj.address)
            if obj.name == "SightCheckAllPlayer":
                try:
                    if self.reflection.object_name(obj.outer_address) == "PalAISensorComponent":
                        sight_check_wrapper = self.reflection.read_u64(
                            obj.address + 0xD8
                        )
                except (MemoryReadError, TrainerError):
                    pass
            elif obj.name == "InConeShapAndDitance_PreThreshold_Actor":
                try:
                    if self.reflection.object_name(obj.outer_address) == "PalUtility":
                        cone_check_wrapper = self.reflection.read_u64(
                            obj.address + 0xD8
                        )
                except (MemoryReadError, TrainerError):
                    pass
            if self._is_child_of(obj.class_address, item_container_class):
                item_containers.append(obj.address)
            if not self._is_child_of(obj.class_address, character_class):
                continue
            try:
                component = self.reflection.read_u64(
                    obj.address + o.player_character_parameter_component
                )
                if not component:
                    continue
                individual = self.reflection.read_u64(
                    component + o.parameter_individual_parameter
                )
                if not individual:
                    continue
                save = individual + o.individual_save_parameter
                is_player = self.process.read(save + o.save_is_player, 1)[0]
            except (MemoryReadError, TrainerError):
                continue
            target = PalTarget(
                actor=obj.address,
                component=component,
                individual=individual,
                save=save,
            )
            if is_player == 1:
                player = target
            else:
                live_pals.append(target)
        if player is None:
            raise TrainerError(
                "没有找到本地玩家。请先进入本地单人存档并站在可操作状态，再重新连接。"
            )
        player_state = self.reflection.read_u64(
            player.actor + o.player_character_player_state
        )
        player_uid = (
            self.process.read(player_state + o.player_state_player_uid, 16)
            if player_state
            else b"\0" * 16
        )
        live_by_individual = {
            target.individual: target
            for target in live_pals
        }
        pal_candidates: list[PalTarget] = []
        for individual in individual_addresses:
            save = individual + o.individual_save_parameter
            try:
                if self.process.read(save + o.save_is_player, 1) != b"\0":
                    continue
            except MemoryReadError:
                continue
            target = live_by_individual.get(individual)
            if target is None:
                target = PalTarget(
                    actor=0,
                    component=0,
                    individual=individual,
                    save=save,
                )
            pal_candidates.append(target)
        owned_pals = _filter_owned_pals(
            self.process,
            pal_candidates,
            o.save_owner_player_uid,
            player_uid,
        )
        player_movement = self.reflection.read_u64(
            player.actor + o.player_character_movement
        )
        inventory = (
            self.reflection.read_u64(player_state + o.player_state_inventory_data)
            if player_state
            else 0
        )
        money = (
            self.reflection.read_u64(inventory + o.inventory_money_data)
            if inventory
            else 0
        )
        item_slots: list[int] = []
        if inventory:
            common_container_id = self.process.read(
                inventory + o.inventory_info,
                16,
            )
            for container in item_containers:
                try:
                    container_id = self.process.read(
                        container + o.item_container_id,
                        16,
                    )
                    if container_id != common_container_id:
                        continue
                    array = self.process.read(
                        container + o.item_container_slots,
                        16,
                    )
                    data, count, maximum = struct.unpack("<Qii", array)
                    if not data or not (0 <= count <= maximum <= 4096):
                        continue
                    if count:
                        raw_slots = self.process.read(data, count * 8)
                        item_slots.extend(
                            struct.unpack(f"<{count}Q", raw_slots)
                        )
                except (MemoryReadError, struct.error):
                    continue
        technology = (
            self.reflection.read_u64(player_state + o.player_state_technology_data)
            if player_state
            else 0
        )
        persistent_level = self.reflection.read_u64(player.actor + 0x20)
        world = (
            self.reflection.read_u64(persistent_level + 0xB8)
            if persistent_level
            else 0
        )
        world_settings = (
            self.reflection.read_u64(persistent_level + 0x298)
            if persistent_level
            else 0
        )
        sight_check_native = _first_call_target_with_prefix(
            self.process,
            sight_check_wrapper,
            bytes.fromhex("48 89 5C 24 08 57 48 81"),
            search_size=0x120,
        )
        cone_check_native = _first_call_target_with_prefix(
            self.process,
            cone_check_wrapper,
            bytes.fromhex("48 8B C4 48 81 EC F8 00"),
            search_size=0x180,
        )
        stealth_branch = _resolve_stealth_branch(
            self.process,
            sight_check_native,
            cone_check_native,
        )
        overheat_weapon = None
        if overheat_base_class and overheat_offsets:
            overheat_weapon = OverheatWeaponLayout(
                base_class=overheat_base_class,
                meta_classes=tuple(overheat_meta_classes),
                weapon_classes=tuple(overheat_weapon_classes),
                instances=tuple(overheat_weapon_instances),
                heat_value=overheat_offsets[0],
                heat_per_shot=overheat_offsets[1],
                cooling_speed=overheat_offsets[2],
                is_overheated=overheat_offsets[3],
                displayed_heat=overheat_offsets[4],
                is_in_cool_time=overheat_offsets[5],
            )
        return LiveTargets(
            player=player.actor,
            player_component=player.component,
            player_individual=player.individual,
            player_save=player.save,
            player_movement=player_movement,
            player_state=player_state,
            player_uid=player_uid,
            inventory=inventory,
            money=money,
            item_slots=tuple(slot for slot in item_slots if slot),
            item_slot_class=item_slot_class,
            individual_class=individual_class,
            object_scan_index=object_scan_index,
            technology=technology,
            persistent_level=persistent_level,
            world=world,
            world_settings=world_settings,
            stealth_branch=stealth_branch,
            overheat_weapon=overheat_weapon,
            pals=tuple(owned_pals),
            all_pals=tuple(live_pals),
        )


class PointerCaptureHook:


    def __init__(
        self,
        process: ProcessMemory,
        transaction: PatchTransaction,
        *,
        name: str,
        hook_address: int,
        original_bytes: bytes,
    ):
        if len(original_bytes) < 5:
            raise ValueError("捕获钩子的原指令长度至少需要 5 字节")
        self.process = process
        self.transaction = transaction
        self.name = name
        self.hook_address = hook_address
        self.original_bytes = bytes(original_bytes)
        self.cave = transaction.allocate(0x1000, near=hook_address)
        self.pointer_address = self.cave + 0x200
        code = bytearray()
        code += b"\x52"
        code += b"\x48\xBA" + struct.pack("<Q", self.pointer_address)
        code += b"\x48\x89\x02"
        code += b"\x5A"
        code += self.original_bytes
        code += relative_jump(
            self.cave + len(code),
            hook_address + len(self.original_bytes),
        )
        process.write(self.cave, code, executable=True)
        process.write(self.pointer_address, b"\0" * 8)
        enabled = relative_jump(hook_address, self.cave)
        enabled += b"\x90" * (len(self.original_bytes) - len(enabled))
        self.patch = MemoryPatch(
            name=name,
            address=hook_address,
            enabled_bytes=enabled,
            expected_bytes=(self.original_bytes,),
        )
        transaction.apply(self.patch)

    def value(self) -> int:
        return struct.unpack("<Q", self.process.read(self.pointer_address, 8))[0]


class LiveTrainerSession:


    OPTION_CAPTURE_PATTERN = AobPattern.parse(
        "F3 0F 10 40 ?? EB ?? E8 ?? ?? ?? ?? "
        "F3 0F 10 40 ?? 4D 8D BE D0 00 00 00"
    )
    OPTION_CAPTURE_INSTRUCTION_OFFSET = 17
    OPTION_CAPTURE_ORIGINAL = bytes.fromhex("4D 8D BE D0 00 00 00")

    def __init__(self):
        self.process: ProcessMemory | None = None
        self.reflection: UnrealReflection | None = None
        self.offsets: PalworldOffsets | None = None
        self.targets: LiveTargets | None = None
        self.transaction: PatchTransaction | None = None
        self.option_hook: PointerCaptureHook | None = None
        self._option_pointer = 0
        self.states: dict[str, FeatureState] = {
            feature_id: FeatureState(False, spec.default)
            for feature_id, spec in FEATURE_BY_ID.items()
        }
        self._original_values: dict[tuple[str, int], bytes] = {}
        self._simple_patches: dict[str, list[MemoryPatch]] = {}
        self._exp_controls: ExpHookControls | None = None
        self._owned_pals: dict[int, PalTarget] = {}
        self._pending_pal_individuals: dict[int, int] = {}
        self._object_scan_index = 0
        self._next_pal_refresh = 0.0
        self._overheat_weapon_classes: set[int] = set()
        self._overheat_weapons: set[int] = set()
        self._class_inheritance_cache: dict[tuple[int, int], bool] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.last_error = ""
        self.last_tick = 0.0

    @property
    def connected(self) -> bool:
        return bool(self.process and self.process.is_alive() and self.targets)

    def connect(self):
        with self._lock:
            if self.process:
                self.disconnect()
            process = ProcessMemory.attach_palworld(writable=True)
            self.process = process
            try:
                module = process.module()
                fingerprint = GameFingerprint.from_path(module.path)
                if (
                    fingerprint.size != CURRENT_GAME_SIZE
                    or fingerprint.sha256 != CURRENT_GAME_SHA256
                ):
                    raise TrainerError(
                        "当前游戏程序不是已适配的 Steam 1.0 正式版。"
                        f"\n检测到：{fingerprint.sha256[:16]}… / {fingerprint.size} 字节"
                        "\n为避免崩溃，实时写入已拒绝；请先为这个游戏版本重新适配。"
                    )
                reflection = UnrealReflection(process)
                reflection.validate()
                offsets = PalworldOffsets.resolve(reflection)
                targets = TargetLocator(reflection, offsets).locate()
                transaction = PatchTransaction(process)
                self.transaction = transaction
                option_match = process.scan_unique(self.OPTION_CAPTURE_PATTERN)
                hook_address = (
                    option_match + self.OPTION_CAPTURE_INSTRUCTION_OFFSET
                )
                actual = process.read(
                    hook_address,
                    len(self.OPTION_CAPTURE_ORIGINAL),
                )
                if actual != self.OPTION_CAPTURE_ORIGINAL:
                    raise TrainerError(
                        "世界参数捕获点的原指令与当前适配数据不一致"
                    )
                option_hook = PointerCaptureHook(
                    process,
                    transaction,
                    name="世界参数指针捕获",
                    hook_address=hook_address,
                    original_bytes=actual,
                )
                self.reflection = reflection
                self.offsets = offsets
                self.targets = targets
                self._owned_pals = {
                    target.individual: target
                    for target in targets.pals
                }
                self._pending_pal_individuals.clear()
                self._object_scan_index = targets.object_scan_index
                self._next_pal_refresh = 0.0
                self._overheat_weapon_classes = set(
                    targets.overheat_weapon.weapon_classes
                    if targets.overheat_weapon
                    else ()
                )
                self._overheat_weapons = set(
                    targets.overheat_weapon.instances
                    if targets.overheat_weapon
                    else ()
                )
                self._class_inheritance_cache.clear()
                self.option_hook = option_hook
                option_pointer = self._wait_for_option_pointer()
                if process.read(
                    option_pointer + offsets.option_is_multiplay,
                    1,
                )[0]:
                    raise TrainerError(
                        "检测到当前世界为多人/服务器模式。"
                        "实时修改器仅允许本机单人存档，已拒绝写入。"
                    )


                option_hook.patch.disable(process)
                self._option_pointer = option_pointer
                self._prepare_simple_patches()
                self._install_exp_hook()
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._worker,
                    name="PalPartnerLiveTrainer",
                    daemon=True,
                )
                self._thread.start()
            except Exception:
                if self.transaction:
                    self.transaction.restore_all()
                process.close()
                self.process = None
                self.reflection = None
                self.offsets = None
                self.targets = None
                self.transaction = None
                self.option_hook = None
                self._option_pointer = 0
                self._owned_pals.clear()
                self._pending_pal_individuals.clear()
                self._object_scan_index = 0
                self._next_pal_refresh = 0.0
                self._overheat_weapon_classes.clear()
                self._overheat_weapons.clear()
                self._class_inheritance_cache.clear()
                raise

    def disconnect(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
        with self._lock:
            for state in self.states.values():
                state.enabled = False
            self._restore_original_values()
            restore_errors = []
            if self.transaction:
                restore_errors = self.transaction.restore_all()
            if self.process:
                self.process.close()
            self.process = None
            self.reflection = None
            self.offsets = None
            self.targets = None
            self.transaction = None
            self.option_hook = None
            self._option_pointer = 0
            self._owned_pals.clear()
            self._pending_pal_individuals.clear()
            self._object_scan_index = 0
            self._next_pal_refresh = 0.0
            self._overheat_weapon_classes.clear()
            self._overheat_weapons.clear()
            self._class_inheritance_cache.clear()
            self._exp_controls = None
            self._simple_patches.clear()
            if restore_errors:
                self.last_error = "；".join(restore_errors)

    def set_feature(
        self,
        feature_id: str,
        enabled: bool,
        value: int | float | None = None,
    ):
        spec = FEATURE_BY_ID[feature_id]
        if value is None:
            value = self.states[feature_id].value
        if spec.kind is not FeatureKind.TOGGLE:
            numeric = float(value)
            if spec.minimum is not None and numeric < spec.minimum:
                raise ValueError(f"{spec.label} 不能小于 {spec.minimum}")
            if spec.maximum is not None and numeric > spec.maximum:
                raise ValueError(f"{spec.label} 不能大于 {spec.maximum}")
            value = int(numeric) if spec.kind is FeatureKind.INTEGER else numeric
        with self._lock:
            if (
                enabled
                and feature_id in PATCH_ONLY_FEATURES
                and feature_id not in self._simple_patches
            ):
                raise TrainerError(
                    f"{spec.label}：当前游戏代码中没有找到已验证的补丁位置，"
                    "因此没有假装开启。请重新连接或重新适配此版本。"
                )
            if (
                enabled
                and feature_id in {"unlimited_exp", "exp_multiplier"}
                and not self._exp_controls
            ):
                raise TrainerError(
                    f"{spec.label}：当前游戏代码中没有找到已验证的经验处理位置。"
                )
            state = self.states[feature_id]
            was_enabled = state.enabled
            state.enabled = bool(enabled)
            state.value = value
            try:
                if feature_id in self._simple_patches and was_enabled != state.enabled:
                    self._toggle_simple_patch(feature_id, state.enabled)
                if (
                    feature_id in {"unlimited_exp", "exp_multiplier"}
                    and self._exp_controls
                    and was_enabled != state.enabled
                ):
                    patch = self._exp_controls.patch
                    if state.enabled:
                        assert self.transaction
                        self.transaction.apply(patch)
                    elif not any(
                        self.states[item].enabled
                        for item in {"unlimited_exp", "exp_multiplier"}
                    ):
                        patch.disable(self.process)
            except Exception:
                state.enabled = was_enabled
                raise
            if not state.enabled:
                self._restore_feature_originals(feature_id)

    def disable_all(self):
        for feature_id in self.states:
            self.set_feature(feature_id, False)

    def available_feature_ids(self) -> set[str]:
        available = set(FEATURE_BY_ID) - set(PATCH_ONLY_FEATURES)
        available.update(self._simple_patches)
        if not self._exp_controls:
            available.difference_update({"unlimited_exp", "exp_multiplier"})
        return available

    def status(self) -> dict[str, int | float | str | bool]:
        return {
            "connected": self.connected,
            "pid": self.process.pid if self.process else 0,
            "player": self.targets.player if self.targets else 0,
            "pals": len(self._owned_pals) if self.targets else 0,
            "options": self._option_pointer,
            "available": len(self.available_feature_ids()),
            "last_tick": self.last_tick,
            "last_error": self.last_error,
        }

    def _wait_for_option_pointer(self, timeout: float = 1.5) -> int:
        assert self.option_hook
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pointer = self.option_hook.value()
            if pointer:
                return pointer
            time.sleep(0.025)
        raise TrainerError(
            "没有捕获到当前世界参数。请确认已经进入本地单人世界，"
            "关闭背包/菜单并让游戏正常运行后重试。"
        )

    def _prepare_simple_patches(self):
        assert self.process and self.transaction and self.offsets
        definitions = {
            "no_spoil": (
                AobPattern.parse(
                    "F3 0F 58 8B ?? ?? 00 00 0F 2F ?? 72 ?? "
                    "0F 28 ?? F3 0F 5D"
                ),
                0,
                bytes.fromhex("0F 57 C9 0F 1F 44 00 00"),
                8,
            ),
            "infinite_ammo": (
                AobPattern.parse(
                    "8B ?? ?? 85 F6 7F ?? 32 C0 ?? 8B ?? 24 ?? 48"
                ),
                3,
                bytes.fromhex("31 F6"),
                2,
            ),
        }
        for feature_id, (pattern, offset, replacement, length) in definitions.items():
            matches = self.process.scan(pattern)
            if len(matches) != 1:
                continue
            address = matches[0] + offset
            original = self.process.read(address, length)
            self._simple_patches[feature_id] = [
                MemoryPatch(
                    name=FEATURE_BY_ID[feature_id].label,
                    address=address,
                    enabled_bytes=replacement,
                    expected_bytes=(original,),
                )
            ]

        self._register_cave_feature(
            "normal_temperature",
            AobPattern.parse(
                "39 87 ?? ?? 00 00 74 ?? ?? ?? ?? ?? ?? ?? ?? ?? "
                "?? ?? ?? ?? ?? 74 ?? ?? 8B ?? E8 ?? ?? ?? ?? 8B"
            ),
            instruction_length=6,
            build=lambda _cave, original: b"\x50\x31\xC0"
            + original
            + b"\x58",
        )
        self._register_cave_feature(
            "rare_spawn_always",
            AobPattern.parse(
                "F3 0F 10 B0 ?? ?? 00 00 F3 0F 59 ?? ?? ?? ?? ?? "
                "FF 15 ?? ?? ?? ?? 25 FF 7F 00 00"
            ),
            instruction_length=8,
            build=lambda _cave, _original: (
                b"\x50\xB8"
                + struct.pack("<f", 10000.0)
                + b"\x66\x0F\x6E\xF0\x58"
            ),
        )
        self._register_cave_feature(
            "infinite_durability",
            AobPattern.parse(
                "F3 0F 10 41 08 0F 2E C2 74 ?? F3 0F 11 ?? ?? "
                "F3 0F 11"
            ),
            instruction_length=5,
            build=lambda _cave, original: bytes.fromhex(
                "F3 0F 10 51 0C"
            )
            + original,
        )
        self._register_cave_feature(
            "instant_weapon_cooldown",
            AobPattern.parse(
                "F3 0F 11 89 ?? ?? 00 00 75 ?? 0F 57 ?? 0F 2F "
                "?? 77 ?? ?? ?? EB"
            ),
            instruction_length=8,
            build=lambda _cave, original: (
                b"\x50\xB8"
                + struct.pack("<f", 0.1)
                + b"\x66\x0F\x6E\xC8\x58"
                + original
            ),
        )
        self._register_cave_feature(
            "pal_instant_cooldown",
            AobPattern.parse(
                "F3 0F 59 73 ?? ?? 88 ?? ?? ?? 89 ?? ?? F3"
            ),
            instruction_length=5,
            build=lambda _cave, _original: (
                b"\x50\xB8"
                + struct.pack("<f", 0.01)
                + b"\x66\x0F\x6E\xF0\x58"
            ),
        )
        self._register_cave_feature(
            "pal_instant_cooldown",
            AobPattern.parse(
                "8B 06 48 8B CE 89 ?? 24 ?? E8 ?? ?? ?? ?? ?? ?? 74"
            ),
            instruction_length=5,
            build=lambda _cave, _original: bytes.fromhex(
                "9C 8B 06 83 F8 01 7E 06 C7 06 01 00 00 00 "
                "9D 8B 06 48 89 F1"
            ),
            append=True,
        )
        recipe_offsets = self.offsets.recipe_material_counts

        def free_craft_code(_cave: int, original: bytes) -> bytes:
            code = bytearray(b"\x52\x48\x85\xFF")
            code += b"\x74" + bytes((7 * len(recipe_offsets),))
            for offset in recipe_offsets:
                if not 0 <= offset <= 0x7F:
                    raise TrainerError("制作配方材料偏移超出短指令范围")
                code += b"\xC7\x47" + bytes((offset,)) + b"\0\0\0\0"
            code += b"\x5A" + original
            return bytes(code)

        self._register_cave_feature(
            "free_craft",
            AobPattern.parse(
                "88 0B 48 85 C0 0F 84 ?? ?? 00 00 ?? ?? ?? ?? ?? "
                "?? ?? ?? ?? ?? ?? ?? ?? 8B ?? ?? ?? 89 ?? ?? ?? "
                "8B ?? ?? 89 ?? ?? ?? F3 0F 10 47 14"
            ),
            instruction_length=5,
            build=free_craft_code,
        )
        self._register_build_material_hook()
        drop_matches = self.process.scan(
            AobPattern.parse(
                "66 0F 6E C0 0F 5B C0 F3 0F 59 05 ?? ?? ?? ?? "
                "0F 2F ?? 0F 87 ?? ?? 00 00 2B"
            )
        )
        if drop_matches:
            patches = []
            for address in drop_matches:
                original = self.process.read(address, 4)
                patches.append(
                    MemoryPatch(
                        name="100% 掉宝率",
                        address=address,
                        enabled_bytes=bytes.fromhex("0F 57 C0 90"),
                        expected_bytes=(original,),
                    )
                )
            self._simple_patches["drop_always"] = patches

        if self.targets and self.targets.stealth_branch:
            address = self.targets.stealth_branch
            original = self.process.read(address, 3)
            if original == bytes.fromhex("84 C0 74"):
                self._simple_patches["stealth_mode"] = [
                    MemoryPatch(
                        name=FEATURE_BY_ID["stealth_mode"].label,
                        address=address,
                        enabled_bytes=bytes.fromhex("84 C0 EB"),
                        expected_bytes=(original,),
                    )
                ]

    def _register_cave_feature(
        self,
        feature_id: str,
        pattern: AobPattern,
        *,
        instruction_length: int,
        build,
        append: bool = False,
    ):
        assert self.process and self.transaction
        matches = self.process.scan(pattern)
        if len(matches) != 1:
            return
        address = matches[0]
        original = self.process.read(address, instruction_length)
        cave = self.transaction.allocate(0x400, near=address)
        body = bytearray(build(cave, original))
        body += relative_jump(
            cave + len(body),
            address + instruction_length,
        )
        self.process.write(cave, body, executable=True)
        replacement = relative_jump(address, cave)
        replacement += b"\x90" * (instruction_length - len(replacement))
        patch = MemoryPatch(
            name=FEATURE_BY_ID[feature_id].label,
            address=address,
            enabled_bytes=replacement,
            expected_bytes=(original,),
        )
        if append:
            self._simple_patches.setdefault(feature_id, []).append(patch)
        else:
            self._simple_patches[feature_id] = [patch]

    def _register_build_material_hook(self):
        assert self.process and self.transaction and self.offsets
        matches = self.process.scan(
            AobPattern.parse(
                "0F B6 42 74 88 41 74 8B ?? 78 89 ?? 78 "
                "0F B6 42 7C 88 41 7C"
            )
        )
        candidates = [
            address
            for address in matches
            if _is_build_material_copy_constructor(
                self.process,
                address,
            )
        ]
        if len(candidates) != 1:
            return
        address = candidates[0]
        original = self.process.read(address, 7)
        if original != bytes.fromhex("0F B6 42 74 88 41 74"):
            return
        cave = self.transaction.allocate(0x400, near=address)
        body = bytearray()
        for offset in self.offsets.build_material_counts:
            if not 0 <= offset <= 0x7F:
                return
            body += b"\xC7\x41" + bytes((offset,)) + b"\0\0\0\0"
        body += original
        body += relative_jump(cave + len(body), address + len(original))
        self.process.write(cave, body, executable=True)
        replacement = relative_jump(address, cave) + b"\x90\x90"
        self._simple_patches["free_build"] = [
            MemoryPatch(
                name=FEATURE_BY_ID["free_build"].label,
                address=address,
                enabled_bytes=replacement,
                expected_bytes=(original,),
            )
        ]

    def _install_exp_hook(self):
        assert self.process and self.transaction
        pattern = AobPattern.parse(
            "F3 0F 5A C6 F2 0F 59 C8 F2 ?? 0F 2C"
        )
        matches = self.process.scan(pattern)
        if len(matches) != 1:
            return
        address = matches[0]
        original = self.process.read(address, 8)
        cave = self.transaction.allocate(0x400, near=address)
        unlimited = cave + 0x200
        multiplier = cave + 0x208
        maximum = cave + 0x210
        code = bytearray(b"\x9C")
        code += bytes.fromhex("F3 0F 5A C6")

        def rip(opcode: bytes, target: int, *, trailing: int = 0):
            code.extend(opcode)
            next_address = cave + len(code) + 4 + trailing
            code.extend(struct.pack("<i", target - next_address))

        rip(bytes.fromhex("83 3D"), unlimited, trailing=1)
        code += b"\0"
        jne_at = len(code)
        code += bytes.fromhex("0F 85 00 00 00 00")
        rip(bytes.fromhex("F2 0F 59 0D"), multiplier)
        jmp_at = len(code)
        code += bytes.fromhex("E9 00 00 00 00")
        unlimited_label = len(code)
        rip(bytes.fromhex("F2 0F 10 0D"), maximum)
        done_label = len(code)
        code += bytes.fromhex("F2 0F 59 C8")
        struct.pack_into(
            "<i",
            code,
            jne_at + 2,
            (cave + unlimited_label) - (cave + jne_at + 6),
        )
        struct.pack_into(
            "<i",
            code,
            jmp_at + 1,
            (cave + done_label) - (cave + jmp_at + 5),
        )
        code += b"\x9D"
        code += relative_jump(cave + len(code), address + len(original))
        self.process.write(cave, code, executable=True)
        self.process.write(unlimited, struct.pack("<i", 0))
        self.process.write(multiplier, struct.pack("<d", 1.0))
        self.process.write(maximum, struct.pack("<d", 19_999_999.0))
        replacement = relative_jump(address, cave) + b"\x90" * 3
        patch = MemoryPatch(
            name="经验控制",
            address=address,
            enabled_bytes=replacement,
            expected_bytes=(original,),
        )
        self._exp_controls = ExpHookControls(
            unlimited_flag=unlimited,
            multiplier=multiplier,
            patch=patch,
        )

    def _toggle_simple_patch(self, feature_id: str, enabled: bool):
        assert self.process
        patches = self._simple_patches.get(feature_id, ())
        changed: list[MemoryPatch] = []
        try:
            for patch in patches:
                if enabled:
                    assert self.transaction
                    self.transaction.apply(patch)
                else:
                    patch.disable(self.process)
                changed.append(patch)
        except Exception:
            for patch in reversed(changed):
                try:
                    if enabled:
                        patch.disable(self.process)
                    else:
                        assert self.transaction
                        self.transaction.apply(patch)
                except Exception:
                    pass
            raise

    def _player_target_is_current(self) -> bool:
        if not self.process or not self.offsets or not self.targets:
            return False
        p, o, t = self.process, self.offsets, self.targets
        try:
            if (
                p.read_u64(t.player + o.player_character_parameter_component)
                != t.player_component
            ):
                return False
            if (
                p.read_u64(t.player + o.player_character_player_state)
                != t.player_state
            ):
                return False
            if (
                p.read_u64(
                    t.player_component + o.parameter_individual_parameter
                )
                != t.player_individual
            ):
                return False
            if p.read(t.player_save + o.save_is_player, 1) != b"\x01":
                return False
            if t.inventory and (
                p.read_u64(t.player_state + o.player_state_inventory_data)
                != t.inventory
            ):
                return False
            return True
        except TrainerError:
            return False

    def _pal_individual_is_current(
        self,
        target: PalTarget,
        *,
        require_owner: bool,
    ) -> bool:
        if not self.process or not self.offsets or not self.targets:
            return False
        p, o, t = self.process, self.offsets, self.targets
        try:
            if (
                p.read_u64(target.individual + UOBJECT_CLASS_PRIVATE)
                != t.individual_class
            ):
                return False
            if target.save != target.individual + o.individual_save_parameter:
                return False
            if p.read(target.save + o.save_is_player, 1) != b"\0":
                return False
            if require_owner and (
                p.read(target.save + o.save_owner_player_uid, 16)
                != t.player_uid
            ):
                return False
            return True
        except TrainerError:
            return False

    def _live_pal_target_is_current(
        self,
        target: PalTarget,
        *,
        require_owner: bool,
    ) -> bool:
        if not self.process or not self.offsets:
            return False
        if not target.actor or not target.component:
            return False
        if not self._pal_individual_is_current(
            target,
            require_owner=require_owner,
        ):
            return False
        p, o = self.process, self.offsets
        try:
            if (
                p.read_u64(
                    target.actor + o.player_character_parameter_component
                )
                != target.component
            ):
                return False
            if (
                p.read_u64(
                    target.component + o.parameter_individual_parameter
                )
                != target.individual
            ):
                return False
            return True
        except TrainerError:
            return False

    def _owned_pal_target(self, individual: int) -> PalTarget | None:
        if not self.process or not self.offsets or not self.targets:
            return None
        p, o, t = self.process, self.offsets, self.targets
        save = individual + o.individual_save_parameter
        target = PalTarget(
            actor=0,
            component=0,
            individual=individual,
            save=save,
        )
        if not self._pal_individual_is_current(
            target,
            require_owner=True,
        ):
            return None
        try:
            actor = p.read_u64(individual + o.individual_actor)
            component = (
                p.read_u64(actor + o.player_character_parameter_component)
                if actor
                else 0
            )
            if component and (
                p.read_u64(component + o.parameter_individual_parameter)
                == individual
            ):
                return PalTarget(
                    actor=actor,
                    component=component,
                    individual=individual,
                    save=save,
                )
        except TrainerError:
            pass
        return target

    def _class_is_child_of(
        self,
        class_address: int,
        parent_address: int,
    ) -> bool:
        if not self.reflection:
            return False
        key = (class_address, parent_address)
        cached = self._class_inheritance_cache.get(key)
        if cached is not None:
            return cached
        current = class_address
        seen = set()
        while current and current not in seen:
            if current == parent_address:
                self._class_inheritance_cache[key] = True
                return True
            seen.add(current)
            try:
                current = self.reflection.read_u64(
                    current + USTRUCT_SUPER_STRUCT
                )
            except TrainerError:
                break
        self._class_inheritance_cache[key] = False
        return False

    def _refresh_dynamic_targets(self):
        if not self.process or not self.reflection or not self.targets:
            return
        now = time.monotonic()
        if now < self._next_pal_refresh:
            return
        self._next_pal_refresh = now + 1.0
        try:
            num_elements = self.reflection.object_array_stats()[2]
            if num_elements < self._object_scan_index:
                self._object_scan_index = num_elements
                self._pending_pal_individuals.clear()
                return
            for obj in self.reflection.iter_objects(
                start=self._object_scan_index,
            ):
                if obj.class_address == self.targets.individual_class:
                    self._pending_pal_individuals.setdefault(
                        obj.address,
                        0,
                    )
                layout = self.targets.overheat_weapon
                if layout and (
                    obj.class_address in layout.meta_classes
                    and self._class_is_child_of(
                        obj.address,
                        layout.base_class,
                    )
                ):
                    self._overheat_weapon_classes.add(obj.address)
                if (
                    layout
                    and obj.class_address in self._overheat_weapon_classes
                    and not obj.name.startswith("Default__")
                ):
                    self._overheat_weapons.add(obj.address)
            self._object_scan_index = num_elements
            for individual, target in tuple(self._owned_pals.items()):
                if not self._pal_individual_is_current(
                    target,
                    require_owner=True,
                ):
                    self._owned_pals.pop(individual, None)
            for individual in tuple(self._pending_pal_individuals):
                target = self._owned_pal_target(individual)
                if target is not None:
                    self._owned_pals[individual] = target
                    self._pending_pal_individuals.pop(individual, None)
                    continue
                attempts = self._pending_pal_individuals[individual] + 1
                if attempts >= 10:
                    self._pending_pal_individuals.pop(individual, None)
                else:
                    self._pending_pal_individuals[individual] = attempts
        except TrainerError:
            return

    def _live_component_for_pal(self, target: PalTarget) -> int:
        if not self.process or not self.offsets:
            return 0
        p, o = self.process, self.offsets
        try:
            actor = p.read_u64(target.individual + o.individual_actor)
            if not actor:
                return 0
            component = p.read_u64(
                actor + o.player_character_parameter_component
            )
            if not component:
                return 0
            if (
                p.read_u64(component + o.parameter_individual_parameter)
                != target.individual
            ):
                return 0
            return component
        except TrainerError:
            return 0

    def _overheat_weapon_is_current(self, weapon: int) -> bool:
        if not self.process:
            return False
        try:
            return (
                self.process.read_u64(weapon + UOBJECT_CLASS_PRIVATE)
                in self._overheat_weapon_classes
            )
        except TrainerError:
            return False

    def _item_slot_is_current(self, slot: int) -> bool:
        if not self.process or not self.targets:
            return False
        try:
            return (
                self.process.read_u64(slot + UOBJECT_CLASS_PRIVATE)
                == self.targets.item_slot_class
            )
        except TrainerError:
            return False

    def _worker(self):
        while not self._stop.wait(0.05):
            try:
                with self._lock:
                    if not self.process or not self.process.is_alive():
                        return
                    if not self._player_target_is_current():
                        self.disconnect()
                        self.last_error = (
                            "玩家对象已重建，实时功能已自动关闭；请重新连接"
                        )
                        return
                    self._refresh_dynamic_targets()
                    self._apply_continuous()
                    self.last_error = ""
                    self.last_tick = time.time()
            except Exception as exc:
                self.last_error = str(exc)
                time.sleep(0.2)

    def _state(self, feature_id: str) -> FeatureState:
        return self.states[feature_id]

    def _save_original(self, feature_id: str, address: int, size: int):
        assert self.process
        key = (feature_id, address)
        if key not in self._original_values:
            self._original_values[key] = self.process.read(address, size)

    def _write(
        self,
        feature_id: str,
        address: int,
        data: bytes,
        *,
        restore: bool = False,
    ):
        assert self.process
        if not address:
            return
        if restore:
            self._save_original(feature_id, address, len(data))
        self.process.write(address, data)

    def _write_i32(self, feature_id: str, address: int, value: int, **kwargs):
        self._write(feature_id, address, struct.pack("<i", int(value)), **kwargs)

    def _write_i64(self, feature_id: str, address: int, value: int, **kwargs):
        self._write(feature_id, address, struct.pack("<q", int(value)), **kwargs)

    def _write_f32(self, feature_id: str, address: int, value: float, **kwargs):
        self._write(feature_id, address, struct.pack("<f", float(value)), **kwargs)

    def _write_f64(self, feature_id: str, address: int, value: float, **kwargs):
        self._write(feature_id, address, struct.pack("<d", float(value)), **kwargs)

    def _restore_feature_originals(self, feature_id: str):
        if not self.process:
            return
        keys = [key for key in self._original_values if key[0] == feature_id]
        for key in keys:
            _, address = key
            data = self._original_values.pop(key)
            try:
                self.process.write(address, data)
            except TrainerError:
                pass

    def _original_f32(self, feature_id: str, address: int) -> float:
        assert self.process
        key = (feature_id, address)
        if key not in self._original_values:
            self._original_values[key] = self.process.read(address, 4)
        return struct.unpack("<f", self._original_values[key])[0]

    def _restore_original_values(self):
        for feature_id in {key[0] for key in self._original_values}:
            self._restore_feature_originals(feature_id)

    def _apply_continuous(self):
        assert self.process and self.offsets and self.targets
        o = self.offsets
        t = self.targets
        save = t.player_save

        max_hp = self._state("max_health")
        lock_hp = self._state("lock_health")
        god = self._state("god_mode")
        if max_hp.enabled:
            self._write_i64(
                "max_health",
                save + o.save_max_hp + o.fixed_point_value,
                int(max_hp.value) * 1000,
            )
        if lock_hp.enabled:
            self._write_i64(
                "lock_health",
                save + o.save_hp + o.fixed_point_value,
                int(lock_hp.value) * 1000,
            )


        if self._state("infinite_shield").enabled:
            maximum = self.process.read(
                save + o.save_shield_max_hp + o.fixed_point_value,
                8,
            )
            self._write(
                "infinite_shield",
                save + o.save_shield_hp + o.fixed_point_value,
                maximum,
            )
        max_shield = self._state("max_shield")
        if max_shield.enabled:
            self._write_i64(
                "max_shield",
                save + o.save_shield_max_hp + o.fixed_point_value,
                int(max_shield.value) * 1000,
            )

        max_food = self._state("max_hunger")
        if max_food.enabled:
            self._write_f32(
                "max_hunger",
                save + o.save_max_full_stomach,
                float(max_food.value),
            )
        if self._state("full_hunger").enabled:
            maximum = self.process.read(save + o.save_max_full_stomach, 4)
            self._write(
                "full_hunger",
                save + o.save_full_stomach,
                maximum,
            )
            self._write("full_hunger", save + o.save_hunger_type, b"\0")

        max_stamina = self._state("max_stamina")
        if max_stamina.enabled:
            self._write_i64(
                "max_stamina",
                save + o.save_max_sp + o.fixed_point_value,
                int(max_stamina.value) * 1000,
            )
        if self._state("infinite_stamina").enabled:
            maximum = self.process.read(
                save + o.save_max_sp + o.fixed_point_value,
                8,
            )
            self._write(
                "infinite_stamina",
                t.player_component + o.parameter_sp + o.fixed_point_value,
                maximum,
            )

        direct_i32 = (
            ("craft_speed", save + o.save_craft_speed),
            ("edit_attribute_points", save + o.save_unused_status_point),
            (
                "edit_technology_points",
                t.technology + o.technology_points if t.technology else 0,
            ),
            (
                "edit_ancient_points",
                t.technology + o.ancient_technology_points if t.technology else 0,
            ),
        )
        for feature_id, address in direct_i32:
            state = self._state(feature_id)
            if state.enabled:
                self._write_i32(feature_id, address, int(state.value))

        edit_money = self._state("edit_money")
        if edit_money.enabled and t.money:
            self._write_i64(
                "edit_money",
                t.money + o.money_value,
                int(edit_money.value),
            )
        edit_items = self._state("edit_item_amount")
        if edit_items.enabled:
            for slot in t.item_slots:
                if not self._item_slot_is_current(slot):
                    continue
                try:
                    current = struct.unpack(
                        "<i",
                        self.process.read(slot + o.item_slot_stack_count, 4),
                    )[0]
                    if current > 0:
                        self._write_i32(
                            "edit_item_amount",
                            slot + o.item_slot_stack_count,
                            int(edit_items.value),
                        )
                except TrainerError:
                    continue

        max_weight = self._state("max_weight")
        if max_weight.enabled and t.inventory:
            self._write_f32(
                "max_weight",
                t.inventory + o.inventory_max_weight,
                float(max_weight.value),
            )
            self._write_f32(
                "max_weight",
                t.inventory + o.inventory_cached_max_weight,
                float(max_weight.value),
            )

        if self._state("zero_weight").enabled and t.inventory:
            self._write_f32(
                "zero_weight",
                t.inventory + o.inventory_now_weight,
                0.0,
                restore=True,
            )

        player_speed = self._state("player_speed")
        if player_speed.enabled:
            self._write_f32(
                "player_speed",
                t.player + o.player_character_custom_time_dilation,
                float(player_speed.value),
                restore=True,
            )
        if self._state("infinite_jump").enabled:
            self._write_i32(
                "infinite_jump",
                t.player + o.player_character_jump_max_count,
                999,
                restore=True,
            )
            self._write_i32(
                "infinite_jump",
                t.player + o.player_character_jump_current_count,
                0,
            )

        ai_speed = self._state("ai_speed")
        if ai_speed.enabled:
            for target in t.all_pals:
                if not self._live_pal_target_is_current(
                    target,
                    require_owner=False,
                ):
                    continue
                try:
                    self._write_f32(
                        "ai_speed",
                        target.actor + o.player_character_custom_time_dilation,
                        float(ai_speed.value),
                        restore=True,
                    )
                except TrainerError:
                    continue

        movement_speed = self._state("movement_speed")
        if movement_speed.enabled and t.player_movement:
            for offset in (
                o.movement_max_walk_speed,
                o.movement_max_walk_speed_crouched,
                o.movement_max_swim_speed,
                o.movement_max_fly_speed,
                o.movement_max_custom_speed,
            ):
                address = t.player_movement + offset
                original = self._original_f32("movement_speed", address)
                self._write_f32(
                    "movement_speed",
                    address,
                    original * float(movement_speed.value),
                )
        jump_height = self._state("jump_height")
        if jump_height.enabled and t.player_movement:
            address = t.player_movement + o.movement_jump_z_velocity
            original = self._original_f32("jump_height", address)
            self._write_f32(
                "jump_height",
                address,
                original * float(jump_height.value),
            )

        if self._exp_controls:
            unlimited = self._state("unlimited_exp")
            multiplier = self._state("exp_multiplier")
            self.process.write(
                self._exp_controls.unlimited_flag,
                struct.pack("<i", 1 if unlimited.enabled else 0),
            )
            self.process.write(
                self._exp_controls.multiplier,
                struct.pack(
                    "<d",
                    float(multiplier.value) if multiplier.enabled else 1.0,
                ),
            )

        self._apply_pal_stats()
        self._apply_weapon_overheat()
        self._apply_world_settings()

    def _apply_pal_stats(self):
        assert self.process and self.offsets and self.targets
        o = self.offsets
        health = self._state("pal_lock_health")
        food = self._state("pal_full_hunger")
        san = self._state("pal_full_san")
        stamina = self._state("pal_infinite_stamina")
        if not any(state.enabled for state in (health, food, san, stamina)):
            return
        for target in tuple(self._owned_pals.values()):
            if not self._pal_individual_is_current(
                target,
                require_owner=True,
            ):
                continue
            try:
                if health.enabled:
                    self._write_i64(
                        "pal_lock_health",
                        target.save + o.save_hp + o.fixed_point_value,
                        int(health.value) * 1000,
                    )
                if food.enabled:
                    maximum = self.process.read(
                        target.save + o.save_max_full_stomach,
                        4,
                    )
                    self._write(
                        "pal_full_hunger",
                        target.save + o.save_full_stomach,
                        maximum,
                    )
                    self._write(
                        "pal_full_hunger",
                        target.save + o.save_hunger_type,
                        b"\0",
                    )
                if san.enabled:
                    self._write_f32(
                        "pal_full_san",
                        target.save + o.save_sanity_value,
                        9999.0,
                    )
                if stamina.enabled:
                    component = self._live_component_for_pal(target)
                    if not component:
                        continue
                    maximum = self.process.read(
                        target.save + o.save_max_sp + o.fixed_point_value,
                        8,
                    )
                    self._write(
                        "pal_infinite_stamina",
                        component + o.parameter_sp + o.fixed_point_value,
                        maximum,
                    )
            except TrainerError:
                continue

    def _apply_weapon_overheat(self):
        assert self.process and self.targets
        if not self._state("instant_weapon_cooldown").enabled:
            return
        layout = self.targets.overheat_weapon
        if not layout:
            return
        for weapon in tuple(self._overheat_weapons):
            if not self._overheat_weapon_is_current(weapon):
                self._overheat_weapons.discard(weapon)
                continue
            try:
                self._write_f64(
                    "instant_weapon_cooldown",
                    weapon + layout.heat_value,
                    0.0,
                )
                self._write_f64(
                    "instant_weapon_cooldown",
                    weapon + layout.displayed_heat,
                    0.0,
                )
                self._write_f64(
                    "instant_weapon_cooldown",
                    weapon + layout.heat_per_shot,
                    0.0,
                    restore=True,
                )
                self._write_f64(
                    "instant_weapon_cooldown",
                    weapon + layout.cooling_speed,
                    1000.0,
                    restore=True,
                )
                self._write(
                    "instant_weapon_cooldown",
                    weapon + layout.is_overheated,
                    b"\0",
                )
                self._write(
                    "instant_weapon_cooldown",
                    weapon + layout.is_in_cool_time,
                    b"\0",
                )
            except TrainerError:
                continue

    def _apply_world_settings(self):
        assert self.process and self.offsets and self.targets
        o = self.offsets
        option = self._option_pointer
        if option:
            settings = (
                ("capture_always", o.option_capture_rate, 1_000_000_000.0),
                ("instant_work", o.option_work_speed_rate, 10_000.0),
            )
            for feature_id, offset, value in settings:
                if self._state(feature_id).enabled:
                    self._write_f32(
                        feature_id,
                        option + offset,
                        value,
                        restore=True,
                    )
            drop = self._state("drop_multiplier")
            if drop.enabled:
                for offset in (
                    o.option_collection_drop_rate,
                    o.option_enemy_drop_rate,
                ):
                    self._write_f32(
                        "drop_multiplier",
                        option + offset,
                        float(drop.value),
                        restore=True,
                    )
            damage = self._state("damage_multiplier")
            one_hit = self._state("one_hit_kill")
            if damage.enabled or one_hit.enabled:
                value = 100_000.0 if one_hit.enabled else float(damage.value)
                self._write_f32(
                    "one_hit_kill" if one_hit.enabled else "damage_multiplier",
                    option + o.option_player_damage_attack,
                    value,
                    restore=True,
                )
            god = self._state("god_mode")
            defense = self._state("defense_multiplier")
            if god.enabled:
                self._write_f32(
                    "god_mode",
                    option + o.option_player_damage_defense,
                    0.0,
                    restore=True,
                )
            elif defense.enabled:
                self._write_f32(
                    "defense_multiplier",
                    option + o.option_player_damage_defense,
                    1.0 / float(defense.value),
                    restore=True,
                )

        if self.targets.world_settings:
            game_speed = self._state("game_speed")
            if game_speed.enabled:
                self._write_f32(
                    "game_speed",
                    self.targets.world_settings + o.world_settings_time_dilation,
                    float(game_speed.value),
                    restore=True,
                )

        if option:
            freeze = self._state("freeze_time")
            time_speed = self._state("time_speed")
            if freeze.enabled or time_speed.enabled:
                value = 0.0 if freeze.enabled else float(time_speed.value)
                for offset in (0x1C, 0x20):
                    self._write_f32(
                        "freeze_time" if freeze.enabled else "time_speed",
                        option + offset,
                        value,
                        restore=True,
                    )
