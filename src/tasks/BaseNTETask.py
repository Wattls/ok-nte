import re

from ok import BaseTask
from src.Labels import Labels


class BaseNTETask(BaseTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
