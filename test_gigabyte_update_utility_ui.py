"""Tests for the window's views, selection rules and update flow. No real installer runs."""

import gc
import importlib.machinery
import importlib.util
import sys
import threading
import time
import unittest
from pathlib import Path

import gigabyte_update_utility as core
import software_guide as guide

_loader = importlib.machinery.SourceFileLoader(
    "gigabyte_update_utility_ui", str(Path(__file__).with_name("gigabyte_update_utility_ui.pyw")))
_spec = importlib.util.spec_from_loader("gigabyte_update_utility_ui", _loader)
ui = importlib.util.module_from_spec(_spec)
_loader.exec_module(ui)

DRIVER_URL = core.BASE_URL + "CHIPSET/A/1_2_B.EXE"


def item(num, offered=True, kind="driver", name=None, url=DRIVER_URL, installed="1.0", latest=None, size=1000):
    latest = latest or ("2.0" if offered else installed)
    return core.Item(num, name or f"Item {num}", kind, installed, latest, url, size, offered,
                     description=f"About {num}")


def new_driver(num, name=None):
    return item(num, name=name, installed=core.NOT_INSTALLED, latest="1.0")


def optional(num="378", name="Norton"):
    return item(num, kind="app", name=name, installed=core.NOT_INSTALLED, latest="25.9")


def module(num, installed="1.0", latest="2.0"):
    offered = core.is_newer(latest, installed)
    return core.Item(num, num, "module", installed, latest, "https://x/M.EXE", 1000, offered, ("/S",))


def entries_for(items, hardware=core.Hardware(), laptop=False):
    return {i.num: guide.guide_for(i, hardware, laptop) for i in items}


def selection(view, items):
    return ui.Selection(view, items, entries_for(items))


class SplitTests(unittest.TestCase):
    def test_installed_goes_to_updates_and_the_rest_to_downloads(self):
        updates, downloads = ui.split_views([item("1"), item("2", offered=False), new_driver("3"), optional()])
        self.assertEqual([i.num for i in updates], ["1", "2"])
        self.assertEqual([i.num for i in downloads], ["3", "378"])

    def test_unknown_version_stays_in_updates(self):
        updates, _ = ui.split_views([item("1", offered=False, installed=core.UNKNOWN)])
        self.assertEqual([i.num for i in updates], ["1"])


class GroupingTests(unittest.TestCase):
    def test_updates_group_by_category_with_updates_first(self):
        items = [item("1", offered=False, name="B"), item("2", name="Z"),
                 item("3", name="Audio", url=core.BASE_URL + "AUDIO/A/B.EXE"), module("GBT_VGA")]
        groups = ui.grouped_updates(items, entries_for(items))
        self.assertEqual([c for c, _ in groups], [guide.CHIPSET, guide.AUDIO, guide.GRAPHICS])
        self.assertEqual([i.name for i in groups[0][1]], ["Z", "B"])

    def test_categories_with_an_update_come_first(self):
        items = [item("1", offered=False), module("GBT_VGA")]
        groups = ui.grouped_updates(items, entries_for(items))
        self.assertEqual([c for c, _ in groups], [guide.GRAPHICS, guide.CHIPSET])

    def test_downloads_group_by_tier_in_order(self):
        items = [optional(), module("GBT_VGA", installed=core.NOT_INSTALLED), new_driver("1")]
        groups = ui.grouped_downloads(items, entries_for(items, core.Hardware(gigabyte_gpu=True)))
        self.assertEqual([t for t, _ in groups], [guide.NEEDED, guide.RECOMMENDED, guide.OPTIONAL])
        self.assertEqual([g[0].num for _, g in groups], ["1", "GBT_VGA", "378"])

    def test_modules_split_from_the_rest(self):
        modules, others = ui.split_modules([item("1"), optional(), module("GBT_VGA")])
        self.assertEqual([i.num for i in modules], ["GBT_VGA"])
        self.assertEqual([i.num for i in others], ["1", "378"])

    def test_module_themes_start_with_control_center(self):
        items = [module("GBT_Mouse"), module("GBT_VGA"), module("GBT_RGB_Sync_Control"), module(core.HOST_MODULE)]
        groups = ui.grouped_modules(items, entries_for(items))
        self.assertEqual([c for c, _ in groups], [guide.CONTROL_CENTER, guide.GRAPHICS, guide.LIGHTING, guide.PERIPHERALS])

    def test_recommended_modules_lead_their_theme(self):
        items = [module("GBT_SingleLed", installed=core.NOT_INSTALLED), module("GBT_RGB_Sync_Control", installed=core.NOT_INSTALLED)]
        groups = ui.grouped_modules(items, entries_for(items, core.Hardware(rgb_controller=True)))
        self.assertEqual([i.num for i in groups[0][1]], ["GBT_RGB_Sync_Control", "GBT_SingleLed"])

    def test_empty_tiers_are_left_out(self):
        items = [optional()]
        self.assertEqual([t for t, _ in ui.grouped_downloads(items, entries_for(items))], [guide.OPTIONAL])


