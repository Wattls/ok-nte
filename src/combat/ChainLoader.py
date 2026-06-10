class ChainLoader:
    # 角色名称映射：支持中文名、英文名、链式类名，统一解析为内部 role_id
    CHAR_NAME_MAP = {
        "浔": "hotori",
        "Hotori": "hotori",
        "HotoriChain": "hotori",
        "零": "zero",
        "Zero": "zero",
        "ZeroChain": "zero",
        "九原": "jiuyuan",
        "Jiuyuan": "jiuyuan",
        "JiuyuanChain": "jiuyuan",
        "娜娜莉": "nanally",
        "Nanally": "nanally",
        "NanallyChain": "nanally",
    }

    # 策略注册表：strategy_name -> 配置字典
    # 新增策略只需注册此表 + 创建对应 Chain 类，无需修改 replace/load 方法
    STRATEGY_TABLE = {
        "HOTORI_CREATION_CHAIN": {
            "required_roles": ["hotori", "zero", "jiuyuan", "nanally"],
            "chain_map": {
                "hotori": ("src.char.HotoriChain", "HotoriChain", "char_chain_hotori"),
                "zero": ("src.char.ZeroChain", "ZeroChain", "char_chain_zero"),
                "jiuyuan": ("src.char.JiuyuanChain", "JiuyuanChain", "char_chain_jiuyuan"),
                "nanally": ("src.char.NanallyChain", "NanallyChain", "char_chain_nanally"),
            },
            "error_msg": "队伍缺少浔/零/九原/娜娜莉其中一，无法使用浔创生链式轴！",
            "builder_role": "hotori",
            "builder_method": "_build_next_chain",
        },
    }

    @staticmethod
    def replace_chars_with_strategy(task, strategy_name):
        """通用策略替换：根据 STRATEGY_TABLE 注册表动态加载 Chain 角色。
        新增策略只需在 STRATEGY_TABLE 中注册，无需修改此方法。
        """
        import importlib

        config = ChainLoader.STRATEGY_TABLE.get(strategy_name)
        if config is None:
            return None

        chain_map = config["chain_map"]
        team_instances = {}

        for i, c in enumerate(task.chars):
            role_id = ChainLoader._resolve_role(c)
            if role_id in chain_map:
                module_path, class_name, builtin_key = chain_map[role_id]
                mod = importlib.import_module(module_path)
                target_cls = getattr(mod, class_name)

                if c.__class__.__name__ != target_cls.__name__:
                    new_char = target_cls(
                        task, c.index, char_name=c.char_name, confidence=c.confidence
                    )
                    new_char.element = c.element
                    new_char.builtin_key = builtin_key
                    task.chars[i] = new_char
                    team_instances[role_id] = new_char
                else:
                    team_instances[role_id] = c

        required = config["required_roles"]
        if not all(team_instances.get(r) for r in required):
            task.log_warning(config["error_msg"])
            return None

        return tuple(team_instances[r] for r in required)

    @staticmethod
    def _resolve_role(c):
        """通过 char_name 或类名查 CHAR_NAME_MAP，返回内部 role_id"""
        cn = getattr(c, "char_name", "")
        if cn in ChainLoader.CHAR_NAME_MAP:
            return ChainLoader.CHAR_NAME_MAP[cn]
        cls_name = c.__class__.__name__
        return ChainLoader.CHAR_NAME_MAP.get(cls_name, "")

    @staticmethod
    def load_strategy(task, strategy_name: str):
        """通用策略加载：根据 STRATEGY_TABLE 注册表动态构建 chain builder。
        新增策略只需在 STRATEGY_TABLE 中注册 builder_role 和 builder_method。
        """
        config = ChainLoader.STRATEGY_TABLE.get(strategy_name)
        if config is None:
            return None

        result = ChainLoader.replace_chars_with_strategy(task, strategy_name)
        if result is None:
            return None

        builder_role = config["builder_role"]
        builder_method = config["builder_method"]
        role_index = config["required_roles"].index(builder_role)
        builder_char = result[role_index]

        def builder():
            return getattr(builder_char, builder_method)()

        return builder
