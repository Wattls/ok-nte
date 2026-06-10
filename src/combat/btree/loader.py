"""行为树加载器 — 构建默认树 + JSON 配置加载。

从 CombatController.perform() 的执行链映射为标准行为树结构。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.combat.btree.core import BTNode, Selector, Sequence
from src.combat.btree.nodes import (
    AnchorDetect,
    BacklineUltimate,
    EnergyRefresh,
    NormalAttack,
    PreScan,
    ResolveCurrentChar,
    SelfSkills,
    SynergyReactions,
    TransformEWindowGuard,
    WindowUnlock,
)

# ====================== 默认树 ======================


def build_default_tree() -> Sequence:
    """构建默认行为树 — 等价于当前 CombatController.perform() 逻辑。

    树结构:
        Sequence(CombatRoot)                    ← 每帧全部执行
        ├── EnergyRefresh                      ← 能量刷新（始终SUCCESS）
        ├── ResolveCurrentChar                 ← 角色解析（无角色→FAILURE停止）
        └── Selector(决策层)                    ← 选择器短路
              ├── PreScan                      ← 预检中→SUCCESS停止
              ├── AnchorDetect                 ← 首次→SUCCESS停止
              ├── SynergyReactions             ← 环合反应: 有动作→SUCCESS停止
              └── Sequence                     ← 环合反应失败→清理+后续
                    ├── WindowUnlock           ← 开放窗口锁
                    └── Selector
                          ├── SelfSkills                ← 自身技能
                          ├── TransformEWindowGuard     ← 变身E→跳过后续节点
                          ├── BacklineUltimate          ← 后台大招
                          └── NormalAttack              ← 普通攻击: 始终SUCCESS
    """
    tree = Sequence([
        EnergyRefresh(),
        ResolveCurrentChar(),
        Selector([
            PreScan(),
            AnchorDetect(),
            SynergyReactions(),
            Sequence([
                WindowUnlock(),
                Selector([
                    SelfSkills(),
                    TransformEWindowGuard(),
                    BacklineUltimate(),
                    NormalAttack(),
                ], name="PostSynergyDecisions"),
            ], name="PostSynergy"),
        ], name="Decisions"),
    ], name="CombatRoot")
    return tree


# ====================== JSON 加载器 ======================

# 节点名 → 类的注册表
_NODE_REGISTRY: dict[str, type[BTNode]] = {
    "PreScan": PreScan,
    "AnchorDetect": AnchorDetect,
    "EnergyRefresh": EnergyRefresh,
    "ResolveCurrentChar": ResolveCurrentChar,
    "SynergyReactions": SynergyReactions,
    "WindowUnlock": WindowUnlock,
    "SelfSkills": SelfSkills,
    "TransformEWindowGuard": TransformEWindowGuard,
    "BacklineUltimate": BacklineUltimate,
    "NormalAttack": NormalAttack,
}

_COMPOSITE_REGISTRY: dict[str, type[BTNode]] = {
    "Selector": Selector,
    "Sequence": Sequence,
}


def load_tree_from_json(path: str | Path) -> Selector:
    """从 JSON 配置文件加载行为树。

    JSON 格式示例:
    {
      "type": "Selector",
      "name": "CombatRoot",
      "children": [
        {"type": "Sequence", "name": "EnergyRefresh", "children": [
          {"type": "EnergyRefresh"}
        ]},
        {"type": "Sequence", "name": "SynergyReactions", "children": [
          {"type": "SynergyReactions"}
        ]},
        {"type": "Sequence", "name": "SelfSkills", "children": [
          {"type": "SelfSkills"}
        ]},
        {"type": "Sequence", "name": "NormalAttack", "children": [
          {"type": "NormalAttack"}
        ]}
      ]
    }
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _parse_node(data)


def _parse_node(data: dict) -> BTNode:
    """递归解析 JSON 节点。"""
    node_type = data.get("type", "")
    name = data.get("name", "")
    children_data = data.get("children", [])

    # 组合节点
    if node_type in _COMPOSITE_REGISTRY:
        cls = _COMPOSITE_REGISTRY[node_type]
        children = [_parse_node(c) for c in children_data]
        return cls(children, name=name)

    # 叶子节点
    if node_type in _NODE_REGISTRY:
        cls = _NODE_REGISTRY[node_type]
        return cls()

    raise ValueError(f"未知节点类型: {node_type}")