"""元节点：预检、锚点检测、能量刷新、前台角色解析等通用逻辑。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.combat.btree.core import Action, BTStatus, Condition

if TYPE_CHECKING:
    from src.combat.btree.context import BTContext


# ====================== PreScan 预检 ======================


class PreScan(Action):
    """开场预检帧执行 + 预检完成后的日志与动态首发。

    前 N 帧只扫描不做动作，预检完成时打印日志并执行动态首发。
    """

    def __init__(self):
        super().__init__("PreScan")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        # 已在预检阶段
        if not ctx.pre_scan_done:
            ctx.pre_scan_count += 1
            cur = ctx.current_char
            char_idx = cur.index if cur is not None else -1
            detected_idx = ctx.task.get_current_char_index()

            # 双重验证确认前台角色
            if char_idx >= 0 and detected_idx >= 0 and char_idx == detected_idx:
                ctx.confirmed_front_idx = char_idx
            elif char_idx >= 0:
                ctx.confirmed_front_idx = char_idx
            elif detected_idx >= 0:
                ctx.confirmed_front_idx = detected_idx

            if ctx.pre_scan_count >= ctx.PRE_SCAN_FRAMES:
                ctx.pre_scan_done = True
                # 记录槽位状态
                ctx.slot_energy.clear()
                ctx.slot_ultimate.clear()
                for c in ctx.chars:
                    if c is not None:
                        ctx.slot_energy[c.index] = ctx.task.char_energy.get(c.index, (False,))[0]
                        ctx.slot_ultimate[c.index] = c.ultimate_available()
                self._log_pre_scan_result(ctx)
                self._dynamic_start_decision(ctx)
            return BTStatus.SUCCESS  # 预检帧消耗完毕，阻止后续节点（通过 Selector 短路）

        # 预检已完成，返回 FAILURE 让 Selector 继续
        return BTStatus.FAILURE

    def _log_pre_scan_result(self, ctx: "BTContext"):
        """打印预检结果日志。"""
        front_char = None
        if ctx.confirmed_front_idx >= 0:
            for c in ctx.chars:
                if c is not None and c.index == ctx.confirmed_front_idx:
                    front_char = c
                    break

        info_lines = ["[BT] === 战斗预检结果 ==="]
        front_slot = ctx.confirmed_front_idx + 1 if ctx.confirmed_front_idx >= 0 else "?"
        info_lines.append(f"  前台确认: {front_char or '未识别'} (slot{front_slot})")
        for c in ctx.chars:
            if c is not None:
                idx = c.index
                elem = ctx.get_char_element(c)
                has_e = ctx.slot_energy.get(idx, False)
                has_q = ctx.slot_ultimate.get(idx, False)
                anchor_flag = " | ★锚点" if c.is_anchor() else ""
                info_lines.append(
                    f"  Slot{idx + 1}: {c} ({elem}) "
                    f"能量={'●' if has_e else '○'} "
                    f"大招={'●' if has_q else '○'}{anchor_flag}"
                )
        for line in info_lines:
            ctx.logger.info(line)

    def _dynamic_start_decision(self, ctx: "BTContext"):
        """根据预检结果动态调整首发。"""
        # 确保锚点已检测（AnchorDetect 在树中排在 PreScan 后面，预检期间锚点未设置）
        if ctx.anchor_char is None:
            for c in ctx.chars:
                if c is not None and c.is_anchor():
                    ctx.anchor_char = c
                    ctx.logger.info(f"[BT] 锁定锚点: {ctx.anchor_char}")
                    break
            if ctx.anchor_char is None:
                ctx.anchor_char = ctx.current_char
        if ctx.anchor_char is None:
            return

        actual_front = None
        if ctx.confirmed_front_idx >= 0:
            for c in ctx.chars:
                if c is not None and c.index == ctx.confirmed_front_idx:
                    actual_front = c
                    break
        if actual_front is None:
            actual_front = ctx.anchor_char

        ctx.logger.info(f"[BT] 前台确认: {actual_front}, 锚点: {ctx.anchor_char}")

        # 前台不是锚点，先切回
        if actual_front is not ctx.anchor_char:
            ctx.logger.info(f"[BT] 前台非锚点，先切回: {actual_front} -> {ctx.anchor_char}")
            ctx.switch_to(ctx.anchor_char)
            ctx.current_char = ctx.anchor_char
            ctx.confirmed_front_idx = ctx.anchor_char.index
            # 报告环合
            self._report_synergy_on_switch(ctx, actual_front, ctx.anchor_char)

        my_elem = ctx.get_char_element(ctx.anchor_char)
        team_elements = {ctx.get_char_element(c) for c in ctx.chars if c}
        from src.combat.SynergyRule import detect_team_system, get_system_info

        system_name = detect_team_system(team_elements)
        info = get_system_info(system_name)

        primary_target_elem = None
        if info and not ctx.synergy_a_active:
            primary = info.get("primary_pair")
            if primary:
                for e in primary:
                    if e != my_elem:
                        primary_target_elem = e
                        break

        # 满能量 + 触发浊燃
        if primary_target_elem is not None:
            for c in ctx.chars:
                if c is None or c is ctx.anchor_char:
                    continue
                if ctx.get_char_element(c) == primary_target_elem and ctx.slot_energy.get(
                    c.index, False
                ):
                    ctx.logger.info(f"[BT] 预检首发: {c} 能量满，优先触发浊燃")
                    self._quick_swap_with_intro(ctx, ctx.anchor_char, c)
                    return

        # 任意满能量
        for c in ctx.chars:
            if c is None or c is ctx.anchor_char:
                continue
            if ctx.slot_energy.get(c.index, False):
                ctx.logger.info(f"[BT] 预检首发: {c} 能量满，先切去触发环合")
                self._quick_swap_with_intro(ctx, ctx.anchor_char, c)
                return

        # 有Q无能量
        for c in ctx.chars:
            if c is None or c is ctx.anchor_char:
                continue
            if ctx.slot_ultimate.get(c.index, False):
                ctx.logger.info(f"[BT] 预检首发: {c} 大招可用，先切过去释放")
                self._quick_swap_action(ctx, ctx.anchor_char, c, "q")
                return

    def _quick_swap_with_intro(self, ctx: "BTContext", current_char, target):
        """摔炮+入场技。"""
        ctx.switch_to(target)
        ctx.current_char = target
        self._report_synergy_on_switch(ctx, current_char, target)
        target.wait_intro()
        ctx.try_quick_actions(target)
        ctx.switch_back_to_anchor()

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
        # 更新确认索引
        ctx.confirmed_front_idx = ctx.anchor_char.index

    @staticmethod
    def _report_synergy_on_switch(ctx: "BTContext", source, target):
        """报告环合反应触发。"""
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


# ====================== AnchorDetect 锚点检测 ======================


class AnchorDetect(Action):
    """检测并锁定锚点角色（每场战斗首次）。"""

    def __init__(self):
        super().__init__("AnchorDetect")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        if ctx.anchor_char is not None:
            return BTStatus.FAILURE  # 已锁定，跳过
        for c in ctx.chars:
            if c is not None and c.is_anchor():
                ctx.anchor_char = c
                ctx.logger.info(f"[BT] 锁定锚点: {c.name}")
                return BTStatus.SUCCESS
        # 兜底：使用当前角色
        ctx.anchor_char = ctx.current_char
        if ctx.anchor_char is not None:
            ctx.logger.info(f"[BT] 兜底锚点: {ctx.anchor_char}")
            return BTStatus.SUCCESS
        return BTStatus.FAILURE




# ====================== EnergyRefresh 能量刷新 ======================


class EnergyRefresh(Action):
    """每帧刷新能量视觉检测 + 角色主动状态刷新。"""

    def __init__(self):
        super().__init__("EnergyRefresh")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        ctx.task.scan_all_energy()
        if ctx.current_char is not None and hasattr(ctx.current_char, "_check_cd_expired"):
            ctx.current_char._check_cd_expired()
        return BTStatus.SUCCESS


# ====================== ResolveCurrentChar 前台角色解析 ======================


class ResolveCurrentChar(Action):
    """解析前台角色：None 回退 + 一致性校验。"""

    def __init__(self):
        super().__init__("ResolveCurrentChar")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        cur = ctx.resolve_current_char()
        if cur is None:
            return BTStatus.FAILURE

        # 一致性校验
        if ctx.confirmed_front_idx >= 0 and cur.index != ctx.confirmed_front_idx:
            for c in ctx.chars:
                if c is not None and c.index == ctx.confirmed_front_idx:
                    if ctx.is_current_char(c.index):
                        ctx.logger.info(
                            f"[BT] 前台不一致: cur={cur}(idx{cur.index}), "
                            f"pre_scan确认={c}(idx{c.index})，使用pre_scan结果"
                        )
                        for cc in ctx.chars:
                            if cc is not None:
                                cc.is_current_char = cc.index == ctx.confirmed_front_idx
                        ctx.current_char = c
                    break
        return BTStatus.SUCCESS


# ====================== TransformEWindowGuard 变身E窗口守卫 ======================


class TransformEWindowGuard(Condition):
    """变身E窗口活跃时阻止后续节点。"""

    def __init__(self):
        super().__init__("TransformEWindowGuard", self._check)

    @staticmethod
    def _check(ctx: "BTContext") -> bool:
        cur = ctx.current_char
        if cur is not None and getattr(cur, "transform_e_active", False):
            return True
        return False


# ====================== WindowUnlock 窗口解锁 ======================


class WindowUnlock(Action):
    """无动作时开放窗口锁。变身窗口活跃时保持锁定。"""

    def __init__(self):
        super().__init__("WindowUnlock")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        cur = ctx.current_char
        if not getattr(cur, "transform_e_active", False):
            ctx.window_synergy_done = False
        return BTStatus.SUCCESS