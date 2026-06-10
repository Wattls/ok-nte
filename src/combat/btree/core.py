"""行为树核心节点类型。

提供：
  - BTStatus: SUCCESS / FAILURE / RUNNING 三态返回值
  - BTNode:   抽象基类，tick(ctx) 为入口
  - Selector: 选择器，子节点依次执行，首个 SUCCESS 即停止
  - Sequence: 顺序节点（→），子节点依次执行，首个 FAILURE 即停止
  - Condition:条件叶子节点，包装 callable → bool
  - Action:   动作叶子节点，包装 callable → BTStatus
"""

from __future__ import annotations

import enum
import traceback
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable

from src.combat.BaseCombatTask import NotInCombatException

if TYPE_CHECKING:
    from src.combat.btree.context import BTContext


class BTStatus(enum.Enum):
    """行为树节点执行状态。"""
    SUCCESS = 1   # 执行成功（Selector 短路，Sequence 继续）
    FAILURE = 2   # 执行失败（Selector 继续，Sequence 短路）
    RUNNING = 3   # 仍在执行（多帧操作，需保持状态）


class BTNode(ABC):
    """行为树节点抽象基类。

    子类需实现 _tick(ctx) → BTStatus。
    框架层 tick() 提供统一的日志/异常保护。
    """

    def __init__(self, name: str = ""):
        self.name = name or self.__class__.__name__

    @abstractmethod
    def _tick(self, ctx: "BTContext") -> BTStatus:
        """子类实现：执行节点逻辑。"""

    def tick(self, ctx: "BTContext") -> BTStatus:
        """入口：带异常保护的执行。

        NotInCombatException / CharDeadException 向上透传，
        不在此层吞掉，确保战斗退出流程正确触发。
        """
        try:
            return self._tick(ctx)
        except NotInCombatException:
            raise
        except Exception:
            ctx.logger.error(f"[BT] 节点 {self.name} 异常\n{traceback.format_exc()}")
            return BTStatus.FAILURE

    def reset(self):
        """重置节点内部状态（RUNNING 中断时调用）。"""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name!r})"


# ====================== 组合节点 ======================


class Selector(BTNode):
    """选择器（Fallback）。

    依次 tick 子节点：
    - SUCCESS → 立即返回 SUCCESS（短路）
    - RUNNING → 立即返回 RUNNING（保持当前子节点）
    - FAILURE → 继续下一个子节点
    - 全部 FAILURE → 返回 FAILURE

    等价语义：子节点依次尝试，首个 SUCCESS 即返回
    """

    def __init__(self, children: list[BTNode] | None = None, name: str = ""):
        super().__init__(name or "Selector")
        self.children: list[BTNode] = children or []

    def _tick(self, ctx: "BTContext") -> BTStatus:
        for child in self.children:
            status = child.tick(ctx)
            if status in (BTStatus.SUCCESS, BTStatus.RUNNING):
                return status
        return BTStatus.FAILURE

    def reset(self):
        for c in self.children:
            c.reset()

    def add_child(self, child: BTNode) -> "Selector":
        self.children.append(child)
        return self


class Sequence(BTNode):
    """顺序节点。

    依次 tick 子节点：
    - SUCCESS → 继续下一个子节点
    - RUNNING → 立即返回 RUNNING
    - FAILURE → 立即返回 FAILURE（短路）
    - 全部 SUCCESS → 返回 SUCCESS

    等价语义：子节点依次执行，全部 SUCCESS 才返回 SUCCESS
    """

    def __init__(self, children: list[BTNode] | None = None, name: str = ""):
        super().__init__(name or "Sequence")
        self.children: list[BTNode] = children or []

    def _tick(self, ctx: "BTContext") -> BTStatus:
        for child in self.children:
            status = child.tick(ctx)
            if status in (BTStatus.FAILURE, BTStatus.RUNNING):
                return status
        return BTStatus.SUCCESS

    def reset(self):
        for c in self.children:
            c.reset()

    def add_child(self, child: BTNode) -> "Sequence":
        self.children.append(child)
        return self


# ====================== 叶子节点 ======================


class Condition(BTNode):
    """条件节点：包装一个返回 bool 的可调用对象。

    True → SUCCESS, False → FAILURE。
    """

    def __init__(
        self,
        name: str,
        fn: Callable[["BTContext"], bool],
        *,
        invert: bool = False,
    ):
        super().__init__(name)
        self._fn = fn
        self._invert = invert

    def _tick(self, ctx: "BTContext") -> BTStatus:
        result = self._fn(ctx)
        if self._invert:
            result = not result
        return BTStatus.SUCCESS if result else BTStatus.FAILURE


class Action(BTNode):
    """动作节点：包装一个返回 BTStatus 的可调用对象。

    两种用法：
    1. 传入 fn=lambda ctx: ... → 使用内置 _tick()
    2. 子类覆写 _tick(ctx) → 不传 fn，使用自定义逻辑
    """

    def __init__(
        self,
        name: str,
        fn: Callable[["BTContext"], BTStatus | bool] | None = None,
    ):
        super().__init__(name)
        self._fn = fn

    def _tick(self, ctx: "BTContext") -> BTStatus:
        if self._fn is None:
            raise NotImplementedError(
                f"Action 节点 {self.name} 未提供 fn 且未覆写 _tick()"
            )
        result = self._fn(ctx)
        if isinstance(result, bool):
            return BTStatus.SUCCESS if result else BTStatus.FAILURE
        return result