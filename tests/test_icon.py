import os
import xml.etree.ElementTree as ET

import bambustudio_import as bi


def test_tab_icon_svg_is_valid_white_20px():
    root = ET.fromstring(bi.TAB_ICON_SVG)
    assert root.get("viewBox") == "0 0 20 20"
    assert "#ffffff" in bi.TAB_ICON_SVG.lower()


def test_write_tab_icon(tmp_path):
    path = bi.write_tab_icon(str(tmp_path))
    assert path == os.path.join(str(tmp_path), "bambustudio_import_tab_icon.svg")
    with open(path, encoding="utf-8") as fh:
        assert fh.read() == bi.TAB_ICON_SVG


def test_write_tab_icon_unwritable_returns_empty(tmp_path):
    assert bi.write_tab_icon(str(tmp_path / "missing" / "dir")) == ""


def test_page_has_logo_svg():
    ET.fromstring(bi.LOGO_SVG)
    assert bi.LOGO_SVG in bi.PAGE_HTML
