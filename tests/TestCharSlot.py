# Test case
import unittest
import time

from src.config import config
from ok.test.TaskTestCase import TaskTestCase

from src.tasks.trigger.AutoCombatTask import AutoCombatTask


class TestHealthBar(TaskTestCase):
    task_class = AutoCombatTask

    config = config

    def test_enemy_health(self):
        # Create a BattleReport object
        self.set_image('tests/images/05.png')
        self.task.has_char_slot_changed(2)
        self.set_image('tests/images/06.png')
        self.task.has_char_slot_changed(2)

if __name__ == '__main__':
    unittest.main()
