from src.tasks.BaseNTETask import binarize_bgr_by_brightness
from src.Labels import Labels

def process_feature(feature_name, feature):
    if feature_name in char_labels:
        feature.mat = binarize_bgr_by_brightness(feature.mat)
    match(feature_name):
        case Labels.boss_lv_text:
            feature.mat = binarize_bgr_by_brightness(feature.mat, threshold=180)
        case Labels.mini_map_arrow:
            feature.mat = binarize_bgr_by_brightness(feature.mat, threshold=200)

char_labels = {
    Labels.char_1_text, 
    Labels.char_2_text, 
    Labels.char_3_text, 
    Labels.char_4_text
}