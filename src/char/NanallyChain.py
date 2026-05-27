import time

from src.char.Nanally import Nanally


class NanallyChain(Nanally):
    def do_perform(self):
        if self.task.chain_executor.active:
            self.continues_normal_attack(0.2)
            return
        self.wait_intro()
        self.click_skill()
        self.click_ultimate()

    def _try_initial_e_release(self):
        """尝试在开始时释放E技能."""
        if self.has_cd("skill"):
            self.logger.info("Nanally E in CD, skip initial release")
            return False
        e_deadline = time.time() + 2.0
        while time.time() < e_deadline:
            self.task.sleep_check()
            if self.skill_available() and self.click_skill():
                self.logger.info("Nanally E released for copy")
                return True
            self.click()
            self.sleep(0.05)
        return False

    def _try_q_after_e(self, e_time):
        """E释放后尝试释放Q."""
        q_deadline = time.time() + 1.0
        while time.time() < q_deadline:
            self.task.sleep_check()
            if self.ultimate_available() and time.time() - e_time >= 0.4:
                self.task._combat_settle.time = None
                self.click_ultimate()
                self.logger.info("Nanally Q released after E")
                return True
            self.click()
            self.sleep(0.05)
        return False

    def _standby_loop_iteration(self, hotori, last_e_time_ref):
        """单次待机循环, 输出并检查退出条件. 返回True表示应该退出."""
        if hotori and hotori.time_to_next_burst() <= 2.0:
            return True
        nanally_start = time.time()
        while time.time() - nanally_start < 1.2:
            self.task.sleep_check()
            if hotori and hotori.time_to_next_burst() <= 2.0:
                return True
            if self.task.is_char_at_index(self.index):
                if self.skill_available() and self.click_skill():
                    last_e_time_ref[0] = time.time()
                if self.ultimate_available() and time.time() - last_e_time_ref[0] >= 0.4:
                    self.task._combat_settle.time = None
                    self.click_ultimate()
                self.click()
            else:
                self.click()
                key = self._get_char_key("NanallyChain")
                if key:
                    self.task.send_key(key)
            self.sleep(0.05)
        return False

    def _switch_to_hotori_and_back(self, hotori):
        """切换到Hotori打一套普攻再切回来."""
        hotori_key = self._get_char_key("HotoriChain")
        if hotori_key:
            self.task.send_key(hotori_key)
            switch_start = time.time()
            while not self.task.is_char_at_index(hotori.index) and time.time() - switch_start < 1.0:
                self.task.sleep_check()
                self.click()
                self.sleep(0.05)
            if self.task.is_char_at_index(hotori.index):
                hotori_start = time.time()
                while time.time() - hotori_start < 0.8:
                    self.task.sleep_check()
                    hotori.click()
                    hotori.sleep(0.1)
                    self.task.next_frame()
        key = self._get_char_key("NanallyChain")
        if key:
            self.task.send_key(key)
            switch_back_start = time.time()
            while not self.task.is_char_at_index(self.index) and time.time() - switch_back_start < 1.0:
                self.task.sleep_check()
                self.click()
                self.sleep(0.05)

    def chain_dynamic_standby(self):
        hotori = next((c for c in self.task.chars if c.__class__.__name__ == "HotoriChain"), None)
        _last_e_time = 0

        if self._try_initial_e_release():
            _last_e_time = time.time()
            self._try_q_after_e(_last_e_time)

        last_e_time_ref = [_last_e_time]
        while True:
            self.task.sleep_check()
            if hotori and hotori.time_to_next_burst() <= 2.0:
                self.logger.info("Nanally bail, handing off to Jiuyuan")
                self.task.chain_executor.step_complete()
                self._send_chain_key()
                self.switch_next_char()
                return
            if self._standby_loop_iteration(hotori, last_e_time_ref):
                continue
            self._switch_to_hotori_and_back(hotori)
