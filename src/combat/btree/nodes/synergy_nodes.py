"""环合反应触发节点。

将 CombatController._handle_synergy_reactions() 拆分为：
  - 浊燃过期清理（内嵌在 SynergyReactions 开头）
  - 变身E特殊路径
  - 正常能量决策路径
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.char.BaseChar import Element
from src.combat.btree.core import Action, BTStatus
from src.combat.SynergyRule import get_energy_decision, get_ring_adjacent
from src.sound_trigger.SoundCombatContext import SoundCombatContext

if TYPE_CHECKING:
    from src.combat.btree.context import BTContext


class SynergyReactions(Action):
    """环合反应触发 — 纯属性驱动决策。

    返回 SUCCESS 表示已处理环合反应（上层 Selector 短路），
    返回 FAILURE 表示无事发生，继续后续节点。
    """

    def __init__(self):
        super().__init__("SynergyReactions")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        cur = ctx.current_char
        if cur is None:
            return BTStatus.FAILURE

        # === 浊燃过期清理 ===
        self._clear_expired_synergy_a(ctx)

        my_elem = ctx.get_char_element(cur)
        if my_elem == Element.DEFAULT:
            return BTStatus.FAILURE

        # === 变身E特殊路径 ===
        if self._try_transform_e(ctx, cur):
            return BTStatus.SUCCESS

        # === 正常能量决策路径 ===
        return self._handle_normal_synergy(ctx, cur, my_elem)

    # ----- 浊燃过期清理 -----

    @staticmethod
    def _clear_expired_synergy_a(ctx: "BTContext"):
        if ctx.synergy_a_active and ctx.get_synergy_a_remaining() <= 0:
            ctx.synergy_a_active = False
            ctx.window_synergy_done = False
            ctx.synergy_b_count = 0
            ctx.synergy_a_start_time = 0.0
            ctx.logger.info("[BT] 浊燃已过期，清除黯星标记")

    # ----- 变身E路径 -----

    def _try_transform_e(self, ctx: "BTContext", cur) -> bool:
        if (
            not hasattr(cur, "transform_e_active")
            or not cur.transform_e_active
            or ctx.window_synergy_done
        ):
            return False

        target = self._get_transform_e_target(ctx, cur)
        if target is None:
            return False

        self._quick_swap_transform_e(ctx, cur, target)
        ctx.window_synergy_done = True
        ctx.energy_dirty = True
        self._report_synergy_on_switch(ctx, target, cur)
        return True

    def _get_transform_e_target(self, ctx: "BTContext", cur):
        """变身E专用：找相邻元素队友。"""
        my_elem = ctx.get_char_element(cur)
        ring = [
            Element.WHITE, Element.GREEN, Element.RED,
            Element.PURPLE, Element.BLUE, Element.YELLOW,
        ]
        try:
            idx = ring.index(my_elem)
        except ValueError:
            return None

        left_elem = ring[idx - 1] if idx > 0 else ring[-1]
        right_elem = ring[idx + 1] if idx < len(ring) - 1 else ring[0]

        left_candidates = []
        right_candidates = []
        for c in ctx.chars:
            if c is None or c is cur:
                continue
            elem = ctx.get_char_element(c)
            if elem == left_elem:
                left_candidates.append(c)
            elif elem == right_elem:
                right_candidates.append(c)

        def _sort_key(c):
            return c.ultimate_available()

        left_candidates.sort(key=_sort_key, reverse=True)
        right_candidates.sort(key=_sort_key, reverse=True)

        if ctx.synergy_a_active:
            remaining = ctx.get_synergy_a_remaining()
            if remaining >= ctx.system_secondary_duration:
                preferred = right_candidates + left_candidates
                reason = f"浊燃≥{ctx.system_secondary_duration}s，优先右邻"
            else:
                preferred = left_candidates + right_candidates
                reason = f"浊燃<{ctx.system_secondary_duration}s，优先左邻刷新"
        else:
            preferred = left_candidates + right_candidates
            reason = "无浊燃，优先左邻开启浊燃"

        if not preferred:
            return None

        target = preferred[0]
        ctx.logger.info(
            f"[BT] 变身E目标: {target} ({reason}) "
            f"(Q={'是' if target.ultimate_available() else '否'})"
        )
        return target

    def _quick_swap_transform_e(self, ctx: "BTContext", cur, target):
        """安魂曲变身E速切：切到目标 → 入场技 → Q/E → 轮询切回。

        反击可能在任意阶段触发（switch_to / wait_intro / QE / 轮询）。
        框架 sleep_check_interval=0.2s 太慢，在中断活跃时直接调用
        sleep_check() 立即执行反击，不等框架攒满 0.2s。

        每帧无条件校验前台角色：
        - 已是锚点 → 完成
        - 声音中断 → 立即 sleep_check() 执行反击
        - 非目标 → 先切回 target（处理达芙蒂尔反击后前台错位）
        - 在目标 → 发按键切回锚点
        """
        SoundCombatContext.enter_non_blocking()
        try:
            ctx.switch_to(target)
            ctx.task.sleep_check()  # 立即处理 switch_to 期间可能积累的反击
            ctx.current_char = target
            target.wait_intro()
            ctx.task.sleep_check()  # 立即处理入场技期间可能积累的反击
            ctx.try_quick_actions(target)
            ctx.task.sleep_check()  # 立即处理 Q/E 期间可能积累的反击

            # 如果进入Q动画（队伍UI消失），等待动画结束再开始轮询切回
            if not ctx.task.is_in_team():
                ctx.logger.info("[BT] 变身E: Q动画中，等待结束再切回")
                while not ctx.task.is_in_team():
                    ctx.task.next_frame()
                    ctx.task.check_combat()
                    ctx.task.sleep(0.1)
                ctx.logger.info("[BT] 变身E: Q动画结束，开始轮询切回")

            anchor = ctx.anchor_char
            if anchor is None:
                return

            ctx.logger.info(f"[BT] 变身E: 轮询切回{anchor}")
            loop_start = time.time()
            non_target_count = 0
            while True:
                ctx.task.next_frame()
                ctx.task.check_combat()
                # 已在锚点 → 完成
                if ctx.is_current_char(anchor.index):
                    ctx.current_char = anchor
                    ctx.confirmed_front_idx = anchor.index
                    ctx.logger.info(
                        f"[BT] 变身E: 成功切回安魂曲 "
                        f"(耗时{time.time() - loop_start:.2f}s, "
                        f"前台非目标={non_target_count}次)"
                    )
                    if hasattr(anchor, "_close_transform_window"):
                        anchor._close_transform_window()
                    return
                # 声音中断：立即执行反击/闪避，不等 0.2s 间隔
                if SoundCombatContext.should_interrupt_combat():
                    ctx.task.sleep_check()
                    continue
                # 超时兜底：3秒未切回则走 switch_to 强制切回，保住窗口
                if time.time() - loop_start > 3:
                    ctx.logger.warning(
                        f"[BT] 变身E: 轮询超时({time.time() - loop_start:.2f}s)，强制切回"
                    )
                    ctx.switch_to(anchor)
                    ctx.current_char = anchor
                    ctx.confirmed_front_idx = anchor.index
                    if hasattr(anchor, "_close_transform_window"):
                        anchor._close_transform_window()
                    return
                # 前台非目标：反击后达芙蒂尔可能没切回，先切回 target
                if not ctx.is_current_char(target.index):
                    non_target_count += 1
                    ctx.logger.info(f"[BT] 变身E: 前台非目标，先切回{target}")
                    ctx.switch_to(target)
                    ctx.task.sleep_check()
                    ctx.current_char = target
                    continue
                # 在目标上：发送切回锚点的按键
                target.click()
                ctx.task.send_key(anchor.index + 1)
                ctx.task.click()
                ctx.task.sleep(ctx.LOOP_TICK)
        finally:
            SoundCombatContext.exit_non_blocking()

    # ----- 正常环合路径 -----

    def _handle_normal_synergy(self, ctx: "BTContext", cur, my_elem: Element) -> BTStatus:
        """正常能量决策：收集满能量队友 → 调用决策引擎 → 执行。"""
        ctx.refresh_system_durations()

        # 收集满能量队友
        full_energy_elements: set[Element] = set()
        my_has_energy = (
            ctx.task.char_energy.get(cur.index, (False,))[0]
            or cur.is_cycle_full()
        )
        if my_has_energy:
            full_energy_elements.add(my_elem)

        for c in ctx.chars:
            if c is None or c is cur:
                continue
            if ctx.task.char_energy.get(c.index, (False,))[0]:
                c_elem = ctx.get_char_element(c)
                if c_elem != Element.DEFAULT:
                    full_energy_elements.add(c_elem)

        # 调用决策引擎
        decision = get_energy_decision(
            anchor_element=my_elem,
            full_energy_elements=full_energy_elements,
            synergy_a_remaining=ctx.get_synergy_a_remaining(),
            synergy_a_active=ctx.synergy_a_active,
            window_synergy_done=ctx.window_synergy_done,
            b_window=ctx.system_secondary_duration,
        )

        if decision["action"] == "do_nothing":
            return BTStatus.FAILURE

        target_element = decision["target_element"]
        if target_element is None:
            return BTStatus.FAILURE

        # 能量兜底：当前角色无能量但同元素队友有能量 → 跳板
        if not my_has_energy:
            energy_target = ctx.get_energy_teammate(my_elem, exclude=cur)
            if energy_target is not None:
                ctx.logger.info(
                    f"[BT] 能量兜底: {cur}({my_elem}) -> {energy_target}(同元素满能量)"
                )
                self._quick_swap_stepping_stone(ctx, cur, energy_target)
                ctx.window_synergy_done = True
                ctx.energy_dirty = True
                return BTStatus.SUCCESS

        # 同元素满能量处理
        if target_element == my_elem:
            if my_has_energy:
                return self._handle_anchor_energy(ctx, cur, my_elem)
            else:
                target = ctx.get_energy_teammate(target_element, exclude=cur)
                if target is None:
                    return BTStatus.FAILURE
                ctx.logger.info(f"[BT] 跳板: {cur} -> {target}(同元素能量满)")
                self._quick_swap_stepping_stone(ctx, cur, target)
                ctx.window_synergy_done = True
                ctx.energy_dirty = True
                return BTStatus.SUCCESS

        # 找目标元素队友
        target = ctx.get_energy_teammate(target_element, exclude=cur)
        if target is None:
            target = ctx.get_teammate_by_element(target_element, exclude=cur)
        if target is None:
            return BTStatus.FAILURE

        ctx.logger.info(
            f"[BT] {decision['reason']}: {cur}({my_elem}) -> {target}({target_element})"
        )
        self._quick_swap_with_intro(ctx, cur, target)
        ctx.window_synergy_done = True
        ctx.energy_dirty = True
        return BTStatus.SUCCESS

    def _handle_anchor_energy(self, ctx: "BTContext", cur, my_elem: Element) -> BTStatus:
        """锚点满能量时的切换决策。"""
        left_adj, right_adj = get_ring_adjacent(my_elem)
        if not ctx.synergy_a_active:
            target = ctx.get_teammate_by_element(left_adj, exclude=cur)
            reason = f"{my_elem}满能量，切左邻({left_adj})触发浊燃"
        elif ctx.get_synergy_a_remaining() >= ctx.system_secondary_duration:
            target = ctx.get_teammate_by_element(right_adj, exclude=cur)
            reason = (
                f"{my_elem}满能量，浊燃≥{ctx.system_secondary_duration}s，"
                f"切右邻({right_adj})触发黯星"
            )
        else:
            target = ctx.get_teammate_by_element(left_adj, exclude=cur)
            reason = (
                f"{my_elem}满能量，浊燃<{ctx.system_secondary_duration}s，"
                f"切左邻({left_adj})刷新浊燃"
            )

        if target is None:
            return BTStatus.FAILURE

        ctx.logger.info(f"[BT] {reason}: {cur} -> {target}")
        self._quick_swap_with_intro(ctx, cur, target)
        ctx.window_synergy_done = True
        ctx.energy_dirty = True
        return BTStatus.SUCCESS

    # ----- 切换动作 -----

    def _quick_swap_with_intro(self, ctx: "BTContext", current_char, target):
        """摔炮+入场技，通过 BLUE 路由切回避免浪费 RED 能量。"""
        ctx.switch_to(target)
        ctx.current_char = target
        self._report_synergy_on_switch(ctx, current_char, target)
        target.wait_intro()
        ctx.try_quick_actions(target)
        ctx.switch_back_to_anchor()

    def _quick_swap_stepping_stone(self, ctx: "BTContext", current_char, stepping_stone):
        """跳板式切换：同元素 → 相邻元素触发环合。"""
        my_elem = ctx.get_char_element(stepping_stone)
        left_adj, right_adj = get_ring_adjacent(my_elem)
        if not ctx.synergy_a_active:
            target_elem = left_adj
        elif ctx.get_synergy_a_remaining() >= ctx.system_secondary_duration:
            target_elem = right_adj
        else:
            target_elem = left_adj

        target = ctx.get_teammate_by_element(target_elem, exclude=stepping_stone)
        if target is None:
            ctx.switch_to(stepping_stone)
            ctx.current_char = stepping_stone
            ctx.switch_back_to_anchor()
            ctx.current_char = ctx.anchor_char
            ctx.confirmed_front_idx = ctx.anchor_char.index
            return

        ctx.logger.info(
            f"[BT] 跳板消耗: {stepping_stone}({my_elem}) -> {target}({target_elem})"
        )
        ctx.switch_to(stepping_stone)
        ctx.current_char = stepping_stone
        ctx.switch_to(target)
        ctx.current_char = target
        target.wait_intro()
        ctx.try_quick_actions(target)
        self._report_synergy_on_switch(ctx, stepping_stone, target)
        ctx.switch_back_to_anchor()
        ctx.current_char = ctx.anchor_char
        ctx.confirmed_front_idx = ctx.anchor_char.index

    # ----- 环合报告（与 meta_nodes.py 中的逻辑一致，保持独立以避免循环引用）-----

    @staticmethod
    def _report_synergy_on_switch(ctx: "BTContext", source, target):
        """报告环合反应触发，更新状态追踪。"""
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

        ctx.logger.info(f"[BT] 环合反应: {name} ({s_elem}↔{t_elem})")

        team_elements = {ctx.get_char_element(c) for c in ctx.chars if c}
        system_name = detect_team_system(team_elements)
        info = get_system_info(system_name)
        if info is None:
            return

        pair = frozenset({s_elem, t_elem})
        primary = info.get("primary_pair")
        secondary = info.get("secondary_pair")

        durations = get_system_durations(system_name)
        ctx.system_primary_duration = durations["primary_duration"]
        ctx.system_secondary_duration = durations["secondary_duration"]

        if pair == primary and primary is not None:
            is_dissonance = False
            if ctx.last_secondary_time > 0:
                sec_elapsed = ctx.task.time_elapsed_accounting_for_freeze(ctx.last_secondary_time)
                if sec_elapsed <= ctx.system_secondary_duration:
                    is_dissonance = True
            if not ctx.synergy_a_active:
                ctx.synergy_a_start_time = time.time()
                ctx.synergy_a_active = True
                ctx.window_synergy_done = False
                ctx.synergy_b_count = 0
                tag = "+失谐" if is_dissonance else ""
                ctx.logger.info(f"[BT] 浊燃{tag}, 持续{ctx.system_primary_duration}s")
            else:
                ctx.synergy_a_start_time = time.time()
                ctx.window_synergy_done = False
                ctx.synergy_b_count = 0
                tag = "+失谐" if is_dissonance else ""
                ctx.logger.info(f"[BT] 浊燃{tag}刷新, 重新计时{ctx.system_primary_duration}s")
        elif pair == secondary and secondary is not None:
            ctx.last_secondary_time = time.time()
            if ctx.synergy_a_active:
                ctx.synergy_b_count += 1
                ctx.logger.info(f"[BT] 黯星+失谐 (第{ctx.synergy_b_count}次)")
            else:
                ctx.logger.info("[BT] 黯星 (等待浊燃激活)")