import threading
import time

from qfluentwidgets import FluentIcon

from src.tasks.BaseNTETask import BaseNTETask
from src.tasks.NTEOneTimeTask import NTEOneTimeTask


# ─────────────────────────── 常量 ───────────────────────────

DETECT_POINTS = {
    "d": (0.2301, 0.7715),
    "f": (0.4066, 0.7715),
    "j": (0.5949, 0.7715),
    "k": (0.7684, 0.7743),
}

# 亮度阈值：低于此值认为鼓点经过（背景≈245，鼓点≈28）
BRIGHTNESS_THRESHOLD = 100

# 结算界面
FINISH_DETECT_BOX  = (0.20, 0.18, 0.80, 0.30)   # OCR检测"演奏结果"的区域
FINISH_CLOSE_POS   = (0.5402, 0.0437)             # 结算界面×关闭按钮

# 选歌界面
SONG_SELECT_BOX    = (0.85, 0.90, 1.00, 1.00)    # OCR检测"开始演奏"的区域
SONG_START_POS     = (0.8313, 0.9313)             # 开始演奏按钮（实测）

FINISH_CHECK_INTERVAL = 2.0   # 结算检测间隔（秒），避免每帧跑OCR


class RhythmTask(NTEOneTimeTask, BaseNTETask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "自动音游"
        self.description = "异环鼓组音游自动按键，支持自动结算和重打"
        self.icon = FluentIcon.MUSIC
        self.default_config.update({
            "启动延迟秒":   3,
            "按键延迟ms":   0,
            "超时秒数":     180,
            "调试日志":     False,
            "自动关闭结算": True,
            "自动重打":     False,
            "循环次数":     1,
            "D列键位":      "d",
            "F列键位":      "f",
            "J列键位":      "j",
            "K列键位":      "k",
        })
        self.config_description.update({
            "启动延迟秒":   "点击启动后等待几秒再开始检测，留时间切换到游戏界面",
            "按键延迟ms":   "检测到鼓点后的延迟（毫秒），有延迟时异步执行不阻塞检测",
            "超时秒数":     "单首歌最长等待时间，超时后停止任务",
            "调试日志":     "开启后每帧输出各点亮度，仅调试用",
            "自动关闭结算": "歌曲结束后自动关闭结算界面",
            "自动重打":     "关闭结算后自动点击开始演奏重新打歌，需同时开启自动关闭结算",
            "循环次数":     "自动重打的总次数（包含第一次），0=无限循环",
            "D列键位":      "第1列对应的键盘按键",
            "F列键位":      "第2列对应的键盘按键",
            "J列键位":      "第3列对应的键盘按键",
            "K列键位":      "第4列对应的键盘按键",
        })

        self._prev_state: dict[str, bool] = {k: False for k in DETECT_POINTS}
        self._last_finish_check: float    = 0.0
        self._pending_keys: set           = set()
        self._pending_lock                = threading.Lock()
        self._px_cache: dict | None       = None
        self._cache_shape: tuple | None   = None

    # =========================================================================
    # 入口
    # =========================================================================

    def run(self):
        super().run()

        delay = float(self.config.get("启动延迟秒", 3))
        if delay > 0:
            self.log_info(f"将在 {delay:.0f} 秒后开始检测，请切换到游戏界面")
            self.sleep(delay)

        total   = int(self.config.get("循环次数", 1))
        endless = (total == 0)
        count   = 0

        while endless or count < total:
            count += 1
            label = f"第 {count} 次" + ("" if endless else f"/{total}")

            # 点击开始演奏
            self.log_info(f"{label}：点击开始演奏")
            self.operate_click(SONG_START_POS[0], SONG_START_POS[1])

            # 等待离开选歌界面（最多15秒），确认进入音游后再开始检测
            self.log_info("等待进入音游界面")
            deadline_load = time.time() + 15
            while time.time() < deadline_load:
                self.next_frame()
                self.sleep(0.3)
                if not self._is_song_select():
                    break
            self.sleep(1.0)  # 额外等待界面稳定

            # 重置每曲状态
            self._prev_state        = {k: False for k in DETECT_POINTS}
            self._last_finish_check = 0.0

            # 单曲主循环
            self._run_single()

            # 结算处理
            if bool(self.config.get("自动关闭结算", True)):
                self._handle_finish()
            else:
                break

            # 是否继续
            if endless or count < total:
                if bool(self.config.get("自动重打", False)):
                    # 等待回到选歌界面后再点
                    self.log_info("等待回到选歌界面")
                    self.sleep(1.0)
                    # 确认在选歌界面再点（防止界面未就绪）
                    deadline = time.time() + 10
                    while time.time() < deadline:
                        if self._is_song_select():
                            break
                        self.next_frame()
                        self.sleep(0.5)
                else:
                    break

        self.log_info(f"自动音游任务结束，共完成 {count} 次", notify=True)

    def _run_single(self):
        """单曲打击主循环"""
        timeout  = float(self.config.get("超时秒数", 180))
        deadline = time.time() + timeout
        self.log_info("音游开始，按键 D/F/J/K")

        try:
            while time.time() < deadline:
                now = time.time()
                if now - self._last_finish_check >= FINISH_CHECK_INTERVAL:
                    self._last_finish_check = now
                    if self._is_finished():
                        self.log_info("检测到结算界面")
                        return
                self.tick()
                self.next_frame()
        finally:
            self._wait_pending()

    # =========================================================================
    # 结算 & 重打
    # =========================================================================

    def _is_finished(self) -> bool:
        """检测是否出现结算界面（OCR 识别"演奏结果"）"""
        return bool(self.ocr(
            box=self.box_of_screen(*FINISH_DETECT_BOX),
            match=["演奏结果"],
        ))

    def _handle_finish(self):
        """关闭结算界面"""
        self.log_info("关闭结算界面")
        self.sleep(1.5)
        self.click(FINISH_CLOSE_POS[0], FINISH_CLOSE_POS[1])
        self.sleep(1.0)


    def _is_song_select(self) -> bool:
        """检测当前是否在选歌界面（右下角有"开始演奏"按钮）"""
        return bool(self.ocr(
            box=self.box_of_screen(*SONG_SELECT_BOX),
            match=["开始演奏"],
        ))

    # =========================================================================
    # 每帧逻辑
    # =========================================================================

    def tick(self):
        state  = self.detect_notes()
        delay  = float(self.config.get("按键延迟ms", 0)) / 1000.0
        key_map = {
            "d": str(self.config.get("D列键位", "d")).strip() or "d",
            "f": str(self.config.get("F列键位", "f")).strip() or "f",
            "j": str(self.config.get("J列键位", "j")).strip() or "j",
            "k": str(self.config.get("K列键位", "k")).strip() or "k",
        }
        col_name = {"d": "第1列", "f": "第2列", "j": "第3列", "k": "第4列"}

        for track, has_note in state.items():
            prev = self._prev_state[track]
            if has_note and not prev:
                actual_key = key_map[track]
                if delay > 0:
                    self._press_async(actual_key, delay, track, col_name[track])
                else:
                    self.send_key(actual_key, interval=0)
                    self.log_info(f"按键 {actual_key.upper()} ({col_name[track]})")
            self._prev_state[track] = has_note

    # =========================================================================
    # 异步按键
    # =========================================================================

    def _press_async(self, key: str, delay: float, track: str, col: str = ""):
        def _do():
            time.sleep(delay)
            self.send_key(key, interval=0)
            self.log_info(f"按键 {key.upper()} ({col}) 延迟{delay*1000:.0f}ms")
            with self._pending_lock:
                self._pending_keys.discard(t)

        t = threading.Thread(target=_do, daemon=True)
        with self._pending_lock:
            self._pending_keys.add(t)
        t.start()

    def _wait_pending(self, timeout: float = 0.5):
        with self._pending_lock:
            threads = list(self._pending_keys)
        for t in threads:
            t.join(timeout=timeout)

    # =========================================================================
    # 鼓点检测：单点亮度
    # =========================================================================

    def detect_notes(self) -> dict[str, bool]:
        frame = self.frame
        if frame is None:
            return {k: False for k in DETECT_POINTS}

        fh, fw = frame.shape[:2]
        if self._cache_shape != (fh, fw):
            self._px_cache    = {k: (int(x*fw), int(y*fh)) for k, (x, y) in DETECT_POINTS.items()}
            self._cache_shape = (fh, fw)

        debug       = bool(self.config.get("调试日志", False))
        result      = {}
        debug_parts = [] if debug else None

        for key, (px, py) in self._px_cache.items():
            brightness  = int(frame[py-1:py+2, px-1:px+2].mean())
            has_note    = brightness < BRIGHTNESS_THRESHOLD
            result[key] = has_note
            if debug:
                debug_parts.append(f"{key.upper()}:{brightness}{'✓' if has_note else '✗'}")

        if debug:
            self.log_info("检测 | " + " | ".join(debug_parts))

        return result