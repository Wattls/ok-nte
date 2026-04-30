# Test case
import unittest
import time

from src.config import config
from ok.test.TaskTestCase import TaskTestCase

from src.tasks.trigger.AutoCombatTask import AutoCombatTask


class TestCurrentChar(TaskTestCase):
    task_class = AutoCombatTask

    config = config

    def test_current_char3(self):
        self.set_image('tests/images/01.png')
        self.task.in_team()
        result = self.task.is_char_at_index(2)
        self.assertEqual(result, True)
    
    def test_current_char2(self):
        self.set_image('tests/images/02.png')
        self.task.in_team()
        result = self.task.is_char_at_index(1)
        self.assertEqual(result, True)

if __name__ == '__main__':
    unittest.main()