class RowStatusTests(unittest.TestCase):
    def status(self, it, hardware=core.Hardware()):
        return ui.row_status(it, guide.guide_for(it, hardware))

    def test_the_four_statuses(self):
        self.assertEqual(self.status(item("1")), "UPDATE")
        self.assertEqual(self.status(item("1", offered=False)), "ok")
        self.assertEqual(self.status(new_driver("1")), "NEW")
        self.assertEqual(self.status(optional()), "OPTIONAL")

    def test_matching_hardware_turns_optional_into_recommended(self):
        vga = module("GBT_VGA", installed=core.NOT_INSTALLED)
        self.assertEqual(self.status(vga), "OPTIONAL")
        self.assertEqual(self.status(vga, core.Hardware(gigabyte_gpu=True)), guide.RECOMMENDED)


class SelectionTests(unittest.TestCase):
    def test_update_all_takes_update_rows(self):
        s = selection(ui.UPDATES, [item("1"), item("2"), item("3", offered=False)])
        self.assertEqual(s.button_label(), "Update All (2)")
        self.assertEqual([i.num for i in s.targets()], ["1", "2"])

    def test_install_needed_takes_new_rows_only(self):
        s = selection(ui.DOWNLOADS, [new_driver("1"), optional()])
        self.assertEqual(s.button_label(), "Install Needed (1)")
        self.assertEqual([i.num for i in s.targets()], ["1"])

    def test_tick_replaces_the_button_and_limits_the_targets(self):
        s = selection(ui.UPDATES, [item("1"), item("2")])
        s.toggle("2")
        self.assertEqual(s.button_label(), "Install Selected (1)")
        self.assertEqual([i.num for i in s.targets()], ["2"])

    def test_clearing_the_last_tick_restores_the_default(self):
        s = selection(ui.UPDATES, [item("1")])
        s.toggle("1")
        s.toggle("1")
        self.assertEqual(s.button_label(), "Update All (1)")

    def test_update_all_stays_when_nothing_is_available(self):
        s = selection(ui.UPDATES, [item("1", offered=False)])
        self.assertEqual(s.button_label(), "Update All (0)")
        self.assertEqual(s.targets(), [])

    def test_up_to_date_rows_can_be_ticked(self):
        s = selection(ui.UPDATES, [item("1", offered=False)])
        s.toggle("1")
        self.assertEqual([i.num for i in s.targets()], ["1"])

    def test_select_all_takes_every_installed_row(self):
        s = selection(ui.UPDATES, [item("1"), item("2", offered=False)])
        s.toggle_all()
        self.assertEqual(s.ticked, {"1", "2"})
        s.toggle_all()
        self.assertEqual(s.ticked, set())

    def test_select_all_skips_optional_downloads(self):
        items = [new_driver("1"), module("GBT_VGA", installed=core.NOT_INSTALLED), optional()]
        s = ui.Selection(ui.DOWNLOADS, items, entries_for(items, core.Hardware(gigabyte_gpu=True)))
        s.toggle_all()
        self.assertEqual(s.ticked, {"1", "GBT_VGA"})


