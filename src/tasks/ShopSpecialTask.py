import time

from ok import TaskDisabledException
from qfluentwidgets import FluentIcon

from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


class ShopSpecialTask(NTEOneTimeTask, BaseNTETask):
    CONF_ROUNDS = "循环次数"

    REVENUE_CHECK_INTERVAL = 1.0  # OCR 检测营业额间隔（秒）
    CLICK_INTERVAL = 0.5          # 步骤3点击间隔（秒）
    CONTROL_TIMEOUT = 120         # 单轮玩法最长等待（秒）

    POS_START   = (0.8957, 0.9326)  # 开始玩法按钮
    POS_TAP     = (0.0496, 0.4125)  # 循环点击目标
    OCR_BOX     = (0.7977, 0.0882, 0.9711, 0.1257)  # 营业额 OCR 区域
    POS_CLOSE   = (0.0230, 0.0361)  # 关闭结果界面
    POS_CONFIRM = (0.5984, 0.7764)  # 结算确认

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "店长特供挂机版"
        self.description = "自动完成一轮或多轮挂机店长特供"
        self.icon = FluentIcon.SYNC
        self.default_config.update({self.CONF_ROUNDS: 1})
        self.config_description.update({self.CONF_ROUNDS: "自动循环的轮数"})
        self.add_exit_after_config()

    def run(self):
        super().run()
        try:
            return self.do_run()
        except TaskDisabledException:
            pass
        except Exception as e:
            self.screenshot("shop_special_unexpected_exception")
            self.log_error("ShopSpecialTask error", e)
            raise

    def do_run(self):
        rounds = max(1, int(self.config.get(self.CONF_ROUNDS, 1)))
        success_count = 0
        failed_count = 0

        self.info_set("成功次数", "0")
        self.info_set("失败次数", 0)
        self.info_set("失败原因", None)
        self.info_set("当前营业额", "-")
        self.log_info(f"开始店长特供，共 {rounds} 轮")

        for round_index in range(1, rounds + 1):
            self.info_set("轮次", f"{round_index}/{rounds}")
            self.info_set("成功次数", f"{success_count}/{rounds}")
            self.info_set("失败次数", failed_count)
            self.log_info(f"开始第 {round_index}/{rounds} 轮")

            if self.run_round(round_index):
                success_count += 1
                self.info_set("成功次数", f"{success_count}/{rounds}")
            else:
                failed_count += 1
                self.info_set("失败次数", failed_count)
                self.log_error(f"第 {round_index} 轮失败")

        self.info_set("当前阶段", "任务结束")
        self.info_set("成功次数", f"{success_count}/{rounds}")
        self.info_set("失败次数", failed_count)
        self.log_info(f"店长特供结束，成功 {success_count}/{rounds}", notify=True)

    def run_round(self, round_index: int) -> bool:
        # 步骤1：按 F 进入店长特供页面
        self.info_set("当前阶段", "进入店长特供")
        self.send_key("f", action_name="enter_shop_special")
        self.sleep(1.5)

        # 步骤2：点击开始玩法
        self.info_set("当前阶段", "开始玩法")
        self.operate_click(*self.POS_START, action_name="start_gameplay")
        self.sleep(1.5)

        # 步骤3：循环点击 + OCR 检测营业额
        self.info_set("当前阶段", "营业中")
        if not self.run_until_target_revenue():
            return self._fail_round(round_index, "shop_revenue_timeout", "营业额未在超时内达标")

        # 步骤4：关闭结果界面 → 结算确认
        self.info_set("当前阶段", "结算确认")
        self.operate_click(*self.POS_CLOSE, action_name="close_result")
        self.sleep(1.5)
        self.operate_click(*self.POS_CONFIRM, action_name="confirm_settlement")
        self.sleep(1.5)

        self.info_set("当前阶段", "本轮完成")
        return True

    def run_until_target_revenue(self) -> bool:
        deadline = time.time() + self.CONTROL_TIMEOUT
        last_ocr_time = 0.0

        self.log_info("开始营业循环")
        while time.time() < deadline:
            self.operate_click(*self.POS_TAP, action_name="shop_tap")
            self.sleep(self.CLICK_INTERVAL)

            now = time.time()
            if now - last_ocr_time >= self.REVENUE_CHECK_INTERVAL:
                last_ocr_time = now
                if self._check_revenue_reached():
                    self.log_info("营业额已达标，退出营业循环")
                    return True

        self.log_error("营业额检测超时")
        return False

    def _check_revenue_reached(self) -> bool:
        x1, y1, x2, y2 = self.OCR_BOX
        raw = self.ocr(x1, y1, x2, y2)
        if not raw:
            self.log_debug("OCR 未识别到文字")
            return False

        if isinstance(raw, list):
            text = "".join(
                b.name if hasattr(b, "name") else (b.text if hasattr(b, "text") else str(b))
                for b in raw
            )
        else:
            text = str(raw)

        self.log_debug(f"OCR 识别结果: {text!r}")
        current, target = self._parse_revenue(text)
        if current is None or target is None:
            self.log_warning(f"营业额解析失败，原始文字: {text!r}")
            return False

        self.info_set("当前营业额", f"{current}/{target}")
        return current >= target

    @staticmethod
    def _parse_revenue(text: str):
        text = text.strip().replace(" ", "").replace("／", "/")
        idx = text.rfind("/")
        if idx == -1:
            return None, None
        left  = "".join(c for c in text[:idx]     if c.isdigit())
        right = "".join(c for c in text[idx + 1:] if c.isdigit())
        if not left or not right:
            return None, None
        return int(left), int(right)

    def _fail_round(self, round_index: int, reason: str, message: str) -> bool:
        self.info_set("失败原因", message)
        self.screenshot(f"{reason}_{round_index}")
        self.log_error(message)
        return False
