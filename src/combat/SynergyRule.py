"""
环合反应全局配置模块 (SynergyRule) - 数据驱动版
"""

import json
import os

from src.char.BaseChar import Element

# ====================== 动态配置加载 ======================

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "synergy_config.json")

ELEMENT_RING: tuple[Element, ...] = ()
SYNERGY_PAIRS: dict[frozenset[Element], str] = {}
SYNERGY_TRIPLE: dict[frozenset[Element], str] = {}
TEAM_SYSTEMS: dict[str, frozenset[Element]] = {}
SYSTEM_INFO: dict[str, dict] = {}


def _load_config():
    global ELEMENT_RING, SYNERGY_PAIRS, SYNERGY_TRIPLE, TEAM_SYSTEMS, SYSTEM_INFO

    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"[SynergyRule] 找不到配置文件: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. 解析元素环
    ring_list = []
    for e_name in data.get("element_ring", []):
        e_enum = getattr(Element, e_name.upper(), None)
        if e_enum:
            ring_list.append(e_enum)
    ELEMENT_RING = tuple(ring_list)

    # 2. 解析双属性环合 (处理 "White-Green" 分割)
    for pair_key, name in data.get("synergy_pairs", {}).items():
        parts = pair_key.split("-")
        if len(parts) == 2:
            e1 = getattr(Element, parts[0].upper(), None)
            e2 = getattr(Element, parts[1].upper(), None)
            if e1 and e2:
                SYNERGY_PAIRS[frozenset({e1, e2})] = name

    # 3. 解析三属性环合
    for triple_key, name in data.get("synergy_triple", {}).items():
        parts = triple_key.split("-")
        elements = [getattr(Element, p.upper(), None) for p in parts]
        if all(elements):
            SYNERGY_TRIPLE[frozenset(elements)] = name

    # 4. 解析体系配置 (动态加载失谐/创生元素集合)
    for sys_name, sys_info in data.get("systems", {}).items():
        sys_elems = frozenset(
            getattr(Element, e.upper(), Element.DEFAULT)
            for e in sys_info.get("elements", [])
        )
        TEAM_SYSTEMS[sys_name] = sys_elems

        # 构建完整体系信息 (供 CombatController 使用)
        entry: dict = {
            "elements": sys_elems,
            "description": sys_info.get("description", sys_name),
        }
        if "anchor" in sys_info:
            entry["anchor"] = getattr(Element, sys_info["anchor"].upper(), None)
        if "primary_pair" in sys_info:
            parts = sys_info["primary_pair"].split("-")
            e1 = getattr(Element, parts[0].upper(), None)
            e2 = getattr(Element, parts[1].upper(), None)
            if e1 and e2:
                entry["primary_pair"] = frozenset({e1, e2})
        if "secondary_pair" in sys_info:
            parts = sys_info["secondary_pair"].split("-")
            e1 = getattr(Element, parts[0].upper(), None)
            e2 = getattr(Element, parts[1].upper(), None)
            if e1 and e2:
                entry["secondary_pair"] = frozenset({e1, e2})
        if "primary_duration" in sys_info:
            entry["primary_duration"] = float(sys_info["primary_duration"])
        if "secondary_duration" in sys_info:
            entry["secondary_duration"] = float(sys_info["secondary_duration"])
        SYSTEM_INFO[sys_name] = entry


_load_config()

_ELEMENT_RING_INDEX = {e: i for i, e in enumerate(ELEMENT_RING)}
_RING_SIZE = len(ELEMENT_RING)

# ====================== 动态体系检测 ======================


def detect_team_system(elements: set[Element]) -> str:
    """根据队伍元素自动检测所属体系（动态读取JSON规则）"""
    # 完全匹配
    for sys_name, sys_elements in TEAM_SYSTEMS.items():
        if elements.issubset(sys_elements):
            return sys_name

    # 混合队伍判定（看哪个体系的元素多）
    max_overlap = 0
    best_system = "creation"  # 默认兜底
    for sys_name, sys_elements in TEAM_SYSTEMS.items():
        overlap = len(elements & sys_elements)
        if overlap > max_overlap:
            max_overlap = overlap
            best_system = sys_name
    return best_system


# ====================== 公共查询 API ======================

def get_system_elements(system_name: str) -> frozenset[Element] | None:
    """获取指定体系的元素集合。"""
    return TEAM_SYSTEMS.get(system_name)


def get_system_info(system_name: str) -> dict | None:
    """获取指定体系的完整配置信息（含 primary_pair / secondary_pair / anchor / durations）。"""
    return SYSTEM_INFO.get(system_name)


def get_system_durations(system_name: str) -> dict:
    """获取指定体系的持续时间配置，直接从 JSON 配置读取。"""
    info = SYSTEM_INFO[system_name]
    return {
        "primary_duration": info["primary_duration"],
        "secondary_duration": info["secondary_duration"],
    }


def get_synergy_pair_name(elem_a: Element, elem_b: Element) -> str | None:
    """获取双属性环合反应名，无反应返回 None。顺序无关。"""
    if elem_a == elem_b:
        return None
    return SYNERGY_PAIRS.get(frozenset({elem_a, elem_b}))


def get_synergy_triple_name(*elements: Element) -> str | None:
    """获取三属性环合反应名，无反应返回 None。顺序无关。"""
    return SYNERGY_TRIPLE.get(frozenset(elements))


