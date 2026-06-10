import time

from ok import Logger, TriggerTask
from qfluentwidgets import FluentIcon

from src.combat.BaseCombatTask import BaseCombatTask, CharDeadException, NotInCombatException
from src.combat.ChainLoader import ChainLoader

logger = Logger.get_logger(__name__)


class AutoCombatTask(BaseCombatTask, TriggerTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {"_enabled": True}
        self.trigger_interval = 0.1
        self.name = "自动战斗"
        self.description = "受《异环》UI的特殊性影响, 部分场景下存在识别稳定性波动"
        self.icon = FluentIcon.CALORIES
        self.last_is_click = False
        self.default_config.update(
            {
                "自动目标": True,
                "启用环合反应战斗": False,
                "行为树模式(实验性)": False,
            }
        )
        self.config_description.update(
            {
                "自动目标": "关闭时仅在中键选中敌人且画面识别到 'Lv' 文字时开启战斗",
                "启用环合反应战斗": "开启后自动管理环合反应体系（失谐/创生）。"
                "队伍匹配链式（浔-零-九原-娜娜莉）时自动执行链式，"
                "其余队伍自动执行环合反应。关闭则回退通用自动战斗",
                "行为树模式(实验性)": "【实验性】使用行为树节点替代线性执行流程。"
                "需同时开启「启用环合反应战斗」。默认关闭，开启后日志前缀为 [BT]。",
            }
        )
        self.op_index = 0
        self.origin_func = {}

    def run(self):
        ret = False

        if not self.scene.is_in_team(self.is_in_team):
            return

        team_strategy = "NONE"
        chain_builder = None

        # 环合反应战斗模式（延迟创建，在首次进战斗时初始化）
        core = None
        # 行为树模式（实验性）
        btree_ctx = None
        btree_root = None

        combat_start = time.time()
        while self.in_combat():
            try:
                if not ret:
                    ret = True
                    team_strategy = self._auto_detect_strategy()
                    if team_strategy == "NONE":
                        has_residual_chain = any(
                            c.__class__.__name__.endswith("Chain") for c in self.chars if c
                        )
                        if has_residual_chain:
                            self.log_info("检测到残留的 Chain 类角色，重新加载基础角色配置。")
                            self.load_chars()
                    else:
                        self.log_info(f"检测到适用连招策略：{team_strategy}")
                    self.switch_to_combat_start_char()
                    # 首次进战斗时创建 CombatController / BTContext（避免战斗外无效刷屏）
                    synergy_on = self.config.get("启用环合反应战斗", False)
                    btree_mode = self.config.get("行为树模式(实验性)", False)
                    if core is None and synergy_on and team_strategy == "NONE":
                        if btree_mode:
                            from src.combat.btree.context import BTContext
                            from src.combat.btree.loader import build_default_tree

                            btree_ctx = BTContext(self)
                            btree_root = build_default_tree()
                            self.log_info("启用环合反应战斗模式 [行为树]")
                        else:
                            from src.combat.CombatController import CombatController

                            core = CombatController(self)
                            self.log_info("启用环合反应战斗模式")

                # 链式战斗初始化（在首次帧时激活）
                if team_strategy != "NONE" and self.chain_executor:
                    if not self.chain_executor.active:
                        chain_builder = ChainLoader.load_strategy(self, team_strategy)
                        if chain_builder:
                            self.log_info(f"启用连携策略：{team_strategy}")
                            self.chain_executor.reset()
                            self.chain_executor.loop(chain_builder)

                # 执行阶段（优先级：链式 > 环合反应 > 默认自动战斗）
                if self.chain_executor and self.chain_executor.active:
                    current_char, _ = self.chain_executor.target
                    if current_char:
                        current_char.perform()
                    else:
                        self.get_current_char(raise_exception=True).perform()
                elif core is not None:
                    core.perform(self.get_current_char())
                elif btree_ctx is not None:
                    btree_ctx.current_char = self.get_current_char()
                    btree_root.tick(btree_ctx)
                else:
                    self.get_current_char(raise_exception=True).perform()
            except CharDeadException:
                self.log_error("Characters dead", notify=True)
                break
            except NotInCombatException as e:
                logger.info(f"auto_combat_task_out_of_combat {int(time.time() - combat_start)} {e}")
                ret = False
                if self.chain_executor:
                    self.chain_executor.reset()
                if core is not None:
                    core.on_combat_end()
                if btree_ctx is not None:
                    btree_ctx.reset()
                break
        if ret:
            self.combat_end()
            if core is not None:
                core.on_combat_end()
            if btree_ctx is not None:
                btree_ctx.reset()

    # 策略注册表：strategy_name -> required role_ids
    # 新增队伍策略只需在此添加一行 + ChainLoader 中注册对应分支
    STRATEGY_REGISTRY = [
        ("HOTORI_CREATION_CHAIN", {"hotori", "zero", "jiuyuan", "nanally"}),
    ]

    def _auto_detect_strategy(self):
        """自动检测当前队伍匹配的连招策略，未匹配返回 NONE。
        采用注册表模式，新增策略只需往 STRATEGY_REGISTRY 追加。
        """
        if not self.chars:
            return "NONE"
        found = set()
        for c in self.chars:
            role_id = ChainLoader._resolve_role(c)
            if role_id:
                found.add(role_id)
        for strategy_name, required in self.STRATEGY_REGISTRY:
            if required.issubset(found):
                return strategy_name
        return "NONE"
