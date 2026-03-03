from src.Labels import Labels

from typing import TYPE_CHECKING
from src.char.BaseChar import BaseChar

if TYPE_CHECKING:
    from src.combat.BaseCombatTask import BaseCombatTask

char_dict = {
    "char_default": {'cls': BaseChar},
}

char_names = char_dict.keys()


def get_char_by_pos(task: 'BaseCombatTask', box, index, old_char: BaseChar | None):
    highest_confidence = 0
    info = None
    name = "unknown"
    char = None
    if old_char and old_char.confidence > 0.92 and old_char.char_name in char_names:
        char = task.find_one(old_char.char_name, box=box, threshold=0.6)
        if char:
            return old_char
    # if not char:
    #     char = task.find_best_match_in_box(box, char_names, threshold=0.6)
    #     if char:
    #         info = char_dict.get(char.name)
    #         name = char.name
    #         cls: 'BaseChar' = info.get('cls')
    #         return cls(task, index, char_name=name, confidence=char.confidence)
    # task.log_info(f'could not find char {index} {info} {highest_confidence}')
    # if old_char:
    #     return old_char
    # if task.debug:
    #     task.screenshot(f'could not find char {index}')
    return BaseChar(task, index, char_name=name)

def get_char_feature_by_pos(task: 'BaseCombatTask', index):
    box = task.get_box_by_name(f'box_char_{index + 1}')
    return box.crop_frame(task.frame)

def is_float(s):
    try:
        float(s)
        return True
    except ValueError:
        return False
