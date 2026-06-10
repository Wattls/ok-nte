"""自身Q/E技能节点。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from src.combat.btree.core import Action, BTStatus

if TYPE_CHECKING:
    from src.combat.btree.context import BTContext


class SelfSkills(Action):
    """自身Q/E技能 — Q冷却保护 + 大招 + E技能。

    返回 SUCCESS 表示技能已触发，FAILURE 表示无技能可用。
    """

    ULT_COOLDOWN = 5.0  # Q冷却窗口（秒），防止视觉滞后重复阻塞

    def __init__(self):
        super().__init__("SelfSkills")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        cur = ctx.current_char
        if cur is None:
            return BTStatus.FAILURE

        now = time.time()

        # Q冷却检查 + 释放
        if now - ctx.ult_cooldown.get(cur.index, 0) >= self.ULT_COOLDOWN:
            if cur.ultimate_available():
                ctx.ult_cooldown[cur.index] = now
                if cur.click_ultimate():
                    return BTStatus.SUCCESS
                # click_ultimate 返回 False，不 return，继续 E 技能

        # E技能
        if cur.click_skill(time_out=0.3)[0]:
            return BTStatus.SUCCESS

        return BTStatus.FAILURE