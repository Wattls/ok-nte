"""后台大招释放节点。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.combat.btree.core import Action, BTStatus

if TYPE_CHECKING:
    from src.combat.btree.context import BTContext


class BacklineUltimate(Action):
    """后台大招释放 — 遍历队友，有Q就切过去释放。

    优先处理有能量+Q的队友（先释放Q再消耗能量触发环合），
    纯Q的队友直接切过去放Q。
    """

    def __init__(self):
        super().__init__("BacklineUltimate")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        cur = ctx.current_char
        if cur is None:
            return BTStatus.FAILURE

        now = time.time()
        backline = []
        for c in ctx.chars:
            if c is None or c is cur:
                continue
            last_time = ctx.backline_ult_timestamps.get(c.index, 0)
            if now - last_time < ctx.BACKLINE_ULT_COOLDOWN:
                continue
            if ctx.task.has_ultimate_visual(c.index):
                backline.append(c)

        if not backline:
            return BTStatus.FAILURE

        ctx.logger.info(f"[BT] 后台Q: {len(backline)}个队友, 释放")
        for c in backline:
            ctx.backline_ult_timestamps[c.index] = now
            has_energy = ctx.task.char_energy.get(c.index, (False,))[0]
            if has_energy:
                ctx.logger.info(f"[BT] 后台Q+能量: {c} 满能量，先消耗再Q")
                self._quick_swap_synergy_then_q(ctx, cur, c)
            else:
                self._quick_swap_action(ctx, cur, c, "q")
            ctx.energy_dirty = True
        return BTStatus.SUCCESS

    def _quick_swap_action(self, ctx: "BTContext", current_char, target, action: str = "q"):
        """摔炮：切到队友 → 放技能 → 切回。"""
        ctx.switch_to(target)
        ctx.current_char = target
        if action == "q":
            target.click_ultimate()
        elif action == "e":
            if target.skill_available():
                target.click_skill(time_out=0.3)
        ctx.switch_back_to_anchor()
        ctx.current_char = ctx.anchor_char
        ctx.confirmed_front_idx = ctx.anchor_char.index

    def _quick_swap_synergy_then_q(self, ctx: "BTContext", current_char, target):
        """既有能量又有Q：先释放Q，再消耗能量触发环合。"""
        from src.combat.SynergyRule import get_ring_adjacent

        my_elem = ctx.get_char_element(target)
        left_adj, right_adj = get_ring_adjacent(my_elem)

        if not ctx.synergy_a_active:
            adj_elem = left_adj
        elif ctx.get_synergy_a_remaining() >= ctx.system_secondary_duration:
            adj_elem = right_adj
        else:
            adj_elem = left_adj

        adj_target = ctx.get_teammate_by_element(adj_elem, exclude=target)

        # Step 1: 切到目标
        ctx.switch_to(target)
        ctx.current_char = target

        # Step 2: 放Q
        if target.ultimate_available():
            target.click_ultimate()

        if adj_target is not None:
            # Step 3: 目标 → 邻元素（消耗能量触发环合）
            ctx.switch_to(adj_target)
            ctx.current_char = adj_target
            self._report_synergy(ctx, target, adj_target)
            adj_target.wait_intro()
            ctx.try_quick_actions(adj_target)

        # Step 4: 切回锚点
        ctx.switch_back_to_anchor()
        ctx.current_char = ctx.anchor_char
        ctx.confirmed_front_idx = ctx.anchor_char.index

    @staticmethod
    def _report_synergy(ctx: "BTContext", source, target):
        """报告环合反应触发（精简版，仅 primary_pair 判定）。"""
        import time

        from src.char.BaseChar import Element
        from src.combat.SynergyRule import (
            detect_team_system,
            get_synergy_pair_name,
            get_system_durations,
            get_system_info,
        )

        s_elem = ctx.get_char_element(source)
        t_elem = ctx.get_char_element(target)
        if s_elem == Element.DEFAULT or t_elem == Element.DEFAULT:
            return

        name = get_synergy_pair_name(s_elem, t_elem)
        if name is None:
            return

        ctx.logger.info(f"[BT] 后台环合反应: {name} ({s_elem}↔{t_elem})")

        team_elements = {ctx.get_char_element(c) for c in ctx.chars if c}
        system_name = detect_team_system(team_elements)
        info = get_system_info(system_name)
        if info is None:
            return

        pair = frozenset({s_elem, t_elem})
        primary = info.get("primary_pair")

        if pair == primary and primary is not None:
            durations = get_system_durations(system_name)
            ctx.system_primary_duration = durations["primary_duration"]
            ctx.system_secondary_duration = durations["secondary_duration"]
            if not ctx.synergy_a_active:
                ctx.synergy_a_start_time = time.time()
                ctx.synergy_a_active = True
                ctx.window_synergy_done = False
                ctx.synergy_b_count = 0
                ctx.logger.info(f"[BT] 浊燃, 持续{ctx.system_primary_duration}s")