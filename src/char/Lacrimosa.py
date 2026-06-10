"""失谐体系 — 安魂曲 (Lacrimosa)
Purple 元素锚点角色，主C驻场输出。
E 技能判定方式：按键后等 CD 稳定，OCR 读数 >= 14 为变身E，否则为普通E。
"""

import time

from src.char.BaseChar import BaseChar


class Lacrimosa(BaseChar):
    """安魂曲 — 失谐体系锚点 (Purple/黯星)"""

    E_CD_STABLE_TIMEOUT = 1.0
    E_CD_CONFIRM_TICK = 0.03
    TRANSFORM_E_FREEZE = 1.5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._transform_active = False
        self._transform_start = 0.0
        self._transform_e_time = 0.0
        self._last_e_animation_time = 0.0

    @property
    def transform_e_active(self) -> bool:
        if not self._transform_active:
            return False
        elapsed = self.task.time_elapsed_accounting_for_freeze(self._transform_start)
        return elapsed < 5.0

    @property
    def transform_e_remaining(self) -> float:
        if not self._transform_active:
            return 0.0
        elapsed = self.task.time_elapsed_accounting_for_freeze(self._transform_start)
        return max(0.0, 5.0 - elapsed)

    def is_anchor(self) -> bool:
        return True

    def record_e_cast(self):
        self._transform_e_time = time.time()
        self.add_freeze_duration(time.time(), self.TRANSFORM_E_FREEZE)

    def _on_special_e_cast(self):
        self._last_e_animation_time = time.time()

    def _wait_for_stable_cd(self, timeout=None, tick=None) -> float:
        """等待 CD 数字稳定（>0），返回 CD 数值。"""
        timeout = timeout or self.E_CD_STABLE_TIMEOUT
        tick = tick or self.E_CD_CONFIRM_TICK
        start = time.time()
        while time.time() - start < timeout:
            self.task.next_frame()
            self.check_combat()
            cd_val = self.task.get_cd("skill")
            if cd_val > 0:
                return cd_val
            self.sleep(tick)
        cd_val = self.task.get_cd("skill")
        if cd_val < 0:
            return max(0.0, abs(cd_val))
        return cd_val if cd_val > 0 else 0.0

    def _is_transform_e(self, cd_val: float) -> bool:
        """变身E判定：CD >= 14 且距上次动画 >= 2s。"""
        if time.time() - self._last_e_animation_time < 2.0:
            return False
        return cd_val >= 14 or cd_val <= 0

    def click_skill(self, time_out=0.3, **kwargs):
        """E技能释放。按键后等 CD 稳定，OCR 判定变身E/普通E。"""
        result = super().click_skill(time_out=time_out, **kwargs)
        clicked = result[0] if result else False

        if not clicked:
            return result

        cd_val = self._wait_for_stable_cd()
        if self._is_transform_e(cd_val):
            self._transform_active = True
            self._transform_start = time.time()
            self.record_e_cast()
            self._on_special_e_cast()
            self.logger.info(f"[安魂曲] 变身E (CD={cd_val:.1f}s)")
        else:
            self._transform_e_time = time.time()
            self.logger.info(f"[安魂曲] 普通E (CD={cd_val:.1f}s)")

        return result

    def _close_transform_window(self):
        if self._transform_active:
            self._transform_active = False
            self.logger.info("[安魂曲] 变身窗口关闭")

    def do_perform(self):
        self.wait_intro()
        if self.click_ultimate():
            return
        if self.click_skill(time_out=0.3)[0]:
            return
        self.continues_normal_attack(0.1)
        self.switch_next_char()

    def reset_state(self):
        super().reset_state()
        self._transform_active = False
        self._transform_start = 0.0
        self._transform_e_time = 0.0
        self._last_e_animation_time = 0.0

    def on_combat_end(self, chars):
        self._transform_active = False
        self._transform_start = 0.0
        self._transform_e_time = 0.0
        self._last_e_animation_time = 0.0
