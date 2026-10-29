import bambustudio_import as bi

IDS = ["account", "rescan", "search", "kindFilter", "printerFilter", "list", "checkReady", "checkNone",
       "import", "clear", "report", "msg"]


def test_html_has_required_ids():
    for i in IDS:
        assert 'id="%s"' % i in bi.PAGE_HTML, i


def test_html_no_external_resources():
    assert "http://" not in bi.PAGE_HTML
    assert "https://" not in bi.PAGE_HTML


def test_html_uses_bridge():
    assert "window.orca.postMessage" in bi.PAGE_HTML
    assert "window.orca.onMessage" in bi.PAGE_HTML


def test_module_imports_without_orca():
    assert bi.orca is None
    assert hasattr(bi, "ImportCore")


def test_parse_message():
    assert bi.parse_message({"action": "scan"}) == {"action": "scan"}
    assert bi.parse_message('{"action": "scan"}') == {"action": "scan"}
    assert bi.parse_message("garbage") == {"action": "garbage"}


def test_permission_targets_cover_tree():
    assert bi.permission_targets(["/a/BambuStudio"]) == ["/a/BambuStudio/*"]


def test_import_only_counts_visible_checked():
    # hidden (filtered-out) ticks must not be imported or counted
    assert "function selectedPaths()" in bi.PAGE_HTML
    assert "it.selectable && S.checked[it.path] && visible(it)" in bi.PAGE_HTML


def test_rescan_keeps_user_ticks():
    assert "S.loaded" in bi.PAGE_HTML


def test_permission_targets_windows_style():
    # Orca's grant matching treats / and \ alike on Windows: one entry per folder,
    # so the permission dialog doesn't list every folder twice
    t = bi.permission_targets([r"C:\Users\r\AppData\Roaming\BambuStudio"])
    assert t == [r"C:\Users\r\AppData\Roaming\BambuStudio\*"]


NEW_IDS = ["statusChips", "banner", "empty"]


def test_html_has_new_layout_ids():
    for i in NEW_IDS:
        assert 'id="%s"' % i in bi.PAGE_HTML, i


def test_status_filter_in_visible():
    assert "S.statusFilter" in bi.PAGE_HTML


def test_short_printer_keeps_nozzle(tmp_path):
    # the "not visible" warning must show the nozzle: P2S 0.2 vs P2S 0.4
    import json
    import shutil
    import subprocess
    import pytest
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = bi.PAGE_HTML.split("<script>")[1].split("</script>")[0]
    fn = js[js.index("function shortPrinter"):]
    fn = fn[:fn.index("\n")]
    script = tmp_path / "t.js"
    script.write_text(encoding="utf-8", data=fn + "\nconsole.log(JSON.stringify(["
                      "shortPrinter('Bambu Lab P2S 0.2 nozzle'), shortPrinter('Bambu Lab X1 Carbon 0.4 nozzle'),"
                      "shortPrinter('Bambu Lab A1 mini 0.4 nozzle'), shortPrinter('Voron 2.4')]));")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, encoding="utf-8").stdout)
    assert out == ["P2S · 0.2 mm nozzle", "X1C · 0.4 mm nozzle", "A1 mini · 0.4 mm nozzle", "Voron 2.4"]


def test_warning_names_target_and_current_printer():
    assert "{for}" in bi.JS_STRINGS["en"]["hidden_current"] and "{cur}" in bi.JS_STRINGS["en"]["hidden_current"]
    assert "{for}" in bi.JS_STRINGS["nl"]["hidden_current"] and "{cur}" in bi.JS_STRINGS["nl"]["hidden_current"]


def test_nozzle_of_item(tmp_path):
    # a single nozzle size among the target printers is shown on the row
    import json
    import shutil
    import subprocess
    import pytest
    if not shutil.which("node"):
        pytest.skip("node not installed")
    js = bi.PAGE_HTML.split("<script>")[1].split("</script>")[0]
    fn = js[js.index("function nozzleOf"):]
    fn = fn[:fn.index("\n")]
    script = tmp_path / "t.js"
    script.write_text(encoding="utf-8", data=fn + "\nconsole.log(JSON.stringify(["
                      "nozzleOf({visible_on:['Bambu Lab P2S 0.2 nozzle']}),"
                      "nozzleOf({visible_on:['Bambu Lab P2S 0.2 nozzle','Bambu Lab X1C 0.2 nozzle']}),"
                      "nozzleOf({visible_on:['Bambu Lab P2S 0.2 nozzle','Bambu Lab P2S 0.4 nozzle']}),"
                      "nozzleOf({visible_on:[]})]));")
    out = json.loads(subprocess.run(["node", str(script)], capture_output=True, encoding="utf-8").stdout)
    assert out == ["0.2", "0.2", "", ""]


def test_import_target_choice_in_page():
    assert 'id="targetBundle"' in bi.PAGE_HTML and 'id="targetUser"' in bi.PAGE_HTML
    assert "target:S.target" in bi.PAGE_HTML
    for lang in ("en", "nl"):
        for key in ("target_label", "target_bundle", "target_user", "saved_user", "stored_user", "stored_bundle"):
            assert bi.JS_STRINGS[lang][key], (lang, key)


def test_remove_buttons_in_page():
    assert 'id="removeSel"' in bi.PAGE_HTML
    assert 'action:"remove"' in bi.PAGE_HTML
    for lang in ("en", "nl"):
        for key in ("remove_n", "remove_one", "confirm_remove", "clear_all"):
            assert bi.JS_STRINGS[lang][key], (lang, key)
