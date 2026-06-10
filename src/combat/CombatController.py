"""
核心战斗层 (CombatController) — 纯属性驱动架构

基于《异环自动化脚本重构文档 V2.1》设计，提供：
  - 纯属性驱动的环合反应决策引擎
  - 通用可配置主C（锚点角色）驻场机制
  - 摔炮速切：非主C角色切前台释放技能后立即切回
  - 监控后台大招
  - 环合能量精准追踪（视觉校验）
  - 全队伍通配，无角色硬编码

使用方式：
  在 AutoCombatTask.run() 中，当 CoreLayer 开关启用时，
  创建 CombatController 实例并调用 core.perform() 替代旧的 get_current_char().perform()。
"""

import time
from typing import TYPE_CHECKING

from src.char.BaseChar import Element
from src.combat.SynergyRule import (
    detect_team_system,
    get_energy_decision,
    get_ring_adjacent,
    get_synergy_pair_name,
    get_system_durations,
    get_system_info,
)
from src.sound_trigger.SoundCombatContext import SoundCombatContext

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar
    from src.combat.BaseCombatTask import BaseCombatTask


# ====================== CombatController 主类 ======================


class CombatController:
    """核心战斗层 — 纯属性驱动，全队伍通配。

    核心机制：
    1. 锚点角色（主C）驻场打普通攻击攒能
    2. 其他角色为"摔炮"：仅触发环合反应或释放Q/E，不放普通攻击
    3. 所有反应决策由 SynergyRule 模块纯属性驱动
    4. 支持失谐体系（暗/咒/魂）和创生体系（光/灵/相）

    配置常量（可按需调整）：
    """

    BACKLINE_ULT_COOLDOWN = 20.0  # 后台Q冷却
    SWITCH_TIMEOUT = 1.0  # 切换确认超时
    LOOP_TICK = 0.05  # 循环间隔
    POST_ACTION_PAUSE = 0.1  # 动作后暂停
    INTRO_MOTION_FREEZE = 0.8  # 入场动画冻结

    def __init__(self, task: "BaseCombatTask"):
        self.task = task
        self.logger = task.logger
        self._backline_ult_timestamps: dict[int, float] = {}
        self._ult_cooldown: dict[int, float] = {}  # Q冷却：防止视觉滞后重复阻塞
        self.anchor_char: "BaseChar | None" = None  # 主C锚点角色（强锁定）
        # 环合状态追踪
        self._synergy_a_active = False  # 浊燃 是否激活
        self._synergy_a_start_time = 0.0  # 浊燃 开始时间
        self._synergy_b_count = 0  # 黯星 结算次数
        self._last_secondary_time = 0.0  # 上次黯星触发时间（用于失谐判定）
        self._window_synergy_done = False  # 当前窗口是否已执行
        # 战斗预检（每场战斗重置）
        self._pre_scan_done = False  # 预检是否完成
        self._pre_scan_count = 0  # 当前扫描帧数
        self._pre_scan_frames = 3  # 总扫描帧数（稳定检测）
        self._confirmed_front_idx: int = -1  # 预检确认的前台角色index
        self._slot_energy: dict[int, bool] = {}  # slot_index -> 满能量
        self._slot_ultimate: dict[int, bool] = {}  # slot_index -> 大招可用
        # 动态持续时间（由体系配置驱动，每帧刷新）
        self._system_primary_duration: float = 0.0
        self._system_secondary_duration: float = 0.0

    # ===== 主循环 =====

    def perform(self, current_char: "BaseChar | None"):
        """核心战斗主循环 — 替代旧的 BaseChar.perform()。

        执行链（从高到低）：
        环合反应触发（消耗能量，入场技）
        自身Q/E技能
        后台大招释放
        普通攻击攒能
        """
        # 视觉检测失败时回退到预检确认的前台角色，避免循环空转
        if current_char is None:
            if self._confirmed_front_idx >= 0:
                for c in self.task.chars:
                    if c is not None and c.index == self._confirmed_front_idx:
                        current_char = c
                        self.logger.info(f"[核心层] get_current_char=None, 回退到预检确认: {c}")
                        break
        if current_char is None:
            return

        # 锁定锚点角色（每轮首次检测）
        if self.anchor_char is None:
            self._detect_anchor(current_char)

        current_char.last_perform = time.time()

        # 每帧刷新能量视觉检测（模板匹配 + 环形遮罩）
        self.task.scan_all_energy()

        # 角色主动状态刷新（如安魂曲E技CD视觉确认）
        if hasattr(current_char, "_check_cd_expired"):
            current_char._check_cd_expired()

        # ===== 开场预检（消耗前几帧稳定扫描，不做战斗动作） =====
        if not self._pre_scan_done:
            self._pre_scan_frame(current_char)
            if self._pre_scan_done:
                # 预检完成：记录槽位状态 + 日志 + 动态首发
                self._slot_energy.clear()
                self._slot_ultimate.clear()
                for c in self.task.chars:
                    if c is not None:
                        self._slot_energy[c.index] = self.task.char_energy.get(c.index, (False,))[0]
                        self._slot_ultimate[c.index] = c.ultimate_available()
                self._log_pre_scan_result()
                self._dynamic_start_decision()
            return

        # 每帧前台角色一致性校验：预检确认的角色可能与 current_char 不一致
        if self._confirmed_front_idx >= 0 and current_char.index != self._confirmed_front_idx:
            for c in self.task.chars:
                if c is not None and c.index == self._confirmed_front_idx:
                    if self._is_current_char(c.index):
                        self.logger.info(
                            f"[核心层] 前台不一致: cur={current_char}(idx{current_char.index}), "
                            f"pre_scan确认={c}(idx{c.index})，使用pre_scan结果"
                        )
                        # 同步 is_current_char 标志，使后续 get_current_char() 返回正确角色
                        for cc in self.task.chars:
                            if cc is not None:
                                cc.is_current_char = cc.index == self._confirmed_front_idx
                        current_char = c
                    break

        # 环合反应触发（入场技，消耗能量）
        if self._handle_synergy_reactions(current_char):
            return
        # 无动作，开放窗口锁，下一轮重新评估能量决策
        # 变身窗口活跃时保持窗口锁，防止重复进入变身E逻辑
        if not getattr(current_char, "transform_e_active", False):
            self._window_synergy_done = False

        # 自身Q/E（锚点使用技能，变身E/普通E在此触发）
        # Q冷却：防止视觉滞后导致重复触发，浪费动画等待时间
        if time.time() - self._ult_cooldown.get(current_char.index, 0) >= 5.0:
            if current_char.ultimate_available():
                self._ult_cooldown[current_char.index] = time.time()
                if current_char.click_ultimate():
                    return
                # click_ultimate 返回 False（没真正释放，如"no effect"）
                # 不 return，继续往下走，避免 Q 视觉检测滞后导致无限循环
        if current_char.click_skill(time_out=0.3)[0]:
            return

        # 变身E窗口内：不做后台Q也不普攻，等待切人完成窗口关闭
        if getattr(current_char, "transform_e_active", False):
            return

        # 后台大招（不消耗环合能量）
        if self._handle_backline_ultimate(current_char):
            return

        # 普通攻击攒能
        current_char.continues_normal_attack(0.1)
        # 角色主动管理钩子（如安魂曲普攻刷新普通E）
        if hasattr(current_char, "_on_after_aa"):
            current_char._on_after_aa()

    # ===== 开场预检与方法首发改写 =====

    def _pre_scan_frame(self, current_char: "BaseChar"):
        """单帧预检：双重验证确认前台角色 + 累积帧数。

        使用 perform() 传来的 current_char（基于 is_current_char 标志）
        与独立图像检测 get_current_char_index() 交叉验证，
        只有两者一致或至少一方强确认时，才认定前台角色。
        """
        self._pre_scan_count += 1

        # 方法1: is_current_char 标志检测（来自 get_current_char()）
        char_idx = current_char.index if current_char is not None else -1
        # 方法2: 独立图像检测（来自 CharUIMixin）
        detected_idx = self.task.get_current_char_index()

        if char_idx >= 0 and detected_idx >= 0 and char_idx == detected_idx:
            # 双路一致：标志 + 图像检测都指向同一角色 → 最高置信度
            if self._is_current_char(char_idx):
                self._confirmed_front_idx = char_idx
        elif detected_idx >= 0 and self._is_current_char(detected_idx):
            # 仅图像检测成功（标志可能尚未更新）
            self._confirmed_front_idx = detected_idx
        elif char_idx >= 0 and self._is_current_char(char_idx):
            # 仅标志检测成功（图像检测暂时失败）
            self._confirmed_front_idx = char_idx

        if self._pre_scan_count >= self._pre_scan_frames:
            self._pre_scan_done = True

    def _log_pre_scan_result(self):
        """打印预检结果日志（各槽位的能量/大招状态 + 前台角色）。"""
        # 确认前台实际角色
        front_char = None
        if self._confirmed_front_idx >= 0:
            for c in self.task.chars:
                if c is not None and c.index == self._confirmed_front_idx:
                    front_char = c
                    break

        info_lines = ["[核心层] === 战斗预检结果 ==="]
        front_slot = self._confirmed_front_idx + 1 if self._confirmed_front_idx >= 0 else "?"
        info_lines.append(f"  前台确认: {front_char or '未识别'} (slot{front_slot})")
        for c in self.task.chars:
            if c is not None:
                idx = c.index
                elem = self._get_char_element(c)
                has_e = self._slot_energy.get(idx, False)
                has_q = self._slot_ultimate.get(idx, False)
                anchor_flag = " | 锚点" if c.is_anchor() else ""
                info_lines.append(
                    f"  Slot{idx + 1}: {c} ({elem}) "
                    f"能量={'●' if has_e else '○'} "
                    f"大招={'●' if has_q else '○'}{anchor_flag}"
                )
        for line in info_lines:
            self.logger.info(line)

    def _dynamic_start_decision(self):
        """根据预检结果动态调整首发：确认前台角色后决策。

        决策顺序（从高到低）：
        1. 满能量 + 触发浊燃（primary pair，如失谐队优先浊燃/RED）
        2. 任意满能量 → 触发环合反应（_quick_swap_with_intro 内自带 Q/E）
        3. 有Q无能量 → 纯Q释放
        """
        if self.anchor_char is None:
            return

        # 确认前台实际角色（预检时双重检测的成果）
        actual_front = None
        if self._confirmed_front_idx >= 0:
            for c in self.task.chars:
                if c is not None and c.index == self._confirmed_front_idx:
                    actual_front = c
                    break

        # 预检确认的前台可能和 current_char 不一致
        if actual_front is None:
            actual_front = self.anchor_char
        self.logger.info(f"[核心层] 前台确认: {actual_front}, 锚点: {self.anchor_char}")

        # 如果前台不是锚点，先切到锚点稳定局面
        if actual_front is not self.anchor_char:
            self.logger.info(f"[核心层] 前台非锚点，先切回: {actual_front} -> {self.anchor_char}")
            self._switch_to(actual_front, self.anchor_char)
            self._report_synergy_on_switch(actual_front, self.anchor_char)

        # 获取体系信息，确定浊燃目标元素
        my_elem = self._get_char_element(self.anchor_char)
        team_elements = {self._get_char_element(c) for c in self.task.chars if c}
        system_name = detect_team_system(team_elements)
        info = get_system_info(system_name)

        primary_target_elem = None
        if info and not self._synergy_a_active:
            primary = info.get("primary_pair")  # e.g. frozenset({Red, Purple})
            if primary:
                for e in primary:
                    if e != my_elem:
                        primary_target_elem = e
                        break

        # 满能量 + 触发浊燃（如失谐队优先浊燃/RED）
        if primary_target_elem is not None:
            for c in self.task.chars:
                if c is None or c is self.anchor_char:
                    continue
                if self._get_char_element(c) == primary_target_elem and self._slot_energy.get(
                    c.index, False
                ):
                    self.logger.info(f"[核心层] 预检首发: {c} 能量满，优先触发浊燃")
                    self._quick_swap_with_intro(self.anchor_char, c)
                    return

        # 任意满能量 → 触发环合反应
        for c in self.task.chars:
            if c is None or c is self.anchor_char:
                continue
            if self._slot_energy.get(c.index, False):
                self.logger.info(f"[核心层] 预检首发: {c} 能量满，先切去触发环合")
                self._quick_swap_with_intro(self.anchor_char, c)
                return

        # 有Q无能量 → 纯Q释放
        for c in self.task.chars:
            if c is None or c is self.anchor_char:
                continue
            if self._slot_ultimate.get(c.index, False):
                self.logger.info(f"[核心层] 预检首发: {c} 大招可用，先切过去释放")
                self._quick_swap_action(self.anchor_char, c, "q")
                return

    # ===== 后台大招 =====

    def _handle_backline_ultimate(self, current_char: "BaseChar") -> bool:
        backline = []
        now = time.time()
        for c in self.task.chars:
            if c is None or c is current_char:
                continue
            last_time = self._backline_ult_timestamps.get(c.index, 0)
            if now - last_time < self.BACKLINE_ULT_COOLDOWN:
                continue
            if self.task.has_ultimate_visual(c.index):
                backline.append(c)

        if not backline:
            return False

        self.logger.info(f"[核心层] 后台Q: {len(backline)}个队友, 释放")
        for c in backline:
            self._backline_ult_timestamps[c.index] = now
            has_energy = self.task.char_energy.get(c.index, (False,))[0]
            if has_energy:
                # 既有能量又有Q：先消耗能量触发环合，再放Q
                self.logger.info(f"[核心层] 后台Q+能量: {c} 满能量，先消耗再Q")
                self._quick_swap_synergy_then_q(current_char, c)
            else:
                # 纯Q释放（仅Q，不消耗能量）
                self._quick_swap_action(current_char, c, "q")
            self._energy_dirty = True
        return True

    # ===== 环合反应 =====

    def _handle_synergy_reactions(self, current_char: "BaseChar") -> bool:
        """纯属性驱动：通过 SynergyRule.get_energy_decision() 统一决策。

        将当前主C元素、满能队友集合、浊燃剩余时间传入决策引擎，
        根据返回的 switch_blue / switch_red / switch_energy 指令执行物理切人。
        """
        # 浊燃过期清理：防止状态残留导致错误走黯星路径
        if self._synergy_a_active and self._get_synergy_a_remaining() <= 0:
            self._synergy_a_active = False
            self._window_synergy_done = False
            self._synergy_b_count = 0
            self._synergy_a_start_time = 0.0
            self.logger.info("[核心层] 浊燃已过期，清除黯星标记")

        my_elem = self._get_char_element(current_char)
        if my_elem == Element.DEFAULT:
            return False

        # 安魂曲变身E：特殊决策路径 —— 跳过正常引擎，直接找相邻元素队友
        # 变身E逻辑需在锚点能量检查前处理，不消耗环合能量
        # 浊燃窗口规则：≥5s切右邻(黯星)，<5s切左邻(刷新浊燃)
        if (
            hasattr(current_char, "transform_e_active")
            and current_char.transform_e_active
            and not self._window_synergy_done
        ):
            target = self._get_transform_e_target(current_char)
            if target is None:
                return False
            self._quick_swap_transform_e(current_char, target)
            self._window_synergy_done = True
            self._energy_dirty = True
            self._report_synergy_on_switch(target, current_char)
            return True

        # 正常路径：收集满能量队友的元素集合
        full_energy_elements: set[Element] = set()
        my_has_energy = (
            self.task.char_energy.get(current_char.index, (False,))[0]
            or current_char.is_cycle_full()
        )

        if my_has_energy:
            full_energy_elements.add(my_elem)

        for c in self.task.chars:
            if c is None or c is current_char:
                continue
            if self.task.char_energy.get(c.index, (False,))[0]:
                c_elem = self._get_char_element(c)
                if c_elem != Element.DEFAULT:
                    full_energy_elements.add(c_elem)

        # 提前检测体系，确保 b_window 使用正确的持续时间
        team_elements = {self._get_char_element(c) for c in self.task.chars if c}
        system_name = detect_team_system(team_elements)
        durations = get_system_durations(system_name)
        self._system_primary_duration = durations["primary_duration"]
        self._system_secondary_duration = durations["secondary_duration"]

        # 调用决策引擎
        decision = get_energy_decision(
            anchor_element=my_elem,
            full_energy_elements=full_energy_elements,
            synergy_a_remaining=self._get_synergy_a_remaining(),
            synergy_a_active=self._synergy_a_active,
            window_synergy_done=self._window_synergy_done,
            b_window=self._system_secondary_duration,
        )

        if decision["action"] == "do_nothing":
            return False

        target_element = decision["target_element"]
        if target_element is None:
            return False

        # 能量兜底：当前角色无能量时，先找同元素队友跳板消耗
        if not my_has_energy:
            energy_target = self._get_energy_teammate(my_elem, exclude=current_char)
            if energy_target is not None:
                self.logger.info(
                    f"[核心层] 能量兜底: {current_char}({my_elem}) -> {energy_target}(同元素满能量)"
                )
                self._quick_swap_stepping_stone(current_char, energy_target)
                self._window_synergy_done = True
                self._energy_dirty = True
                return True

        # 同元素满能量处理（消耗被切走角色的能量触发环合）
        if target_element == my_elem:
            if my_has_energy:
                # Case 1: 前台角色自己有能量 → 直接切相邻元素触发环合
                left_adj, right_adj = get_ring_adjacent(my_elem)
                if not self._synergy_a_active:
                    target = self._get_teammate_by_element(left_adj, exclude=current_char)
                    reason = f"{my_elem}满能量，切左邻({left_adj})触发浊燃"
                elif self._get_synergy_a_remaining() >= self._system_secondary_duration:
                    target = self._get_teammate_by_element(right_adj, exclude=current_char)
                    reason = (
                        f"{my_elem}满能量，浊燃≥{self._system_secondary_duration}s，"
                        f"切右邻({right_adj})触发黯星"
                    )
                else:
                    target = self._get_teammate_by_element(left_adj, exclude=current_char)
                    reason = (
                        f"{my_elem}满能量，浊燃<{self._system_secondary_duration}s，"
                        f"切左邻({left_adj})刷新浊燃"
                    )
                if target is None:
                    return False
                self.logger.info(f"[核心层] {reason}: {current_char} -> {target}")
                self._quick_swap_with_intro(current_char, target)
                self._window_synergy_done = True
                self._energy_dirty = True
                return True
            else:
                # Case 2: 前台无能量，同元素队友有能量 → 跳板式切换
                target = self._get_energy_teammate(target_element, exclude=current_char)
                if target is None:
                    return False
                self.logger.info(f"[核心层] 跳板: {current_char} -> {target}(同元素能量满)")
                self._quick_swap_stepping_stone(current_char, target)
                self._window_synergy_done = True
                self._energy_dirty = True
                return True

        # 优先找有能量的队友，退而求其次找任意的
        target = self._get_energy_teammate(target_element, exclude=current_char)
        if target is None:
            target = self._get_teammate_by_element(target_element, exclude=current_char)
        if target is None:
            return False

        # 即使当前角色无能量，也执行切换：目标角色有能量，
        # 在 _switch_back_to() 切回锚点时消耗目标能量触发环合
        self.logger.info(
            f"[核心层] {decision['reason']}: "
            f"{current_char}({my_elem}) -> {target}({target_element})"
        )

        self._quick_swap_with_intro(current_char, target)
        # 窗口已执行动作，防止下帧重复触发（所有路径都需要）
        self._window_synergy_done = True
        self._energy_dirty = True
        return True

    # ===== 摔炮切换 =====

    def _quick_swap_action(self, current_char: "BaseChar", target: "BaseChar", action: str = "q"):
        """摔炮：切到队友 → 放技能 → 无条件切回锚点。"""
        self._switch_to(current_char, target)
        if action == "q":
            target.click_ultimate()
        elif action == "e":
            if target.skill_available():
                target.click_skill(time_out=0.3)
        self._switch_back_to()

    def _quick_swap_with_intro(self, current_char: "BaseChar", target: "BaseChar"):
        """摔炮+入场技：切到队友 → 入场技 → Q/E → 无条件切回锚点。"""
        self._switch_to(current_char, target)
        # 第一次切换消耗了 current_char 的能量，报告环合反应
        self._report_synergy_on_switch(current_char, target)
        target.wait_intro()
        self._try_quick_actions(target)
        self._switch_back_to()

    def _try_quick_actions(self, target: "BaseChar"):
        """摔炮动作：有Q放Q，有E放E。不放普通攻击。"""
        if target.ultimate_available():
            target.click_ultimate()
        if target.skill_available():
            target.click_skill(time_out=0.3)

    def _quick_swap_transform_e(self, current_char: "BaseChar", target: "BaseChar"):
        """安魂曲变身E速切：切到目标 -> 入场技 -> Q/E -> 普攻+轮询切回。

        整个流程（切出+技能+轮询切回）都在非阻塞模式下执行，
        闪避不会打断切换流程，变身E窗口不会被耗尽。
        """
        # 非阻塞模式覆盖整个变身E速切流程
        SoundCombatContext.enter_non_blocking()
        try:
            self._switch_to(current_char, target)
            target.wait_intro()
            self._try_quick_actions(target)

            anchor = self.anchor_char
            if anchor is None:
                return

            self.logger.info(f"[核心层] 变身E: 轮询切回{anchor}")

            # 轮询：目标打普攻 + 持续尝试切回安魂曲，直到成功为止
            while True:
                self.task.next_frame()
                self.task.check_combat()
                if self._is_current_char(anchor.index):
                    self.logger.info("[核心层] 变身E: 成功切回安魂曲")
                    # 关闭变身窗口，防止下帧重复触发
                    if hasattr(anchor, "_close_transform_window"):
                        anchor._close_transform_window()
                    return
                # 目标不空转，持续普攻
                target.click()
                # 发送切回按键
                self.task.send_key(anchor.index + 1)
                self.task.click()
                self.task.sleep(self.LOOP_TICK)
        finally:
            SoundCombatContext.exit_non_blocking()

    def _quick_swap_stepping_stone(self, current_char: "BaseChar", stepping_stone: "BaseChar"):
        """跳板式切换：同元素队友消耗自身能量切相邻元素触发环合。

        流程：当前角色 → 同元素跳板(不触发反应) → 目标元素(消耗跳板能量触发环合)
        """
        my_elem = self._get_char_element(stepping_stone)
        left_adj, right_adj = get_ring_adjacent(my_elem)
        if not self._synergy_a_active:
            # 无浊燃 → 切左邻开启浊燃
            target_elem = left_adj
        elif self._get_synergy_a_remaining() >= self._system_secondary_duration:
            # 浊燃 ≥ b_window → 切右邻触发黯星
            target_elem = right_adj
        else:
            # 浊燃 < b_window → 切左邻刷新浊燃
            target_elem = left_adj

        target = self._get_teammate_by_element(target_elem, exclude=stepping_stone)
        if target is None:
            self._switch_to(current_char, stepping_stone)
            self._switch_back_to()
            return

        self.logger.info(
            f"[核心层] 跳板消耗: {stepping_stone}({my_elem}) -> {target}({target_elem})"
        )

        # Step 1: 从当前角色切到跳板（定位，不消耗能量）
        self._switch_to(current_char, stepping_stone)
        # Step 2: 跳板切到目标元素（消耗跳板能量，触发环合）
        self._switch_to(stepping_stone, target)
        target.wait_intro()
        self._try_quick_actions(target)
        self._report_synergy_on_switch(stepping_stone, target)
        # 跳板释放完成，切回锚点
        self._switch_back_to()

    def _quick_swap_synergy_then_q(self, current_char: "BaseChar", target: "BaseChar"):
        """既有能量又有Q：先消耗能量触发环合，再释放Q。

        流程：当前 → 目标(有能量+Q) → 邻元素(消耗目标能量触发环合)
              → 回目标 → Q → 回锚点
        """
        my_elem = self._get_char_element(target)
        left_adj, right_adj = get_ring_adjacent(my_elem)
        # 确定消耗能量的目标元素，遵守浊燃窗口规则
        if not self._synergy_a_active:
            adj_elem = left_adj  # 无浊燃 → 切左邻开启浊燃
        elif self._get_synergy_a_remaining() >= self._system_secondary_duration:
            adj_elem = right_adj  # 浊燃 ≥ b_window → 切右邻触发黯星
        else:
            adj_elem = left_adj  # 浊燃 < b_window → 切左邻刷新浊燃

        adj_target = self._get_teammate_by_element(adj_elem, exclude=target)

        # Step 1: 从当前切到目标
        self._switch_to(current_char, target)

        if adj_target is not None:
            # Step 2: 目标 → 邻元素（消耗能量触发环合）
            self._switch_to(target, adj_target)
            self._report_synergy_on_switch(target, adj_target)
            adj_target.wait_intro()
            self._try_quick_actions(adj_target)

            # Step 3: 邻元素 → 回目标放Q
            self._switch_to(adj_target, target)
            target.wait_intro()
        # else: 无相邻元素可消耗能量，直接Q

        # Step 4: 放Q
        if target.ultimate_available():
            target.click_ultimate()

        # Step 5: 切回锚点
        self._switch_back_to()

    # ===== 切换工具 =====

    def _switch_to(self, current_char: "BaseChar", target: "BaseChar"):
        """切换到目标角色，通过角色头像下方红色标识确认。"""
        self.logger.info(f"[核心层] 切换: {current_char} -> {target}")
        switch_start = time.time()
        while time.time() - switch_start < self.SWITCH_TIMEOUT * 2:
            self.task.next_frame()
            self.task.check_combat()
            # 通过角色头像下方红色标识确认当前在场角色
            if self.task.is_char_at_index(target.index):
                for c in self.task.chars:
                    if c is not None:
                        c.is_current_char = c.index == target.index
                return
            # 先点击窗口激活，再发送切换键
            self.task.click()
            self.task.sleep(0.02)
            self.task.send_key(target.index + 1)
            self.task.sleep(self.LOOP_TICK)
        self.logger.warning(f"[核心层] 切换超时: {current_char} -> {target}")

    def _switch_back_to(self):
        """切回锚点角色（主C强锁定）。无条件切回 self.anchor_char。"""
        if self.anchor_char is None:
            return
        current = self.task.get_current_char(raise_exception=False)
        if current is self.anchor_char:
            return
        self._switch_to(current or self.anchor_char, self.anchor_char)
        self.anchor_char.wait_intro()

    def _is_current_char(self, index: int) -> bool:
        """检测当前角色是否为目标index。
        优先使用 get_current_char_index（不受 threshold 限制），
        回退到 is_char_at_index（低置信度场景）。
        """
        detected = self.task.get_current_char_index()
        if detected >= 0:
            return detected == index
        return bool(self.task.is_char_at_index(index))

    # ===== 环合状态追踪 =====

    def _report_synergy_on_switch(self, source: "BaseChar", target: "BaseChar"):
        """报告环合反应触发，更新状态追踪。

        由系统配置驱动，无硬编码元素对。
        """
        s_elem = self._get_char_element(source)
        t_elem = self._get_char_element(target)
        if s_elem == Element.DEFAULT or t_elem == Element.DEFAULT:
            return

        name = get_synergy_pair_name(s_elem, t_elem)
        if name is None:
            return

        self.logger.info(f"[核心层] 环合反应: {name} ({s_elem}↔{t_elem})")

        # 检测所属体系
        team_elements = {self._get_char_element(c) for c in self.task.chars if c}
        system_name = detect_team_system(team_elements)
        self._track_system_reaction(system_name, s_elem, t_elem)

    def _track_system_reaction(self, system_name: str, elem_a: Element, elem_b: Element):
        """通用体系状态追踪，由配置驱动。

        根据体系配置中的 primary_pair / secondary_pair 判定反应类型。
        持续时间从 JSON 配置动态加载。
        """
        info = get_system_info(system_name)
        if info is None:
            return

        pair = frozenset({elem_a, elem_b})
        primary = info.get("primary_pair")
        secondary = info.get("secondary_pair")

        # 加载体系持续时间配置
        durations = get_system_durations(system_name)
        self._system_primary_duration = durations["primary_duration"]
        self._system_secondary_duration = durations["secondary_duration"]

        if pair == primary and primary is not None:
            # 判定是否失谐：上次黯星触发时间在 secondary_duration 内
            is_dissonance = False
            if self._last_secondary_time > 0:
                sec_elapsed = self.task.time_elapsed_accounting_for_freeze(
                    self._last_secondary_time
                )
                if sec_elapsed <= self._system_secondary_duration:
                    is_dissonance = True
            if not self._synergy_a_active:
                self._synergy_a_start_time = time.time()
                self._synergy_a_active = True
                self._window_synergy_done = False
                self._synergy_b_count = 0
                tag = "+失谐" if is_dissonance else ""
                self.logger.info(f"[核心层] 浊燃{tag}, 持续{self._system_primary_duration}s")
            else:
                self._synergy_a_start_time = time.time()
                self._window_synergy_done = False
                self._synergy_b_count = 0
                tag = "+失谐" if is_dissonance else ""
                self.logger.info(
                    f"[核心层] 浊燃{tag}刷新, 重新计时{self._system_primary_duration}s"
                )
        elif pair == secondary and secondary is not None:
            self._last_secondary_time = time.time()
            if self._synergy_a_active:
                self._synergy_b_count += 1
                self.logger.info(f"[核心层] 黯星+失谐 (第{self._synergy_b_count}次)")
            else:
                self.logger.info("[核心层] 黯星 (等待浊燃激活)")

    def _get_synergy_a_remaining(self) -> float:
        """获取浊燃剩余时间（扣除大招动画时停）。"""
        if not self._synergy_a_active or self._synergy_a_start_time <= 0:
            return 0
        elapsed = self.task.time_elapsed_accounting_for_freeze(self._synergy_a_start_time)
        remaining = self._system_primary_duration - elapsed
        return max(0, remaining)

    # ===== 属性工具 =====

    def _detect_anchor(self, current_char: "BaseChar"):
        """从队伍中检测并锁定锚点角色（主C）。"""
        for c in self.task.chars:
            if c is not None and c.is_anchor():
                self.anchor_char = c
                self.logger.info(f"[核心层] 锁定锚点: {c.name}")
                return
        # 兜底：使用当前角色作为锚点
        self.anchor_char = current_char
        self.logger.info(f"[核心层] 兜底锚点: {current_char}")

    def _get_teammate_by_element(
        self, element: Element, exclude: "BaseChar | None" = None
    ) -> "BaseChar | None":
        """根据元素查找队伍中对应角色（排除 exclude 指定的角色）。"""
        for c in self.task.chars:
            if c is None or c is exclude:
                continue
            if self._get_char_element(c) == element:
                return c
        return None

    def _get_energy_teammate(
        self, element: Element, exclude: "BaseChar | None" = None
    ) -> "BaseChar | None":
        """找元素匹配且当前有满能量的具体队友（使用每帧实时 char_energy）。

        用于同元素满能量时定位到有能量的那个队友，
        而不是随便找一个同元素角色。
        """
        for c in self.task.chars:
            if c is None or c is exclude:
                continue
            if self._get_char_element(c) != element:
                continue
            if self.task.char_energy.get(c.index, (False,))[0]:
                return c
        return None

    def _get_transform_e_target(self, current_char: "BaseChar") -> "BaseChar | None":
        """变身E专用：找相邻元素队友，遵守浊燃窗口规则。

        浊燃≥5s → 切右邻触发黯星
        浊燃<5s → 切左邻刷新浊燃
        无浊燃  → 切左邻开启浊燃
        """
        my_elem = self._get_char_element(current_char)
        ring = [
            Element.WHITE,
            Element.GREEN,
            Element.RED,
            Element.PURPLE,
            Element.BLUE,
            Element.YELLOW,
        ]
        try:
            idx = ring.index(my_elem)
        except ValueError:
            return None

        left_elem = ring[idx - 1] if idx > 0 else ring[-1]
        right_elem = ring[idx + 1] if idx < len(ring) - 1 else ring[0]

        # 收集左右邻队友
        left_candidates = []
        right_candidates = []
        for c in self.task.chars:
            if c is None or c is current_char:
                continue
            elem = self._get_char_element(c)
            if elem == left_elem:
                left_candidates.append(c)
            elif elem == right_elem:
                right_candidates.append(c)

        # 排序：有Q优先（变身E不需要任何人的环合能量）
        def _sort_key(c):
            return c.ultimate_available()

        left_candidates.sort(key=_sort_key, reverse=True)
        right_candidates.sort(key=_sort_key, reverse=True)

        # 浊燃窗口规则：决定优先左邻还是右邻
        if self._synergy_a_active:
            remaining = self._get_synergy_a_remaining()
            if remaining >= self._system_secondary_duration:
                # 浊燃 ≥ b_window → 切右邻触发黯星
                preferred = right_candidates + left_candidates
                reason = f"浊燃≥{self._system_secondary_duration}s，优先右邻"
            else:
                # 浊燃 < b_window → 切左邻刷新浊燃
                preferred = left_candidates + right_candidates
                reason = f"浊燃<{self._system_secondary_duration}s，优先左邻刷新"
        else:
            # 无浊燃 → 切左邻开启浊燃
            preferred = left_candidates + right_candidates
            reason = "无浊燃，优先左邻开启浊燃"

        if not preferred:
            return None

        target = preferred[0]
        self.logger.info(
            f"[核心层] 变身E目标: {target} ({reason}) "
            f"(Q={'是' if target.ultimate_available() else '否'})"
        )
        return target

    @staticmethod
    def _get_char_element(c: "BaseChar") -> Element:
        """获取角色元素。"""
        if c.element and c.element != Element.DEFAULT:
            return c.element
        return Element.DEFAULT

    # ===== 状态管理 =====

    def reset(self):
        """重置所有状态（含预检，热键后调用触发重新检测）。"""
        self._backline_ult_timestamps.clear()
        self._ult_cooldown.clear()
        self.anchor_char = None
        self._synergy_a_active = False
        self._synergy_a_start_time = 0.0
        self._synergy_b_count = 0
        self._last_secondary_time = 0.0
        self._window_synergy_done = False
        self._system_primary_duration = 0.0
        self._system_secondary_duration = 0.0
        self._pre_scan_done = False
        self._pre_scan_count = 0
        self._confirmed_front_idx = -1
        self._slot_energy.clear()
        self._slot_ultimate.clear()

    def on_combat_end(self):
        """战斗结束清理。"""
        self._backline_ult_timestamps.clear()
        self._ult_cooldown.clear()
        self.anchor_char = None
        self._synergy_a_active = False
        self._synergy_a_start_time = 0.0
        self._synergy_b_count = 0
        self._last_secondary_time = 0.0
        self._window_synergy_done = False
        self._system_primary_duration = 0.0
        self._system_secondary_duration = 0.0
        self._pre_scan_done = False
        self._pre_scan_count = 0
        self._confirmed_front_idx = -1
        self._slot_energy.clear()
        self._slot_ultimate.clear()
        for c in self.task.chars:
            if c is not None and hasattr(c, "on_combat_end"):
                c.on_combat_end(self.task.chars)

    # ===== 反击事件分发 =====
