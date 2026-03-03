import time

from qfluentwidgets import FluentIcon

from ok import TriggerTask, Logger
from src.combat.BaseCombatTask import BaseCombatTask, NotInCombatException, CharDeadException

logger = Logger.get_logger(__name__)


class AutoCombatTask(BaseCombatTask, TriggerTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.trigger_interval = 0.1
        self.name = "Auto Combat"
        self.description = "Enable auto combat in Abyss, Game World etc"
        self.icon = FluentIcon.CALORIES
        self.last_is_click = False
        self.default_config.update({
            'Auto Target': True,
        })
        self.config_description = {
            'Auto Target': 'Turn off to enable auto combat only when manually target enemy using middle click',
        }
        self.op_index = 0
        self.origin_func = {}

    def run(self):
        ret = False
        if not self.scene.in_team(self.in_team_and_world):
            return

        self.toggle_single_character_mode()
        
        combat_start = time.time()
        while self.in_combat():
            ret = True
            try:
                self.get_current_char().perform()
                self.get_current_char().switch_next_char()
            except CharDeadException:
                self.log_error('Characters dead', notify=True)
                break
            except NotInCombatException as e:
                logger.info(f'auto_combat_task_out_of_combat {int(time.time() - combat_start)} {e}')
                break
        if ret:
            self.combat_end()