class FakeBackend:
    window_items = staticmethod(core.window_items)
    type_of = staticmethod(core.type_of)
    status_of = staticmethod(core.status_of)
    would_downgrade = staticmethod(core.would_downgrade)
    install_order = staticmethod(core.install_order)
    modules_missing_host = staticmethod(core.modules_missing_host)
    control_center_installed = staticmethod(lambda items: any(
        i.num == core.HOST_MODULE and i.installed != core.NOT_INSTALLED for i in items))
    has_modules = staticmethod(core.has_modules)
    MODULE_NOTE = core.MODULE_NOTE
    MISSING_HOST_NOTE = core.MISSING_HOST_NOTE

    def prepare_modules(self):
        self.calls.append("prepare")
        return ["svc"]

    def restore_modules(self, stopped):
        self.calls.append(("restore", stopped))

    def __init__(self, items=(), codes=None, errors=None, restore=(True, ""), hardware=core.Hardware()):
        self.calls = []
        self.items = list(items)
        self.codes = codes or {}
        self.errors = errors or {}
        self.restore = restore
        self.hardware = hardware
        self.scans = 0
        self.downloaded = []
        self.ran = []
        self.restore_calls = 0
        self.scan_error = None

    def scan(self, include_catalog=False):
        self.scans += 1
        if self.scan_error:
            raise self.scan_error
        system = core.System("B550M DS3H", 124, "", "", "")
        return core.ScanResult(system, "26102801", list(self.items), self.hardware)

    def create_restore_point(self):
        self.restore_calls += 1
        return self.restore

    def download(self, target, progress, cancelled, say):
        self.downloaded.append(target.num)
        if target.num in self.errors:
            raise self.errors[target.num]
        progress("f", 500, 1000)
        return target.num

    def run_installer(self, path, args=()):
        self.ran.append(path)
        return self.codes.get(path, 0)


class RunUpdatesTests(unittest.TestCase):
    def run_updates(self, items, backend, cancel=None):
        events = []
        outcome = ui.run_updates(items, lambda *e: events.append(e), cancel or threading.Event(), backend)
        return outcome, events

    def test_only_the_targets_run_in_order_after_a_restore_point(self):
        backend = FakeBackend()
        outcome, events = self.run_updates([item("2"), item("7")], backend)
        self.assertEqual(backend.restore_calls, 1)
        self.assertEqual(backend.ran, ["2", "7"])
        self.assertEqual(outcome.installed, 2)
        self.assertIn(("row", "7", "Done", "good"), events)

    def test_restart_code_counts_as_success(self):
        outcome, events = self.run_updates([item("1")], FakeBackend(codes={"1": core.RESTART_REQUIRED}))
        self.assertTrue(outcome.restart)
        self.assertEqual(outcome.failed, 0)
        self.assertIn(("row", "1", "Done, restart needed", "good"), events)

    def test_failed_exit_code_is_reported_and_the_rest_continue(self):
        backend = FakeBackend(codes={"1": 5})
        outcome, events = self.run_updates([item("1"), item("2")], backend)
        self.assertEqual((outcome.failed, outcome.installed), (1, 1))
        self.assertIn(("row", "1", "Failed (code 5)", "bad"), events)

    def test_rejected_download_never_runs(self):
        backend = FakeBackend(errors={"1": core.ToolError("Rejected a.exe: signature status HashMismatch")})
        outcome, events = self.run_updates([item("1"), item("2")], backend)
        self.assertEqual(backend.ran, ["2"])
        self.assertIn(("row", "1", "Rejected", "bad"), events)

    def test_cancel_during_download_stops_the_item(self):
        backend = FakeBackend(errors={"1": core.Cancelled("stop")})
        cancel = threading.Event()
        original = backend.download

        def download(target, progress, cancelled, say):
            cancel.set()
            return original(target, progress, cancelled, say)

        backend.download = download
        outcome, events = self.run_updates([item("1"), item("2")], backend, cancel)
        self.assertEqual(backend.ran, [])
        self.assertEqual(outcome.cancelled, 2)

    def test_services_stop_and_restart_only_around_modules(self):
        backend = FakeBackend()
        self.run_updates([item("1")], backend)
        self.assertNotIn("prepare", backend.calls)
        backend.calls.clear()
        self.run_updates([module("GBT_VGA")], backend)
        self.assertEqual(backend.calls, ["prepare", ("restore", ["svc"])])

    def test_services_restart_even_when_an_install_breaks(self):
        backend = FakeBackend()

        def broken(*_args):
            raise RuntimeError("boom")

        backend.run_installer = broken
        with self.assertRaises(RuntimeError):
            self.run_updates([module("GBT_VGA")], backend)
        self.assertEqual(backend.calls[-1], ("restore", ["svc"]))

    def test_restore_warning_doesnt_stop_updates(self):
        backend = FakeBackend(restore=(False, "made in the last 24 hours"))
        outcome, _ = self.run_updates([item("1")], backend)
        self.assertEqual(backend.ran, ["1"])
        self.assertIn("24 hours", ui.summary_text(outcome))


