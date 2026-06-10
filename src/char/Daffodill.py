"""
NTE体系 达芙蒂尔 (Daffodill)
Purple 元素摔炮角色，可用于反击和摔炮应答。
特殊入场层管理：同人入场时积攒 E 强化，释放 Q 时恢复特殊入场层。
"""

from src.char.BaseChar import BaseChar, Element


class Daffodill(BaseChar):
    cn_name = "达芙蒂尔"
    DEFAULT_ELEMENT = Element.PURPLE

    SWITCH_TIMEOUT = 1.0
    Q_DEADLINE = 0.3
    POST_ACTION_PAUSE = 0.1
    MAX_E_STACKS = 2
    MAX_SPECIAL_INTRO_STACKS = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._e_stacks = 0
        self._special_intro_stacks = 0

    @property
    def e_stacks(self):
        return self._e_stacks

    @property
    def special_intro_stacks(self):
        return self._special_intro_stacks

    def on_teammate_intro(self):
        """同人入场时触发 E 强化层。"""
        if self._e_stacks < self.MAX_E_STACKS:
            self._e_stacks += 1
            self.logger.info("E强化 +1, 当前: {}/{}".format(self._e_stacks, self.MAX_E_STACKS))

    def on_ultimate_cast(self):
        """Q 释放时：特殊入场恢复 2 层，E 强化消耗 1 层。"""
        self._special_intro_stacks = self.MAX_SPECIAL_INTRO_STACKS
        if self._e_stacks > 0:
            self._e_stacks -= 1
        self.logger.info(
            "Q释放: 特殊入场恢复至{}, E强化消耗1层, 剩余: {}".format(
                self._special_intro_stacks, self._e_stacks
            )
        )

    def consume_special_intro(self):
        """消耗特殊入场层，返回是否成功。"""
        if self._special_intro_stacks > 0:
            self._special_intro_stacks -= 1
            self.logger.info("消耗1层特殊入场, 剩余: {}".format(self._special_intro_stacks))
            return True
        return False

    def consume_e_stack(self):
        """消耗 1 层 E 强化，返回是否成功。"""
        if self._e_stacks > 0:
            self._e_stacks -= 1
            self.logger.info("消耗1层E强化, 剩余: {}".format(self._e_stacks))
            return True
        return False

    def click_ultimate(self, send_click=True, wait_if_cd_ready=0.1):
        result = super().click_ultimate(send_click=send_click, wait_if_cd_ready=wait_if_cd_ready)
        if result:
            self.on_ultimate_cast()
        return result

    def reset_state(self):
        super().reset_state()
        self._e_stacks = 0
        self._special_intro_stacks = 0

    def on_combat_end(self, chars):
        self._e_stacks = 0
        self._special_intro_stacks = 0

    def handle_counter_attack(self):
        """处理反击音效触发的特殊入场反击。"""
        self.logger.info("[反击] 达芙蒂尔检测到反击音效，准备入场")

        if self.consume_special_intro():
            self.logger.info("[反击] 消耗特殊入场进行弹刀反击")
            return True

        self.logger.info("[反击] 无特殊入场层数，尝试释放Q/E")
        self.click_ultimate()
        self.click_skill(time_out=0.5)
        return True

    def do_perform(self):
        """兜底战斗循环：Q → E → 普攻。"""
        self.wait_intro()
        if self.click_ultimate():
            return
        if self.click_skill(time_out=0.3)[0]:
            return
        self.continues_normal_attack(0.1)
