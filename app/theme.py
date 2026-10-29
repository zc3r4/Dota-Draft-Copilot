from __future__ import annotations

QSS = """
* {
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
    color: #e8edf5;
}
QMainWindow, QDialog, QWidget#Root {
    background: #0f141c;
}
QWidget#Header {
    background: #151c27;
    border: 1px solid #232d3d;
    border-radius: 10px;
}
QLabel#Title {
    font-size: 18px;
    font-weight: 700;
    color: #ffffff;
}
QLabel#Subtitle, QLabel#Muted {
    color: #8d9aab;
    font-size: 11px;
}
QLabel#SectionTitle {
    font-size: 13px;
    font-weight: 700;
    color: #ffffff;
}
QLabel#Badge {
    background: #232d3d;
    color: #b9c6d6;
    border-radius: 8px;
    padding: 2px 8px;
    font-size: 10px;
}
QLabel#Good {
    color: #6cd48a;
}
QLabel#Warn {
    color: #f0b35c;
}
QLabel#Bad {
    color: #ec6b6b;
}
QFrame#Panel, QFrame#Card, QFrame#ItemCard, QFrame#Banner {
    background: #151c27;
    border: 1px solid #232d3d;
    border-radius: 10px;
}
QFrame#Banner {
    background: #2a2416;
    border-color: #5a4a22;
}
QFrame#HeroCard {
    background: #111823;
    border: 1px solid #263244;
    border-radius: 8px;
}
QFrame#HeroCard:hover {
    border-color: #e05a3a;
    background: #182231;
}
QFrame#HeroCard[locked="true"] {
    border-color: #4b8f67;
}
QLabel#HeroName {
    font-size: 12px;
    font-weight: 600;
}
QLabel#HeroScore {
    color: #7f8da0;
    font-size: 10px;
}
QPushButton {
    background: #202a3a;
    border: 1px solid #2f3d52;
    border-radius: 7px;
    padding: 6px 12px;
    color: #e8edf5;
}
QPushButton:hover {
    background: #283549;
    border-color: #41546e;
}
QPushButton:pressed {
    background: #182231;
}
QPushButton#Accent {
    background: #c84b2f;
    border-color: #e05a3a;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#Accent:hover {
    background: #dd5a3b;
}
QPushButton#Primary {
    background: #2b6f8f;
    border-color: #3d8fb3;
    color: #ffffff;
    font-weight: 600;
}
QPushButton:disabled, QPushButton#Accent:disabled, QPushButton#Primary:disabled {
    color: #5d6a7a;
    background: #171f2a;
    border-color: #232d3d;
}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #111823;
    border: 1px solid #2c394c;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #c84b2f;
}
QComboBox:hover, QLineEdit:hover {
    border-color: #41546e;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background: #151c27;
    border: 1px solid #2c394c;
    selection-background-color: #2b6f8f;
}
QTabWidget::pane {
    border: 1px solid #232d3d;
    border-radius: 8px;
    background: #111823;
    top: -1px;
}
QTabBar::tab {
    background: #151c27;
    color: #8d9aab;
    padding: 7px 14px;
    margin-right: 2px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}
QTabBar::tab:selected {
    background: #111823;
    color: #ffffff;
    border: 1px solid #232d3d;
    border-bottom-color: #111823;
}
QTabBar::tab:hover {
    color: #e8edf5;
}
QTableWidget, QListWidget, QTreeWidget {
    background: #111823;
    alternate-background-color: #141c28;
    border: none;
    gridline-color: #1e2836;
    outline: none;
}
QTableWidget::item, QListWidget::item {
    padding: 3px;
}
QTableWidget::item:selected, QListWidget::item:selected {
    background: #223247;
    color: #ffffff;
}
QHeaderView::section {
    background: #151c27;
    color: #8d9aab;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #232d3d;
    font-weight: 600;
}
QScrollBar:vertical {
    background: #0f141c;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a374a;
    min-height: 28px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #3b4d66;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar:horizontal {
    background: #0f141c;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background: #2a374a;
    min-width: 28px;
    border-radius: 5px;
}
QToolTip {
    background: #1d2635;
    color: #e8edf5;
    border: 1px solid #35465e;
    padding: 5px;
}
QGroupBox {
    border: 1px solid #232d3d;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #b9c6d6;
}
QCheckBox {
    spacing: 6px;
}
QStatusBar {
    background: #0f141c;
}
"""