class ElevationTests(unittest.TestCase):
    def test_true_when_the_prompt_is_approved(self):
        calls = []

        def shell(*args):
            calls.append(args)
            return 42

        self.assertTrue(ui.relaunch_as_admin(r"C:\a b\ui.pyw", shell))
        self.assertEqual(calls[0][1], "runas")
        self.assertEqual(calls[0][3], '"C:\\a b\\ui.pyw"')

    def test_packaged_exe_relaunches_itself_without_a_script(self):
        calls = []

        def shell(*args):
            calls.append(args)
            return 42

        saved = sys.executable
        sys.frozen = True
        sys.executable = r"C:\apps\GigabyteUpdateUtility.exe"
        try:
            self.assertTrue(ui.relaunch_as_admin("ignored", shell))
        finally:
            del sys.frozen
            sys.executable = saved
        self.assertEqual(calls[0][1:5], ("runas", r"C:\apps\GigabyteUpdateUtility.exe", None, r"C:\apps"))

    def test_false_when_the_prompt_is_declined(self):
        self.assertFalse(ui.relaunch_as_admin(r"C:\ui.pyw", lambda *a: 5))


class WindowTests(unittest.TestCase):
    """Automatic collection stays off here: run on a worker thread, it would free a closed window and abort Tcl."""

    @classmethod
    def setUpClass(cls):
        gc.disable()

    @classmethod
    def tearDownClass(cls):
        gc.collect()
        gc.enable()

    def setUp(self):
        self.notes = []
        self.questions = []
        self.answer = True
        self.backend = FakeBackend([
            item("3", offered=False, name="LAN"), item("1", name="Chipset"), item("2", name="Audio"),
            new_driver("4", name="Missing"), optional(),
            module("GBT_VGA", installed=core.NOT_INSTALLED),
            item("8", kind="catalog", name="Intel Bluetooth"),
        ], hardware=core.Hardware(gigabyte_gpu=True))

    def ask(self, _title, text):
        self.questions.append(text)
        return self.answer

    def make(self, can_install=True):
        app = ui.App(self.backend, can_install, confirm=self.ask, notify=lambda *a: self.notes.append(a[1]))
        self.addCleanup(self.close, app)
        self.pump(app, lambda: not app.busy)
        return app

    @staticmethod
    def close(app):
        app.worker.join(10)
        try:
            app.destroy()
        except Exception:
            pass

    def pump(self, app, done, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            app.update()
            if done():
                return
            time.sleep(0.01)
        self.fail("window did not reach the expected state")

    def status(self, app, num):
        return app.rows[num].status.cget("text")

    def test_title_and_icon(self):
        app = self.make()
        self.assertEqual(app.title(), "Gigabyte Update Utility")
        self.assertTrue(ui.ICON.exists())

    def test_each_view_lists_its_own_software_and_no_catalog(self):
        app = self.make()
        self.assertEqual({i.num for i in app.selections[ui.UPDATES].items}, {"1", "2", "3"})
        self.assertEqual({i.num for i in app.selections[ui.DOWNLOADS].items}, {"4", "378", "GBT_VGA"})
        self.assertNotIn("8", app.rows)

    def test_statuses_use_the_exact_words(self):
        app = self.make()
        self.assertEqual({n: self.status(app, n) for n in app.rows},
                         {"1": "UPDATE", "2": "UPDATE", "3": "ok", "4": "NEW", "378": "OPTIONAL",
                          "GBT_VGA": guide.RECOMMENDED})

    def test_every_row_has_a_box_and_a_hint(self):
        app = self.make()
        for num, row in app.rows.items():
            self.assertEqual(row.tick.cget("text"), ui.UNTICKED, num)
            self.assertTrue(row.hint.cget("text"), num)

    def test_switching_views_changes_heading_button_and_counts(self):
        app = self.make()
        self.assertEqual(app.heading.cget("text"), "Updates")
        self.assertEqual(app.primary.cget("text"), "Update All (2)")
        self.assertEqual(app.nav[ui.UPDATES].cget("text"), "Updates (2)")
        self.assertEqual(app.nav[ui.DOWNLOADS].cget("text"), "Downloads (3)")
        app.show_view(ui.DOWNLOADS)
        self.assertEqual(app.heading.cget("text"), "Downloads")
        self.assertEqual(app.primary.cget("text"), "Install Needed (1)")
        self.assertEqual(app.counts.cget("text"), "1 needed   \u00b7   1 recommended   \u00b7   1 optional")

    def test_ticking_replaces_the_button(self):
        app = self.make()
        app.toggle_row("2")
        self.assertEqual(app.primary.cget("text"), "Install Selected (1)")
        self.assertEqual(app.rows["2"].tick.cget("text"), ui.TICKED)
        app.toggle_row("2")
        self.assertEqual(app.primary.cget("text"), "Update All (2)")

    def test_ticks_belong_to_their_view(self):
        app = self.make()
        app.toggle_row("2")
        app.show_view(ui.DOWNLOADS)
        self.assertEqual(app.primary.cget("text"), "Install Needed (1)")
        app.show_view(ui.UPDATES)
        self.assertEqual(app.primary.cget("text"), "Install Selected (1)")

    def test_update_all_runs_only_update_rows(self):
        app = self.make()
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertCountEqual(self.backend.ran, ["1", "2"])

    def test_install_needed_runs_only_new_rows(self):
        app = self.make()
        app.show_view(ui.DOWNLOADS)
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertEqual(self.backend.ran, ["4"])

    def test_install_selected_runs_only_the_ticked_row(self):
        app = self.make()
        app.toggle_row("2")
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertEqual(self.backend.ran, ["2"])

    def test_a_ticked_up_to_date_row_is_reinstalled(self):
        app = self.make()
        app.toggle_row("3")
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertEqual(self.backend.ran, ["3"])
        self.assertEqual(self.questions, [])

    def test_optional_app_installs_when_ticked(self):
        app = self.make()
        app.show_view(ui.DOWNLOADS)
        app.toggle_row("378")
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertEqual(self.backend.ran, ["378"])

    def test_module_install_warns_and_can_be_declined(self):
        self.backend.items = [module("GBT_VGA")]
        self.answer = False
        app = self.make()
        app.primary.invoke()
        app.update()
        self.assertIn("Control Center", self.questions[0])
        self.assertFalse(app.busy)
        self.assertEqual(self.backend.ran, [])

    def test_module_without_control_center_warns_but_doesnt_install_it(self):
        host = module(core.HOST_MODULE, installed=core.NOT_INSTALLED)
        self.backend.items = [module("GBT_VGA", installed=core.NOT_INSTALLED), host]
        app = self.make()
        app.show_view(ui.DOWNLOADS)
        app.toggle_row("GBT_VGA")
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertIn(core.MISSING_HOST_NOTE, self.questions[0])
        self.assertIn("GBT_VGA", self.questions[0])
        self.assertNotIn(core.MODULE_NOTE, self.questions[0])
        self.assertEqual(self.backend.ran, ["GBT_VGA"])

    def test_declining_the_missing_control_center_warning_installs_nothing(self):
        host = module(core.HOST_MODULE, installed=core.NOT_INSTALLED)
        self.backend.items = [module("GBT_VGA", installed=core.NOT_INSTALLED), host]
        self.answer = False
        app = self.make()
        app.show_view(ui.DOWNLOADS)
        app.toggle_row("GBT_VGA")
        app.primary.invoke()
        app.update()
        self.assertFalse(app.busy)
        self.assertEqual(self.backend.ran, [])

    def test_no_missing_warning_when_control_center_is_installed_or_chosen(self):
        host = module(core.HOST_MODULE, installed=core.NOT_INSTALLED)
        self.backend.items = [module("GBT_VGA", installed=core.NOT_INSTALLED), host]
        app = self.make()
        app.show_view(ui.DOWNLOADS)
        app.toggle_row("GBT_VGA")
        app.toggle_row(core.HOST_MODULE)
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertNotIn(core.MISSING_HOST_NOTE, self.questions[0])
        self.assertEqual(self.backend.ran, [core.HOST_MODULE, "GBT_VGA"])

    def test_modules_section_warns_when_control_center_is_missing(self):
        self.backend.items = [module("GBT_VGA", installed=core.NOT_INSTALLED)]
        app = self.make()
        texts = [w.cget("text") for w in app.lists[ui.DOWNLOADS].body.winfo_children() if w.winfo_class() == "Label"]
        self.assertIn(guide.MODULES_TITLE, texts)
        self.assertIn(guide.MODULES_WARNING, texts)
        self.assertIn(guide.GRAPHICS, texts)

    def test_modules_section_has_no_warning_when_control_center_is_installed(self):
        self.backend.items = [module("GBT_VGA"), module(core.HOST_MODULE)]
        app = self.make()
        texts = [w.cget("text") for w in app.lists[ui.UPDATES].body.winfo_children() if w.winfo_class() == "Label"]
        self.assertIn(guide.MODULES_TITLE, texts)
        self.assertNotIn(guide.MODULES_WARNING, texts)

    def test_modules_lead_updates_only_when_they_hold_the_only_updates(self):
        self.backend.items = [item("3", offered=False, name="LAN"), module("GBT_VGA")]
        app = self.make()
        texts = [w.cget("text") for w in app.lists[ui.UPDATES].body.winfo_children() if w.winfo_class() == "Label"]
        self.assertLess(texts.index(guide.MODULES_TITLE), texts.index(guide.CHIPSET))

    def test_older_file_asks_first_and_can_be_declined(self):
        self.backend.items = [item("3", offered=False, installed="8.05.04.516", latest="8.03.25.247", name="Chipset")]
        self.answer = False
        app = self.make()
        app.toggle_row("3")
        app.primary.invoke()
        app.update()
        self.assertIn("8.05.04.516", self.questions[0])
        self.assertFalse(app.busy)
        self.assertEqual(self.backend.ran, [])

    def test_older_file_installs_once_confirmed(self):
        self.backend.items = [item("3", offered=False, installed="8.05.04.516", latest="8.03.25.247", name="Chipset")]
        app = self.make()
        app.toggle_row("3")
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertEqual(self.backend.ran, ["3"])

    def test_finished_run_checks_again(self):
        app = self.make()
        scans = self.backend.scans
        app.primary.invoke()
        self.pump(app, lambda: self.backend.scans > scans and not app.busy)

    def test_failed_run_keeps_the_result_on_screen(self):
        self.backend.errors = {"1": core.ToolError("Rejected a: signature status HashMismatch")}
        app = self.make()
        app.toggle_row("1")
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.pump(app, lambda: not app.busy)
        self.assertEqual(self.status(app, "1"), "Rejected")
        self.assertIn("HashMismatch", app.rows["1"].hint.cget("text"))

    def test_after_a_partial_failure_update_all_skips_what_finished(self):
        self.backend.codes = {"1": 5}
        app = self.make()
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.pump(app, lambda: not app.busy)
        self.assertCountEqual(self.backend.ran, ["1", "2"])
        self.assertEqual(app.primary.cget("text"), "Update All (1)")
        self.notes.clear()
        self.backend.ran.clear()
        app.primary.invoke()
        self.pump(app, lambda: bool(self.notes))
        self.assertEqual(self.backend.ran, ["1"])

    def test_read_only_mode_cant_install(self):
        app = self.make(can_install=False)
        self.assertIn("disabled", app.primary.state())
        app.start_updates()
        self.assertEqual(self.backend.ran, [])
        self.assertIn("administrator", app.status.cget("text"))

    def test_up_to_date_pc_keeps_the_button_but_disables_it(self):
        self.backend.items = [item("3", offered=False)]
        app = self.make()
        self.assertEqual(app.primary.cget("text"), "Update All (0)")
        self.assertTrue(app.primary.winfo_exists())
        self.assertIn("disabled", app.primary.state())

    def test_up_to_date_pc_can_still_install_a_ticked_row(self):
        self.backend.items = [item("3", offered=False)]
        app = self.make()
        app.toggle_row("3")
        self.assertEqual(app.primary.cget("text"), "Install Selected (1)")
        self.assertNotIn("disabled", app.primary.state())

    def test_needed_drivers_are_pointed_out(self):
        app = self.make()
        self.assertIn("needed driver", app.status.cget("text"))

    def test_scan_failure_is_shown_and_can_be_retried(self):
        self.backend.scan_error = OSError("no network")
        app = self.make()
        self.assertIn("no network", app.status.cget("text"))
        self.assertNotIn("disabled", app.refresh_button.state())

    def test_ticks_are_locked_while_running(self):
        app = self.make()
        release = threading.Event()
        original = self.backend.run_installer

        def run_installer(path, args=()):
            release.wait(5)
            return original(path, args)

        self.backend.run_installer = run_installer
        app.primary.invoke()
        app.update()
        self.assertTrue(app.busy)
        self.assertIn("disabled", app.primary.state())
        app.toggle_row("3")
        self.assertEqual(app.selection.ticked, set())
        release.set()
        self.pump(app, lambda: bool(self.notes))


if __name__ == "__main__":
    unittest.main()
