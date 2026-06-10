"""普通攻击攒能节点。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.combat.btree.core import Action, BTStatus

if TYPE_CHECKING:
    from src.combat.btree.context import BTContext


class NormalAttack(Action):
    """普通攻击攒能 + 角色钩子。

    总是返回 SUCCESS（兜底节点，作为 Selector 的最后一级）。
    """

    def __init__(self):
        super().__init__("NormalAttack")

    def _tick(self, ctx: "BTContext") -> BTStatus:
        cur = ctx.current_char
        if cur is None:
            return BTStatus.FAILURE

        cur.continues_normal_attack(0.1)

        # 角色主动管理钩子（如安魂曲普攻刷新普通E）
        if hasattr(cur, "_on_after_aa"):
            cur._on_after_aa()

        return BTStatus.SUCCESS