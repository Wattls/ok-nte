# Test case
import unittest
import time

from src.config import config
from ok.test.TaskTestCase import TaskTestCase
from ok import og

from src.combat.BaseCombatTask import BaseCombatTask


class TestConFull(TaskTestCase):
    task_class = BaseCombatTask

    config = config

    def test_con_1(self):
        # Create a BattleReport object
        self.set_image('tests/images/05.png')
        result = self.task.get_all_avatar_vibrate()
        self.logger.info(f'con_1 {result}')
        self.assertDictEqual(result, {
            1: False,
            2: False,
            3: True,
            4: False,
        })
        time.sleep(1)
        self.task.screenshot('test_con_1', show_box=True)

    def test_con_2(self):
        og.ok.screenshot.ui_dict.clear()
        # Create a BattleReport object
        self.set_image('tests/images/06.png')
        result = self.task.get_all_avatar_vibrate()
        self.logger.info(f'con_2 {result}')
        self.assertDictEqual(result, {
            1: True,
            2: True,
            3: False,
            4: True,
        })
        time.sleep(1)
        self.task.screenshot('test_con_2', show_box=True)

    def test_con_3(self):
        og.ok.screenshot.ui_dict.clear()
        # Create a BattleReport object
        self.set_image('tests/images/07.png')
        result = self.task.get_all_avatar_vibrate()
        self.logger.info(f'con_3 {result}')
        self.assertDictEqual(result, {
            1: True,
            2: False,
            3: False,
            4: False,
        })
        time.sleep(1)
        self.task.screenshot('test_con_3', show_box=True)


if __name__ == '__main__':
    unittest.main()
