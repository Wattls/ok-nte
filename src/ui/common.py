from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt, QStringListModel
from PySide6.QtWidgets import QCompleter
from qfluentwidgets import EditableComboBox

class CharManagerSignals(QObject):
    refresh_tab = Signal()

char_manager_signals = CharManagerSignals()

def cv_to_pixmap(cv_img):
    if cv_img is None or getattr(cv_img, 'size', 0) == 0:
        return QPixmap()
    if not cv_img.flags['C_CONTIGUOUS']:
        cv_img = cv_img.copy()
    height, width = cv_img.shape[:2]
    channels = cv_img.shape[2] if len(cv_img.shape) > 2 else 1
    bytes_per_line = channels * width

    if channels == 3:
        qimg = QImage(cv_img.data, width, height, bytes_per_line, QImage.Format.Format_RGB888).rgbSwapped()
    elif channels == 4:
        qimg = QImage(cv_img.data, width, height, bytes_per_line, QImage.Format.Format_RGBA8888).rgbSwapped()
    else:
        qimg = QImage(cv_img.data, width, height, bytes_per_line, QImage.Format.Format_Grayscale8)

    return QPixmap.fromImage(qimg)

class SearchableComboBox(EditableComboBox):
    """
    基于 PySide6 的可搜寻下拉框
    继承自 EditableComboBox，实现输入关键字自动过滤清单范围
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_items = []
        self._setup_search_engine()

    def _setup_search_engine(self):
        completer = QCompleter(self.search_items)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        
        self.setCompleter(completer)

    def addItem(self, text: str, icon=None, userData=None):
        """ 重写以同步更新搜寻清单 """
        super().addItem(text, icon, userData)
        self.search_items.append(text)
        self._sync_completer_model()

    def _sync_completer_model(self):
        """ 同步内部资料模型至补全器 """
        completer = self.completer()
        model = QStringListModel(self.search_items, completer)
        completer.setModel(model)

    def clear(self):
        """ 清空时同步重置搜寻引擎 """
        super().clear()
        self.search_items.clear()
        self._sync_completer_model()