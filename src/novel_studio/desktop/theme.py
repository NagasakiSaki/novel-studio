"""Two local paper themes; no remote fonts, assets or theme dependencies."""
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from .ui_locale import install_chinese_buttons
from ..core import ROOT


def load_local_fonts():
    # Offscreen Qt has no platform font enumeration. Use installed Windows fonts,
    # never download or redistribute Microsoft's fonts with the application.
    if not QFontDatabase.hasFamily("Microsoft YaHei"):
        for folder in QStandardPaths.standardLocations(QStandardPaths.FontsLocation):
            QFontDatabase.addApplicationFont(folder + "/msyh.ttc")
            QFontDatabase.addApplicationFont(folder + "/msyhbd.ttc")


COLORS = {
    "light": dict(bg="#f4f2ec", paper="#fffefa", side="#edece5", ink="#293a3b", muted="#76817e",
                  line="#dcded5", accent="#2d6b5f", soft="#dde9df", error="#ad5547",
                  home_text="#51645d", sample="#21473e", sample_text="#fffaf0", sample_note="#c7dfd1"),
    "dark": dict(bg="#1d2527", paper="#242e30", side="#20292b", ink="#e4e7df", muted="#a0aea7",
                 line="#3b4747", accent="#a4c6ad", soft="#344b42", error="#e3a195",
                 home_text="#bdcbc2", sample="#304b42", sample_text="#f3f4e9", sample_note="#cbdfcc"),
}


