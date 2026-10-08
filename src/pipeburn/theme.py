BG = "#16181D"
PANEL = "#1E2128"
FIELD = "#12141A"
BORDER = "#2C313B"
TEXT = "#E8ECF2"
MUTED = "#8B95A5"
STEEL = "#A9BCD0"
EMBER = "#FF7A2F"
EMBER_HOT = "#FF9446"
EMBER_DEEP = "#E8431A"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QLabel {{
    background: transparent;
}}
QLabel#title {{
    font-size: 24px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#subtitle, QLabel#fieldLabel {{
    color: {MUTED};
}}
QLabel#status {{
    color: {STEEL};
    padding: 2px 0;
}}
QLineEdit, QComboBox {{
    background: {FIELD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 9px 12px;
    selection-background-color: {EMBER_DEEP};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {EMBER};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {MUTED};
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid {BORDER};
    selection-background-color: {EMBER_DEEP};
    outline: none;
}}
QCheckBox {{
    spacing: 8px;
    color: {MUTED};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: {FIELD};
}}
QCheckBox::indicator:checked {{
    background: {EMBER};
    border: 1px solid {EMBER};
}}
QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 9px 16px;
}}
QPushButton:hover {{
    border: 1px solid {STEEL};
}}
QPushButton:disabled {{
    color: {MUTED};
    border: 1px solid {BORDER};
}}
QPushButton#primary {{
    background: {EMBER};
    border: 1px solid {EMBER};
    color: #1A0D05;
    font-weight: 700;
    padding: 9px 28px;
}}
QPushButton#primary:hover {{
    background: {EMBER_HOT};
    border: 1px solid {EMBER_HOT};
}}
QPushButton#primary:disabled {{
    background: {PANEL};
    border: 1px solid {BORDER};
    color: {MUTED};
}}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {MUTED};
}}
QPushButton#ghost:hover {{
    color: {TEXT};
    border: 1px solid {BORDER};
}}
QProgressBar {{
    background: {FIELD};
    border: 1px solid {BORDER};
    border-radius: 7px;
    height: 14px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {EMBER_DEEP}, stop:1 {EMBER_HOT});
}}
QPlainTextEdit {{
    background: {FIELD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px;
    color: {MUTED};
    font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace;
    font-size: 11px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QMessageBox {{
    background: {BG};
}}
"""
