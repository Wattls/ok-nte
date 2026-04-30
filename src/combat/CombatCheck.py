import re
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np
from ok import Box, Logger, find_color_rectangles

from src.Labels import Labels
from src.tasks.BaseNTETask import BaseNTETask
from src.utils import game_filters as gf
from src.utils import image_utils as iu

if TYPE_CHECKING:
    from src.char.BaseChar import BaseChar

logger = Logger.get_logger(__name__)


class CombatCheck(BaseNTETask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_ultimate = False
        self._in_combat = False
        self.skip_combat_check = False
        self.sleep_check_interval = 0.4
        self.last_out_of_combat_time = 0
        self.out_of_combat_reason = ""
        self.target_enemy_time_out = 3
        self.switch_char_time_out = 5
        self.combat_end_condition = None
        self.target_enemy_error_notified = False
        self.cds = {}
        self.combat_detect_future = None

    @property
    def in_ultimate(self):
        return self._in_ultimate

    @in_ultimate.setter
    def in_ultimate(self, value):
        self._in_ultimate = value
        if value:
            self._last_ultimate = time.time()

    def on_combat_check(self):
        return True

    def reset_to_false(self, reason=""):
        self.out_of_combat_reason = reason
        self.do_reset_to_false()
        return False

    def do_reset_to_false(self):
        self.cds = {}
        self._in_combat = False
        self.scene.set_not_in_combat()
        return False

    def get_current_char(self) -> "BaseChar":
        """
        获取当前角色。
        此方法必须由子类实现。
        """
        raise NotImplementedError("子类必须实现 get_current_char 方法")

    def load_chars(self) -> bool:
        """
        加载队伍中的角色信息。
        此方法必须由子类实现。
        """
        raise NotImplementedError("子类必须实现 load_chars 方法")

    def check_health_bar(self):
        return self.has_health_bar() or self.is_boss()

    def is_boss(self):
        def filter(image):
            return iu.binarize_bgr_by_brightness(image, threshold=180)

        box = self.box_of_screen(0.3582, 0.0215, 0.4808, 0.0569)
        is_boss = self.find_one(Labels.boss_lv_text, box=box, frame_processor=filter)
        return bool(is_boss)

    def target_enemy(self, wait=True):
        if not wait:
            self.middle_click()
        else:
            if self.has_target():
                return True
            else:
                logger.info(f"target lost try retarget {self.target_enemy_time_out}")
                start = time.time()
                while time.time() - start < self.target_enemy_time_out:
                    self.middle_click(interval=0.4)
                    if self.combat_detect()[0] is True:
                        return True
                    self.next_frame()

    def has_target(self, frame=None):
        # now = time.perf_counter()
        ret = self.find_target(frame=frame)
        # logger.debug(f"has_target cost {time.perf_counter() - now:.3f}")
        return ret

    def find_target(self, frame=None):
        if frame is None:
            frame = self.frame
        # 1. 提前 Crop
        box = self.box_of_screen(0.2, 0.2, 0.8, 0.6715)
        roi = box.crop_frame(frame)
        self.draw_boxes("find_target", box, color="blue")

        # 2. 还原世界亮度 (确保彩色特征在滤镜下依然可用)
        roi = iu.restore_world_brightness(roi)

        # 3. 准备彩色模板
        target_feature = self.get_feature_by_name(Labels.target)
        if target_feature is None:
            return None
        template_bgr = target_feature.mat
        
        # 4. 预处理：边缘图与二值图 (用于处理内核/空心图标并过滤杂讯)
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        roi_edge = cv2.Canny(roi_gray, 50, 150)
        _, roi_bin = cv2.threshold(roi_gray, 180, 255, cv2.THRESH_BINARY)
        
        # 提取模板的边缘特征
        tpl_gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
        tpl_edge = cv2.Canny(tpl_gray, 50, 150)
        
        best_res = None
        
        # 5. 多尺度搜索
        for scale in np.arange(0.4, 1.5, 0.1):
            tw = int(template_bgr.shape[1] * scale)
            th = int(template_bgr.shape[0] * scale)
            if tw < 12 or th < 12: continue
            
            tpl_bgr_scaled = cv2.resize(template_bgr, (tw, th))
            tpl_edge_scaled = cv2.resize(tpl_edge, (tw, th))
            
            # 模式 A: 彩色匹配 (针对完整图标，置信度高)
            res_c = cv2.matchTemplate(roi, tpl_bgr_scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val_c, _, max_loc_c = cv2.minMaxLoc(res_c)
            
            # 模式 B: 边缘匹配 (针对空心/只有内核的图标)
            res_e = cv2.matchTemplate(roi_edge, tpl_edge_scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val_e, _, max_loc_e = cv2.minMaxLoc(res_e)
            
            # 挑选候选者
            if max_val_c > 0.6:
                tx, ty, score_base = max_loc_c[0], max_loc_c[1], max_val_c
            elif max_val_e > 0.5:
                # 边缘匹配作为兜底，门槛稍低，但后续对称性校验会更严
                tx, ty, score_base = max_loc_e[0], max_loc_e[1], max_val_e
            else:
                continue
                
            # 6. 二次校验：几何特征
            crop_bin = roi_bin[ty:ty+th, tx:tx+tw]
            white_count = cv2.countNonZero(crop_bin)
            if white_count < 5: continue
            
            # 对称性：菱形图标的核心属性
            h_sym = cv2.countNonZero(cv2.bitwise_and(crop_bin, cv2.flip(crop_bin, 1))) / white_count
            v_sym = cv2.countNonZero(cv2.bitwise_and(crop_bin, cv2.flip(crop_bin, 0))) / white_count
            sym_score = (h_sym + v_sym) / 2
            
            # 孤立性：真正的 UI 图标周围应该有空隙，粒子特效通常成堆出现
            pad = 5
            if ty >= pad and tx >= pad and ty + th + pad < roi.shape[0] and tx + tw + pad < roi.shape[1]:
                outer_bin = roi_bin[ty-pad:ty+th+pad, tx-pad:tx+tw+pad]
                outer_white = cv2.countNonZero(outer_bin)
                iso_score = white_count / outer_white if outer_white > 0 else 0
            else:
                iso_score = 0.7
                
            # 综合评分：彩色/边缘得分 (2) + 对称性 (2) + 孤立性 (1)
            score = (score_base * 2 + sym_score * 2 + iso_score) / 5
            
            if score > 0.6:
                if best_res is None or score > best_res['confidence']:
                    best_res = {
                        'x': box.x + tx + tw // 2,
                        'y': box.y + ty + th // 2,
                        'w': tw,
                        'h': th,
                        'confidence': score
                    }

        if best_res:
            result_box = Box(
                best_res["x"] - best_res["w"] // 2,
                best_res["y"] - best_res["h"] // 2,
                width=best_res["w"],
                height=best_res["h"],
                confidence=best_res["confidence"],
            )
            self.draw_boxes("target", result_box, color="red")
            return result_box

        return False

    def resize_target(self, scale=1):
        template = self.get_feature_by_name(Labels.target).mat
        if scale == 1:
            return template
        h, w = template.shape[:2]
        template = cv2.resize(
            template, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_NEAREST
        )
        return template

    def has_health_bar(self):
        if self._find_red_health_bar():  # or self._find_boss_health_bar():
            return True
        return False

    def _find_red_health_bar(self):
        min_height = self.height_of_screen(5 / 1440)
        min_width = self.width_of_screen(100 / 2560)
        max_height = min_height * 2.5
        max_width = self.width_of_screen(200 / 2560)

        # 还原原始的颜色过滤
        _frame = iu.filter_by_hsv(self.frame, enemy_health_hsv)
        boxes = find_color_rectangles(
            _frame,
            enemy_health_color_red,
            min_width,
            min_height,
            max_width,
            max_height,
            box=self.main_viewport,
        )

        if len(boxes) > 0:
            self.draw_boxes("enemy_health_bar_red", boxes, color="blue")
            return True
        return False

    def _find_boss_health_bar(self):
        min_height = self.height_of_screen(9 / 2160)
        min_width = self.width_of_screen(100 / 3840)

        boxes = find_color_rectangles(
            self.frame,
            boss_health_color,
            min_width,
            min_height,
            box=self.box_of_screen(0.3277, 0.0507, 0.4980, 0.0701),
        )
        if len(boxes) == 1:
            self.draw_boxes("boss_health", boxes, color="blue")
            return True
        return False

    def in_combat(self, target=False):
        self.in_sleep_check = True
        try:
            return self.do_check_in_combat(target)
        except Exception as e:
            logger.error("do_check_in_combat", e)
        finally:
            self.in_sleep_check = False

    def do_check_in_combat(self, target):
        if self.in_ultimate:
            return True
        if self._in_combat:
            if self.scene.in_combat() is not None:
                return self.scene.in_combat()
            if current_char := self.get_current_char():
                if current_char.skip_combat_check():
                    return self.scene.set_in_combat()
            if not self.on_combat_check():
                self.log_info("on_combat_check failed")
                return self.reset_to_false(reason="on_combat_check failed")
            if self.is_boss():
                return self.scene.set_in_combat()
            # else:
            #     frame = getattr(self, 'cache_frame', None)
            #     if frame is not None:
            #         cv2.imwrite(f"cache_frame_{int(time.time())}.png", frame)
            # if self.has_target():
            #     self.last_in_realm_not_combat = 0
            #     return self.scene.set_in_combat()
            if self.combat_end_condition is not None and self.combat_end_condition():
                return self.reset_to_false(reason="end condition reached")
            combat_detect = self.async_combat_detect()
            if combat_detect is None or combat_detect is True:
                return self.scene.set_in_combat()
            if self.target_enemy(wait=True):
                logger.debug("retarget enemy succeeded")
                return self.scene.set_in_combat()
            if self.should_check_monthly_card() and self.handle_monthly_card():
                return self.scene.set_in_combat()
            logger.error("target_enemy failed, try recheck break out of combat")
            return self.reset_to_false(reason="target enemy failed")
        else:
            from src.tasks.trigger.AutoCombatTask import AutoCombatTask

            has_target = self.async_combat_detect(target=True, lv=False)
            if not has_target and target:
                self.log_debug("try target")
                self.middle_click(after_sleep=0.1)
            is_boss = self.is_boss()
            has_lv = self.find_lv()
            is_auto = self.config.get("自动目标") or not isinstance(self, AutoCombatTask)

            in_combat = (is_boss or has_lv) and (is_auto or has_target)
            if in_combat:
                if not has_target and not self.target_enemy(wait=True):
                    return False
                self.log_info("enter combat")
                self._in_combat = self.load_chars()
                return self._in_combat

    def find_lv(self, frame=None):
        # now = time.time()
        if frame is None:
            frame = self.frame
        frame = gf.isolate_lv_to_black(frame)
        # 计算基于 2K (2560x1440) 分辨率的目标矩形面积
        scale = self.width / 2560.0
        # 使用范围型体积过滤：从单个小字符到完整的 Lv+数字 区域
        min_area = (15 * scale) * (15 * scale) * 0.8
        max_area = (20 * scale) * (20 * scale) * 1.2

        # 转换为二值图并取反（使文字区域为白色 255，背景为黑色 0）
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        binary = cv2.bitwise_not(gray)

        # 连通域分析
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary)

        # 过滤：只保留矩形范围面积在 [min_area, max_area] 之间的部分
        new_binary = np.zeros_like(binary)
        for i in range(1, num_labels):
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            # 这里使用矩形框面积 (w * h) 进行过滤
            if min_area <= (w * h) <= max_area:
                new_binary[labels == i] = 255

        # 还原回 BGR 格式：文字为黑 (0)，背景为白 (255)
        frame = cv2.cvtColor(cv2.bitwise_not(new_binary), cv2.COLOR_GRAY2BGR)

        res = self.ocr(
            frame=frame,
            box=self.main_viewport,
            match=re.compile(r"lv", re.IGNORECASE),
            lib="bg_onnx_ocr",
        )
        # self.log_debug(f"find_lv time: {time.time() - now}")
        return res

    def combat_detect(self, frame=None, target=True, lv=True):
        if frame is None:
            frame = self.frame
        if target and self.has_target(frame=frame):
            return True, "target"
        if lv and self.find_lv(frame=frame):
            return True, "lv"
        return False, None

    def async_combat_detect(self, target=True, lv=True):
        if self.combat_detect_future and self.combat_detect_future.done():
            ret, reason = self.combat_detect_future.result()
            self.combat_detect_future = None
            # self.logger.info(f"combat_detect_future result: {ret}, reason: {reason}")
            return ret
        if self.combat_detect_future is None:
            # self.logger.info("combat_detect_future submit")
            frame = self.frame
            self.combat_detect_future = self.thread_pool_executor.submit(
                self.combat_detect, frame=frame, target=target, lv=lv
            )
        return None


enemy_health_hsv = iu.HSVRange((0, 190, 175), (10, 255, 255))

enemy_health_color_red = {
    "r": (210, 255),
    "g": (20, 80),
    "b": (20, 100),
}

boss_health_color = {
    "r": (215, 240),
    "g": (30, 60),
    "b": (50, 75),
}


def merge_images_vertically(img_list, bg_color=(255, 255, 255)):
    # 1. 找到所有图片中的最大宽度
    max_width = max(img.shape[1] for img in img_list)

    processed_imgs = []
    for img in img_list:
        _, w = img.shape[:2]
        if w < max_width:
            # 计算需要填充的宽度
            pad_width = max_width - w
            # 使用 cv2.copyMakeBorder 进行填充 (常数填充)
            # 这里的 bg_color 如果是灰度图传一个值(0)，如果是彩色传 (0,0,0)
            img = cv2.copyMakeBorder(img, 0, 0, 0, pad_width, cv2.BORDER_CONSTANT, value=bg_color)
        processed_imgs.append(img)

    # 2. 垂直合并
    return cv2.vconcat(processed_imgs)