def apply_theme(app, name):
    install_chinese_buttons(app)
    load_local_fonts()
    c = COLORS[name]
    checked_icon = (ROOT / 'assets' / ('check-' + name + '.png')).as_posix()
    partial_icon = (ROOT / 'assets' / ('partial-' + name + '.png')).as_posix()
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    palette = QPalette()
    for role, key in [(QPalette.Window, "bg"), (QPalette.Base, "paper"), (QPalette.Text, "ink"),
                      (QPalette.WindowText, "ink"), (QPalette.ButtonText, "ink"),
                      (QPalette.Button, "side"), (QPalette.Highlight, "soft"), (QPalette.HighlightedText, "ink")]:
        palette.setColor(role, QColor(c[key]))
    app.setPalette(palette)
    app.setStyleSheet(f"""
      QMainWindow, QDialog {{ background: {c['bg']}; color: {c['ink']}; }}
      QWidget {{ color: {c['ink']}; font-size: 13px; }}
      QWidget#header {{ background: {c['paper']}; border-bottom: 1px solid {c['line']}; }}
      QWidget#navigation, QWidget#inspector {{ background: {c['side']}; }}
      QLabel#brand {{ font-size: 16px; font-weight: 700; letter-spacing: 1px; }}
      QLabel#mark {{ background: {c['accent']}; color: {c['paper']}; border-radius: 9px; font: 700 21px Georgia; }}
      QLabel#muted {{ color: {c['muted']}; font-size: 12px; }}
      QLabel#eyebrow {{ color: {c['accent']}; font-size: 11px; font-weight: 600; letter-spacing: 2px; }}
      QLabel#title {{ font-size: 25px; font-weight: 600; }}
      QLabel#section {{ font-weight: 600; font-size: 13px; }}
      QLabel#pill {{ color: {c['accent']}; background: {c['soft']}; padding: 5px 10px; border-radius: 9px; }}
      QPushButton, QToolButton {{ border: 1px solid {c['line']}; background: {c['paper']};
          border-radius: 6px; padding: 7px 12px; min-height: 18px; }}
      QPushButton:hover, QToolButton:hover {{ border-color: {c['accent']}; }}
      QPushButton:disabled, QToolButton:disabled {{ color: {c['muted']}; }}
      QPushButton#primary {{ background: {c['accent']}; color: {c['paper']}; border-color: {c['accent']}; }}
      QPushButton#role {{ border: none; background: transparent; padding: 6px 14px; }}
      QPushButton#role:checked {{ background: {c['soft']}; color: {c['accent']}; font-weight: 600; }}
      QComboBox, QLineEdit {{ background: {c['paper']}; border: 1px solid {c['line']};
          border-radius: 6px; padding: 7px 10px; selection-background-color: {c['soft']}; }}
      QComboBox::drop-down {{ border: 0; width: 22px; }}
      QTreeWidget, QListWidget {{ background: transparent; border: none; outline: 0; }}
      QTreeWidget::item {{ min-height: 31px; padding-left: 5px; border-radius: 5px; }}
      QTreeWidget::item:selected, QListWidget::item:selected {{ background: {c['soft']}; color: {c['ink']}; }}
      QListWidget::item {{ padding: 12px 8px; border-bottom: 1px solid {c['line']}; }}
      QPlainTextEdit {{ background: {c['paper']}; border: 0; padding: 8px; color: {c['ink']};
          selection-background-color: {c['soft']}; }}
      QPlainTextEdit#manuscript {{ font-family: "Microsoft YaHei"; font-size: 18px; padding: 24px 35px; }}
      QPlainTextEdit#composer {{ border: 1px solid {c['line']}; border-radius: 8px; padding: 8px; }}
      QTabWidget::pane {{ border: 0; background: {c['paper']}; }}
      QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 12px 21px; min-width: 50px; }}
      QTabBar::tab:selected {{ color: {c['accent']}; border-bottom: 2px solid {c['accent']}; }}
      QDockWidget {{ font-weight: 600; border: 0; }}
      QDockWidget::title {{ background: {c['side']}; padding: 10px 13px; }}
      QMainWindow::separator {{ background: {c['line']}; width: 1px; height: 1px; }}
      QStatusBar {{ background: {c['side']}; border-top: 1px solid {c['line']}; }}
      QStatusBar::item {{ border: 0; }}
      QScrollBar:vertical {{ width: 8px; background: transparent; }}
      QScrollBar::handle:vertical {{ background: {c['line']}; min-height: 28px; border-radius: 4px; }}
      QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
      QSplitter::handle {{ background: {c['line']}; width: 1px; }}
      QCheckBox {{ spacing: 8px; padding: 5px; }}
      QTreeWidget::indicator, QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['muted']}; border-radius: 3px; background: {c['paper']}; }}
      QTreeWidget::indicator:checked, QCheckBox::indicator:checked {{ border-color: {c['accent']}; background: {c['accent']}; image: url("{checked_icon}"); }}
      QTreeWidget::indicator:indeterminate, QCheckBox::indicator:indeterminate {{ border-color: {c['accent']}; background: {c['accent']}; image: url("{partial_icon}"); }}
      QWidget#mainNavigation, QWidget#informationDrawer {{ background: {c['side']}; }}
      QPushButton#navItem {{ text-align: left; border: 0; border-radius: 8px; background: transparent; padding: 10px; }}
      QPushButton#navItem:checked {{ background: {c['soft']}; color: {c['accent']}; font-weight: 600; }}
      QPushButton#navItem:hover {{ background: {c['soft']}; }}
      QLabel#navCaption {{ color: {c['muted']}; font-size: 11px; padding: 0 10px 14px 10px; }}
      QPushButton#iconButton {{ border: none; background: transparent; min-width: 24px; }}
      QWidget#homePage {{ background: {c['bg']}; }}
      QFrame#heroCard, QFrame#homeSection {{ background: {c['paper']}; border: 1px solid {c['line']}; border-radius: 14px; }}
      QFrame#sampleCard {{ background: {c['sample']}; border-radius: 14px; }}
      QLabel#homeTitle {{ font-size: 34px; font-weight: 700; }}
      QLabel#homeStage {{ font-size: 13px; font-weight: 600; color: {c['accent']}; }}
      QLabel#homeDescription {{ font-size: 15px; color: {c['home_text']}; }}
      QLabel#homeScope {{ font-size: 13px; font-weight: 600; color: {c['accent']}; }}
      QLabel#homeSectionTitle, QLabel#dialogTitle {{ font-size: 20px; font-weight: 700; }}
      QLabel#sampleKicker {{ font-size: 12px; color: {c['sample_note']}; }}
      QLabel#sampleTitle {{ font-size: 27px; color: {c['sample_text']}; font-weight: 700; }}
      QLabel#sampleExcerpt {{ font-size: 15px; color: {c['sample_note']}; }}
      QWidget#homePage QPushButton#primary, QWidget#homePage QPushButton#homeButton,
      QPushButton#sampleButton {{ min-height: 26px; padding: 8px 15px; border-radius: 8px; font-weight: 600; }}
      QWidget#homePage QPushButton#primary:hover {{ border-color: {c['ink']}; }}
      QWidget#homePage QPushButton#primary:disabled {{ color: {c['muted']}; background: {c['bg']}; border-color: {c['line']}; }}
      QPushButton#homeButton {{ background: {c['bg']}; }}
      QPushButton#homeButton:hover {{ background: {c['soft']}; }}
      QPushButton#sampleButton {{ color: {c['sample']}; background: {c['sample_text']}; border-color: {c['sample_text']}; }}
      QPushButton#sampleButton:hover {{ background: {c['sample_note']}; }}
      QWidget#homePage QPushButton:focus, QDialog#projectDialog QPushButton:focus {{
          border: 2px solid {c['accent']}; padding-left: 11px; padding-right: 11px;
      }}
      QWidget#homePage QPushButton#sampleButton:focus {{ border-color: {c['sample_note']}; }}
      QWidget#homePage QProgressBar {{ border: 0; border-radius: 4px; background: {c['soft']}; max-height: 8px; }}
      QWidget#homePage QProgressBar::chunk {{ border-radius: 4px; background: {c['accent']}; }}
      QPushButton#quietButton {{ background: transparent; border-color: transparent; }}
      QPushButton#quietButton:hover {{ background: {c['soft']}; }}
      QPushButton#summaryCard {{ text-align: left; padding: 14px 18px; background: {c['paper']}; border: 1px solid {c['line']}; border-radius: 10px; }}
      QTableWidget#chapterList {{ border: none; background: transparent; alternate-background-color: transparent; }}
      QTableWidget#chapterList::item {{ border: none; padding: 8px 4px; }}
      QTableWidget#chapterList::item:selected {{ background: {c['soft']}; color: {c['ink']}; }}
      QTableWidget#chapterList::indicator {{ width: 13px; height: 13px; border: 1px solid {c['muted']}; background: {c['paper']}; border-radius: 3px; }}
      QTableWidget#chapterList::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
      QPlainTextEdit#outlineDocument, QPlainTextEdit#chapterReading {{ font-family: "Microsoft YaHei"; font-size: 16px; padding: 16px 20px; }}
    """)