def is_synergy_adjacent(elem_a: Element, elem_b: Element) -> bool:
    """判断两个元素在元素环上是否相邻（可触发环合/入场技）。"""
    if elem_a == elem_b:
        return False
    idx_a = _ELEMENT_RING_INDEX.get(elem_a)
    idx_b = _ELEMENT_RING_INDEX.get(elem_b)
    if idx_a is None or idx_b is None:
        return False
    diff = abs(idx_a - idx_b)
    return diff == 1 or diff == _RING_SIZE - 1


def get_ring_adjacent(anchor: Element) -> tuple[Element, Element]:
    """返回锚点元素在元素环上的两个相邻元素 (left, right)。

    left  = 环上逆时针方向相邻元素
    right = 环上顺时针方向相邻元素
    """
    idx = _ELEMENT_RING_INDEX.get(anchor)
    if idx is None:
        raise ValueError(f"Unknown element: {anchor}")
    left = ELEMENT_RING[(idx - 1) % _RING_SIZE]
    right = ELEMENT_RING[(idx + 1) % _RING_SIZE]
    return left, right


def get_synergy_pair_for_anchor(anchor: Element) -> dict[str, tuple[Element, Element]]:
    """获取锚点元素的两个环合反应配对。

    Returns:
        {"a": (anchor, left_adjacent), "b": (anchor, right_adjacent)}
    """
    left, right = get_ring_adjacent(anchor)
    return {
        "a": (left, anchor),
        "b": (right, anchor),
    }


# ====================== 决策引擎 ======================

def get_synergy_window_decision(
    anchor_element: Element,
    synergy_a_remaining: float,
    synergy_a_active: bool,
    window_synergy_done: bool,
    b_window: float = 5.0,
) -> dict:
    """根据 primary 状态获取下一步决策。

    Args:
        anchor_element: 锚点元素
        synergy_a_remaining: primary 状态剩余时间（秒）
        synergy_a_active: primary 状态是否激活
        window_synergy_done: 窗口内是否已执行过动作
        b_window: secondary 窗口阈值（秒），来自体系配置的 secondary_duration

    Returns:
        {"action": "switch_left" | "switch_right" | "do_nothing",
         "target_element": Element | None, "reason": str}
    """
    if not synergy_a_active:
        return {"action": "do_nothing", "target_element": None, "reason": "primary未激活"}

    if window_synergy_done:
        return {"action": "do_nothing", "target_element": None, "reason": "窗口已执行过动作"}

    left_adjacent, right_adjacent = get_ring_adjacent(anchor_element)

    if synergy_a_remaining >= b_window:
        # primary 剩余≥b_window，切右邻触发 secondary
        return {
            "action": "switch_right",
            "target_element": right_adjacent,
            "reason": f"primary≥{b_window}s，切右邻({right_adjacent})触发secondary",
        }
    else:
        # primary < b_window，切左邻刷新
        return {
            "action": "switch_left",
            "target_element": left_adjacent,
            "reason": f"primary<{b_window}s，切左邻({left_adjacent})刷新",
        }


def get_energy_decision(
    anchor_element: Element,
    full_energy_elements: set[Element],
    synergy_a_remaining: float = 0,
    synergy_a_active: bool = False,
    window_synergy_done: bool = False,
    b_window: float = 5.0,
) -> dict:
    """综合决策：先检查 primary 状态窗口，再检查满能量队友。

    Args:
        anchor_element: 锚点元素
        full_energy_elements: 当前能量已满的队友元素集合
        synergy_a_remaining: primary 状态剩余时间
        synergy_a_active: primary 状态是否激活
        window_synergy_done: 窗口是否已执行
        b_window: secondary 窗口阈值（秒），来自体系配置的 secondary_duration

    Returns:
        {"action", "target_element", "reason"}
        action: "switch_left" | "switch_right" | "switch_energy" | "do_nothing"
    """
    # 1. 先检查 primary 状态窗口
    window = get_synergy_window_decision(
        anchor_element, synergy_a_remaining, synergy_a_active, window_synergy_done, b_window
    )
    if window["action"] != "do_nothing":
        # ★ 窗口切换必须目标元素有能量才有效，否则无能量切换只会产生假反应
        target_elem = window["target_element"]
        if target_elem in full_energy_elements:
            return window
        # 目标元素无能量，窗口时机不可用，继续检查能量路径

    # 2. 无 primary 状态窗口，检查满能量队友
    if not full_energy_elements:
        return {"action": "do_nothing", "target_element": None, "reason": "无满能量队友"}

    left, right = get_ring_adjacent(anchor_element)

    # 右邻满能量 → 切右邻触发 secondary
    # ★ 必须同时满足: primary 状态激活 + 窗口未执行过动作。
    #   缺少 synergy_a_active 会导致 primary 状态过期后（_window_synergy_done 已重置）
    #   仍在能量路径上错误触发 secondary，如黯星。
    if right in full_energy_elements and not window_synergy_done and synergy_a_active:
        return {
            "action": "switch_energy",
            "target_element": right,
            "reason": f"{right}满能量，切右邻触发secondary",
        }

    # 左邻满能量 → 切左邻触发 primary
    # ★ 窗口已执行过动作后，不再触发能量切换
    if left in full_energy_elements and not window_synergy_done:
        if not synergy_a_active or synergy_a_remaining < b_window:
            return {
                "action": "switch_energy",
                "target_element": left,
                "reason": f"{left}满能量，切左邻触发primary",
            }

    # 同元素满能量 → 消耗（跳板路径）
    # ★ 窗口已执行过动作后，不再触发跳板切换
    if anchor_element in full_energy_elements and not window_synergy_done:
        return {
            "action": "switch_energy",
            "target_element": anchor_element,
            "reason": f"{anchor_element}同元素满能量，消耗",
        }

    return {"action": "do_nothing", "target_element": None, "reason": "无待处理事项"}