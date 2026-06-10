"""行为树执行上下文 — 持有战斗状态，供所有节点共享访问。

BTContext 替代 CombatController 的实例属性，将战斗状态从控制器中解耦，
使得行为树节点可以无状态地通过 ctx 访问所有需要的数据。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.char.BaseChar import Element
from src.combat.SynergyRule import detect_team_system, get_system_durations

if TYPE_CHECKING:
    from logging import Logger

    from src.char.BaseChar import BaseChar
    from src.combat.BaseCombatTask import BaseCombatTask
    from src.combat.btree.core import BTNode


class BTContext:
    """行为树执行上下文。

    持有：
    - task 引用（BaseCombatTask）
    - 角色引用（chars / anchor_char / current_char）
    - 环合状态追踪（synergy_a/b/window）
    - 预检状态
    - 冷却/时间追踪
    """

    # 配置常量（与 CombatController 保持一致）
    BACKLINE_ULT_COOLDOWN: float = 20.0
    SWITCH_TIMEOUT: float = 1.0
    LOOP_TICK: float = 0.05
    POST_ACTION_PAUSE: float = 0.1
    INTRO_MOTION_FREEZE: float = 0.8
    PRE_SCAN_FRAMES: int = 3

    def __init__(self, task: "BaseCombatTask"):
        self.task = task
        self.logger: "Logger" = task.logger

        # 角色引用
        self.anchor_char: "BaseChar | None" = None
        self.current_char: "BaseChar | None" = None

        # 环合状态追踪
        self.synergy_a_active: bool = False
        self.synergy_a_start_time: float = 0.0
        self.synergy_b_count: int = 0
        self.last_secondary_time: float = 0.0
        self.window_synergy_done: bool = False
        self.energy_dirty: bool = False

        # 冷却
        self.backline_ult_timestamps: dict[int, float] = {}
        self.ult_cooldown: dict[int, float] = {}

        # 预检
        self.pre_scan_done: bool = False
        self.pre_scan_count: int = 0
        self.confirmed_front_idx: int = -1
        self.slot_energy: dict[int, bool] = {}
        self.slot_ultimate: dict[int, bool] = {}
        self.prev_char_energy: dict[int, bool] = {}

        # 体系持续时间（每帧刷新）
        self.system_primary_duration: float = 0.0
        self.system_secondary_duration: float = 0.0

        # 行为树根节点引用
        self._btree_root: "BTNode | None" = None

    # ===== 便捷属性 =====

    @property
    def chars(self) -> list["BaseChar | None"]:
        return self.task.chars

    # ===== 元素工具 =====

    @staticmethod
    def get_char_element(c: "BaseChar") -> Element:
        """获取角色元素。"""
        if c.element and c.element != Element.DEFAULT:
            return c.element
        return Element.DEFAULT

    # ===== 角色查找 =====

    def get_teammate_by_element(
        self, element: Element, exclude: "BaseChar | None" = None
    ) -> "BaseChar | None":
        """根据元素查找队伍中对应角色。"""
        for c in self.chars:
            if c is None or c is exclude:
                continue
            if self.get_char_element(c) == element:
                return c
        return None

    def get_energy_teammate(
        self, element: Element, exclude: "BaseChar | None" = None
    ) -> "BaseChar | None":
        """找元素匹配且当前有满能量的具体队友。"""
        for c in self.chars:
            if c is None or c is exclude:
                continue
            if self.get_char_element(c) != element:
                continue
            if self.task.char_energy.get(c.index, (False,))[0]:
                return c
        return None

    # ===== 环合时间 =====

    def get_synergy_a_remaining(self) -> float:
        """获取浊燃剩余时间（扣除大招动画时停）。"""
        if not self.synergy_a_active or self.synergy_a_start_time <= 0:
            return 0.0
        elapsed = self.task.time_elapsed_accounting_for_freeze(self.synergy_a_start_time)
        remaining = self.system_primary_duration - elapsed
        return max(0.0, remaining)

    # ===== 前台角色确认 =====

    def is_current_char(self, index: int) -> bool:
        """双路确认当前角色是否为目标 index。"""
        flag_char = self.task.get_current_char(raise_exception=False)
        flag_idx = flag_char.index if flag_char is not None else -1
        detected_idx = self.task.get_current_char_index()
        return flag_idx == index or detected_idx == index

    def resolve_current_char(self) -> "BaseChar | None":
        """解析当前前台角色，失败时回退到预检确认。"""
        cur = self.current_char
        if cur is not None:
            return cur
        if self.confirmed_front_idx >= 0:
            for c in self.chars:
                if c is not None and c.index == self.confirmed_front_idx:
                    self.logger.info(f"[BT] get_current_char=None, 回退到预检确认: {c}")
                    self.current_char = c
                    return c
        return None

    # ===== 切换工具 =====

    def switch_to(self, target: "BaseChar"):
        """切换到目标角色。"""
        cur = self.current_char
        if cur is None:
            return
        self.logger.info(f"[BT] 切换: {cur} -> {target}")
        switch_start = time.time()
        while time.time() - switch_start < self.SWITCH_TIMEOUT * 2:
            self.task.next_frame()
            self.task.check_combat()
            if self.is_current_char(target.index):
                for c in self.chars:
                    if c is not None:
                        c.is_current_char = c.index == target.index
                self.current_char = target
                self.logger.info(f"[BT] 切换确认: {target}")
                return
            self.task.click()
            self.task.sleep(0.02)
            self.task.send_key(target.index + 1)
            self.task.sleep(self.LOOP_TICK)
        self.logger.warning(f"[BT] 切换超时: {cur} -> {target}")

    def switch_back_to_anchor(self):
        """切回锚点角色。"""
        if self.anchor_char is None:
            return
        cur = self.current_char
        if cur is self.anchor_char:
            return
        self.switch_to(self.anchor_char)
        self.anchor_char.wait_intro()

    # ===== 摔炮动作 =====

    def try_quick_actions(self, target: "BaseChar"):
        """摔炮动作：有Q放Q，有E放E。"""
        if target.ultimate_available():
            target.click_ultimate()
        if target.skill_available():
            target.click_skill(time_out=0.3)

    # ===== 体系配置刷新 =====

    def refresh_system_durations(self):
        """刷新体系持续时间（每帧调用）。"""
        team_elements = {self.get_char_element(c) for c in self.chars if c}
        system_name = detect_team_system(team_elements)
        durations = get_system_durations(system_name)
        self.system_primary_duration = durations["primary_duration"]
        self.system_secondary_duration = durations["secondary_duration"]

    # ===== 重置 =====

    def reset(self):
        """重置所有状态（热键/战斗结束后调用）。"""
        self.anchor_char = None
        self.current_char = None
        self.synergy_a_active = False
        self.synergy_a_start_time = 0.0
        self.synergy_b_count = 0
        self.last_secondary_time = 0.0
        self.window_synergy_done = False
        self.energy_dirty = False
        self.backline_ult_timestamps.clear()
        self.ult_cooldown.clear()
        self.pre_scan_done = False
        self.pre_scan_count = 0
        self.confirmed_front_idx = -1
        self.slot_energy.clear()
        self.slot_ultimate.clear()
        self.prev_char_energy.clear()
        self.system_primary_duration = 0.0
        self.system_secondary_duration = 0.0