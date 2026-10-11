import pytest
pytest.importorskip("PySide6")

from client.ui import theme


def test_light_and_dark_palettes_are_distinct_and_complete():
    light = theme.set_theme_mode("light")
    assert light == "light"
    light_qss = theme.build_qss()
    assert "#eef0f6" in light_qss
    assert "@BG0" not in light_qss

    dark = theme.set_theme_mode("dark")
    assert dark == "dark"
    dark_qss = theme.build_qss()
    assert "#11131a" in dark_qss
    assert "@BG0" not in dark_qss
    assert light_qss != dark_qss


def test_unknown_theme_falls_back_to_light():
    assert theme.set_theme_mode("not-a-theme") == "light"
    assert theme.BG0 == "#eef0f6"
    theme.set_theme_mode("light")
