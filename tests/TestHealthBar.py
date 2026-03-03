# Test case
import unittest
import time

from src.config import config
from ok.test.TaskTestCase import TaskTestCase

from src.combat.CombatCheck import CombatCheck


class TestHealthBar(TaskTestCase):
    task_class = CombatCheck

    config = config

    def test_enemy_health(self):
        # Create a BattleReport object
        self.set_image('tests/images/04.png')
        result = self.task.has_health_bar()
        self.logger.info(f'enemy_health: {result}')
        self.assertEqual(result, True)
        time.sleep(1)
        self.task.screenshot('enemy_health', show_box=True)

    def test_boss_health(self):
        # Create a BattleReport object
        self.set_image('tests/images/05.png')
        result = self.task.has_health_bar()
        self.logger.info(f'test2 boss_health: {result}')
        self.assertEqual(result, True)
        time.sleep(1)
        self.task.screenshot('boss_health', show_box=True)


if __name__ == '__main__':
    unittest.main()
