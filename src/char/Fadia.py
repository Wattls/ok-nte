"""
失谐体系 — 法帝娅 (Fadia)
Blue 元素摔炮角色，出招模式：E 后放 Q，或仅 Q。
与通用 Q→E 模式不同，E 技能可能为 Q 提供前置增益。
"""

from src.char.BaseChar import BaseChar


class Fadia(BaseChar):
    """法帝娅 — 失谐体系摔炮 (Blue/浸染)

    出招模式：先 E 后 Q，
    或仅释放 Q。CombatController 通过 _quick_swap_with_intro()
    管理切前台释放后切回锚点。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def do_perform(self):
        """出招循环：有 E 先放 E 后放 Q，无 E 只放 Q。"""
        self.wait_intro()
        # 法帝娅出招模式：先 E 后 Q
        if self.skill_available():
            if self.click_skill(time_out=0.3)[0]:
                # E 释放成功后接 Q
                if self.click_ultimate():
                    return
        # 无 E 或 E 释放失败时，直接 Q
        if self.click_ultimate():
            return
        self.continues_normal_attack(0.1)
