"""Unit tests for the matching rules. Run: py -3.13 -m unittest"""

import os
import struct
import unittest
from pathlib import Path

import gigabyte_update_utility as g


def tables(pci, links, drivers):
    return {"PCITable": pci, "IDtoDriver": links, "DriverTable": drivers}


def pci(dev, num, spec="0", sub="0"):
    return {"VendorDeviceID": dev, "ispeccase": spec, "SubSystemID": sub, "Num": num}


def link(pci_id, driver, win11="1", win10="0"):
    return {"ID_num": pci_id, "Driver_Num": driver, "Win1164": win11, "W1064": win10}


class VersionTests(unittest.TestCase):
    def test_not_installed_counts_as_older(self):
        self.assertTrue(g.is_newer("1.0.0.0", g.NOT_INSTALLED))

    def test_numeric_not_text_order(self):
        self.assertTrue(g.is_newer("8.10.0.1", "8.9.0.1"))

    def test_leading_zeros(self):
        self.assertFalse(g.is_newer("11.29.50.0202", "11.29.50.202"))

    def test_newer_installed_is_kept(self):
        self.assertFalse(g.is_newer("8.03.25.247", "8.05.04.516"))

    def test_unequal_part_counts_never_update(self):
        self.assertFalse(g.is_newer("2.0.0", "1.0.0.0"))


class PlatformTests(unittest.TestCase):
    def test_this_board(self):
        self.assertEqual(g.platform_of("B550M DS3H"), 124)
        self.assertEqual(g.package_file(124), "Package-AMD")

    def test_apm_a520_is_not_a520(self):
        self.assertEqual(g.platform_of("APM-A520M"), 111)

    def test_intel_package(self):
        self.assertEqual(g.package_file(g.platform_of("Z790 AORUS")), "Package-Intel-700")

    def test_unknown_board_is_refused(self):
        with self.assertRaises(g.ToolError):
            g.platform_of("H81M")


class UrlTests(unittest.TestCase):
    def test_url_matches_the_server_layout(self):
        row = {"Driver_Path": "Chipset/Ryzen/CHIPSET/", "Num": "597", "Current_Ver": "8.03.25.247",
               "Driver_Exe_File": "CSetup.exe"}
        self.assertEqual(g.driver_url(row), g.BASE_URL + "CHIPSET/RYZEN/CHIPSET/597_8.03.25.247_CSETUP.EXE")


class MatchTests(unittest.TestCase):
    DEV = 0x14871022
    SUB = 0xA0C31458

    def match(self, t, devices=None, platform=124, column="Win1164"):
        devices = devices or [g.Device(self.DEV, self.SUB, "1.0")]
        return [row["Num"] for row, _ in g.matched_drivers(devices, t, platform, column)]

    def test_plain_row_ignores_subsystem(self):
        t = tables([pci("0x14871022", "1")], [link("1", "10")], [{"Num": "10"}])
        self.assertEqual(self.match(t), ["10"])

    def test_special_row_needs_subsystem(self):
        t = tables([pci("0x14871022", "1", "1", "0xA1841458")], [link("1", "10")], [{"Num": "10"}])
        self.assertEqual(self.match(t), [])

    def test_special_row_with_matching_subsystem(self):
        t = tables([pci("0x14871022", "1", "1", "0xA0C31458")], [link("1", "10")], [{"Num": "10"}])
        self.assertEqual(self.match(t), ["10"])

    def test_os_column_is_respected(self):
        t = tables([pci("0x14871022", "1")], [link("1", "10", win11="0", win10="1")], [{"Num": "10"}])
        self.assertEqual(self.match(t), [])
        self.assertEqual(self.match(t, column="W1064"), ["10"])

    def test_each_driver_appears_once(self):
        t = tables([pci("0x14871022", "1"), pci("0x14871022", "2")],
                   [link("1", "10"), link("2", "10")], [{"Num": "10"}])
        self.assertEqual(self.match(t), ["10"])

    def test_amd_remap(self):
        t = tables([pci("0x14871022", "1")], [link("1", "2693")], [{"Num": "2693"}, {"Num": "4714"}])
        self.assertEqual(self.match(t), ["4714"])
        self.assertEqual(self.match(t, platform=24), ["2693"])

    def test_intel_lan_on_amd_remap(self):
        t = tables([pci("0x14871022", "1")], [link("1", "664")], [{"Num": "664"}, {"Num": "635"}])
        lan = [g.Device(self.DEV, self.SUB, "1.0"), g.Device(0x15F28086, 0, "1.0")]
        self.assertEqual(self.match(t), ["664"])
        self.assertEqual(self.match(t, devices=lan), ["635"])

    def test_bad_table_row_is_skipped(self):
        t = tables([pci("not-hex", "1"), pci("0x14871022", "2")], [link("2", "10")], [{"Num": "10"}])
        self.assertEqual(self.match(t), ["10"])


class InstalledVersionTests(unittest.TestCase):
    def test_device_version_for_ordinary_rows(self):
        self.assertEqual(g.installed_version({"Driver_Ver": "0"}, None, "5.5.5.5"), "5.5.5.5")

    def test_registry_row_without_package_is_not_offered(self):
        self.assertIsNone(g.installed_version({"Driver_Ver": "99"}, None, "5.5.5.5"))

    def test_missing_registry_value_means_not_installed(self):
        package = {"RegistryKeyView": "512", "RegistryKeyPath": r"SOFTWARE\gbt-drivers-test-missing",
                   "RegistryKeyName": "Version"}
        self.assertEqual(g.installed_version({"Driver_Ver": "99"}, package, "5.5.5.5"), g.NOT_INSTALLED)


class HardwareIdTests(unittest.TestCase):
    def test_pci(self):
        self.assertEqual(g.parse_hardware_id(r"PCI\VEN_10EC&DEV_8168&SUBSYS_E0001458&REV_15"),
                         (0x816810EC, 0xE0001458, 0x15))

    def test_hdaudio(self):
        self.assertEqual(g.parse_hardware_id(r"HDAUDIO\FUNC_01&VEN_10EC&DEV_1220&SUBSYS_1458A0C3&REV_1001"),
                         (0x122010EC, 0x1458A0C3, 0x10))

    def test_usb_uses_vid_and_pid(self):
        self.assertEqual(g.parse_hardware_id(r"USB\VID_048D&PID_5702&REV_0001"), (0x048D, 0x5702, 0))

    def test_hid_uses_vid_and_pid(self):
        self.assertEqual(g.parse_hardware_id(r"HID\VID_048D&PID_5702&REV_0001&Col01"), (0x048D, 0x5702, 0))

    def test_acpi_non_hex_vendor_keeps_the_device_only(self):
        self.assertEqual(g.parse_hardware_id(r"ACPI\VEN_ITE&DEV_8295"), (0x8295, 0, 0))

    def test_acpi_hex_vendor_is_combined(self):
        self.assertEqual(g.parse_hardware_id(r"ACPI\VEN_ACPI&DEV_0007"), (0x0007, 0, 0))

    def test_acpi_pnp_zero_is_ignored(self):
        self.assertIsNone(g.parse_hardware_id(r"ACPI\VEN_PNP&DEV_0000"))

    def test_gigabyte_monitor(self):
        self.assertEqual(g.parse_hardware_id(r"MONITOR\GBT3204"), (0x3204, 0, 0))

    def test_other_devices_are_ignored(self):
        self.assertIsNone(g.parse_hardware_id(r"ROOT\VOLMGR"))


def system(platform=124, part="", version="Default string", model="B550M DS3H", cpu=""):
    return g.System(model, platform, part, version, cpu)


class AppRuleTests(unittest.TestCase):
    def listed(self, num, sys_info=None, devices=(), project=False):
        return g.app_listed(num, sys_info or system(), list(devices), {"PCITable": []}, project)

    def test_chrome_is_always_listed(self):
        self.assertTrue(self.listed("5733"))

    def test_cfos_needs_the_system_version_flag(self):
        self.assertFalse(self.listed("670"))
        self.assertTrue(self.listed("670", system(version="Default string-CF")))

    def test_xtu_is_intel_only(self):
        self.assertFalse(self.listed("677", system(cpu="Intel i7-14700K")))
        self.assertTrue(self.listed("677", system(platform=24, cpu="Intel i7-14700K")))
        self.assertFalse(self.listed("677", system(platform=24, cpu="Intel i5-14400")))

    def test_ai_boost_needs_its_part_number(self):
        self.assertFalse(self.listed("2690"))
        self.assertTrue(self.listed("2690", system(part="8arpt711")))

    def test_smart_backup_excludes_one_model(self):
        self.assertTrue(self.listed("673"))
        self.assertFalse(self.listed("673", system(model="8ARPT053")))

    def test_x3d_is_amd_only(self):
        self.assertTrue(self.listed("4719", system(version="x-XT2")))
        self.assertFalse(self.listed("4719", system(platform=24, version="x-XT2")))

    def test_ryzen_master_and_unknown_ids_are_not_listed(self):
        for num in ("676", "4722", "4721", "1680"):
            self.assertFalse(self.listed(num), num)

    def test_system_project_keeps_only_its_own_apps(self):
        self.assertTrue(self.listed("673", project=True))
        self.assertFalse(self.listed("5733", project=True))
        self.assertTrue(self.listed("3711", system(version="x-ACO"), project=True))

    def test_system_project_detection(self):
        self.assertTrue(g.is_system_project("8ARPL999"))
        self.assertFalse(g.is_system_project("8ARPT711"))
        self.assertFalse(g.is_system_project(""))


class DragonTests(unittest.TestCase):
    def tables(self, dev="0x816810EC"):
        return {"PCITable": [pci(dev, "1")]}

    def test_needs_the_listed_revision(self):
        self.assertFalse(g.has_dragon_lan([g.Device(0x816810EC, 0xE0001458, "1", 0x15)], self.tables()))
        self.assertTrue(g.has_dragon_lan([g.Device(0x816810EC, 0xE0001458, "1", 0x16)], self.tables()))

    def test_any_revision_for_the_2_5g_chip(self):
        self.assertTrue(g.has_dragon_lan([g.Device(0x812510EC, 0, "1", 0x05)], self.tables("0x812510EC")))

    def test_platform_rule(self):
        self.assertTrue(g.dragon_allowed(124))
        self.assertFalse(g.dragon_allowed(119))
        self.assertFalse(g.dragon_allowed(31))


class SelectTests(unittest.TestCase):
    def item(self, num, kind="driver", offered=True, size=1):
        return g.Item(num, "n", kind, "1", "2", "u", size, offered)

    def test_default_excludes_apps(self):
        app = g.Item("2", "n", "app", g.NOT_INSTALLED, "2", "u", 1, True)
        items = [self.item("1"), app]
        self.assertEqual([i.num for i in g.select(items, [], False)], ["1"])
        self.assertEqual([i.num for i in g.select(items, [], True)], ["1", "2"])

    def test_default_includes_installed_app_updates(self):
        self.assertEqual([i.num for i in g.select([self.item("2", "app")], [], False)], ["2"])

    def test_catalog_needs_an_id(self):
        items = [self.item("1"), self.item("9", "catalog")]
        self.assertEqual([i.num for i in g.select(items, [], True)], ["1"])
        self.assertEqual([i.num for i in g.select(items, ["9"], False)], ["9"])

    def test_catalog_never_replaces_a_newer_version(self):
        self.assertFalse(g.catalog_offered("8.05.04.516", "8.03.25.247"))
        self.assertFalse(g.catalog_offered("26.6.4", "25.10.42.0"))
        self.assertFalse(g.catalog_offered("1.0.0.0", "1.0.0.0"))
        self.assertTrue(g.catalog_offered(g.NOT_INSTALLED, "1.0.0.0"))
        self.assertTrue(g.catalog_offered(g.NO_DRIVER, "1.0.0"))
        self.assertTrue(g.catalog_offered(None, "1.0.0.0"))

    def test_app_can_be_chosen_by_id(self):
        self.assertEqual([i.num for i in g.select([self.item("2", "app")], ["2"], False)], ["2"])

    def test_install_refuses_what_is_not_offered(self):
        with self.assertRaises(g.ToolError):
            g.select([self.item("1", offered=False)], ["1"], False)

    def test_download_accepts_an_up_to_date_item(self):
        chosen = g.select_for_download([self.item("1", offered=False)], ["1"], False)
        self.assertEqual([i.num for i in chosen], ["1"])

    def test_chrome_gets_the_silent_switch(self):
        row = {"Num": "5733", "Driver_Path": "A/", "Current_Ver": "1", "Driver_Exe_File": "b.exe", "Driver_Desp_Num": "1"}
        original = g.remote_size
        g.remote_size = lambda url: 1
        try:
            self.assertEqual(g.build_item(row, "app", {}, g.NOT_INSTALLED, True).args, ("/S",))
        finally:
            g.remote_size = original


class SignerTests(unittest.TestCase):
    def test_quoted_name_with_comma(self):
        subject = 'CN="GIGA-BYTE TECHNOLOGY CO., LTD.", O="GIGA-BYTE TECHNOLOGY CO., LTD.", C=TW'
        self.assertEqual(g.signer_name(subject), "GIGA-BYTE TECHNOLOGY CO., LTD.")
        self.assertTrue(g.signer_allowed(g.signer_name(subject)))

    def test_plain_name(self):
        self.assertEqual(g.signer_name("CN=Realtek Semiconductor Corp., O=Realtek"), "Realtek Semiconductor Corp.")

    def test_other_publisher_is_refused(self):
        self.assertFalse(g.signer_allowed("Some Other Company"))
        self.assertFalse(g.signer_allowed("Realtek Semiconductor Corp."))


class InstallerTests(unittest.TestCase):
    """Runs cmd.exe in place of an installer, so the exit codes are real."""

    def test_exit_codes_come_back(self):
        cmd = Path(os.environ["ComSpec"])
        self.assertEqual(g.run_installer(cmd, ("/c", "exit", "0")), 0)
        self.assertEqual(g.run_installer(cmd, ("/c", "exit", str(g.RESTART_REQUIRED))), g.RESTART_REQUIRED)
        self.assertEqual(g.run_installer(cmd, ("/c", "exit", "5")), 5)


class TypeTests(unittest.TestCase):
    def test_utility_paths_are_software(self):
        software = g.Item("1", "n", "driver", "", "", g.BASE_URL + "UTILITY/A/1_2_B.EXE", 1, True)
        driver = g.Item("2", "n", "driver", "", "", g.BASE_URL + "CHIPSET/A/1_2_B.EXE", 1, True)
        self.assertEqual(g.type_of(software), "Software")
        self.assertEqual(g.type_of(driver), "Driver")

    def test_apps_are_software(self):
        app = g.Item("1", "n", "app", "", "", g.BASE_URL + "ANTIVIRUS/A/1_2_B.EXE", 1, True)
        self.assertEqual(g.type_of(app), "Software")

    def test_window_leaves_out_the_catalog(self):
        items = [g.Item(str(n), "n", kind, "", "", "u", 1, True) for n, kind in enumerate(("driver", "app", "catalog"))]
        self.assertEqual([i.kind for i in g.window_items(items)], ["driver", "app"])


class StatusTests(unittest.TestCase):
    def status(self, kind="driver", installed="1.0", latest="1.0", size=1, offered=False):
        return g.status_of(g.Item("1", "n", kind, installed, latest, "u", size, offered))

    def test_update(self):
        self.assertEqual(self.status(installed="1.0", latest="2.0", offered=True), "UPDATE")

    def test_new(self):
        self.assertEqual(self.status(installed=g.NOT_INSTALLED, latest="2.0", offered=True), "NEW")

    def test_optional(self):
        self.assertEqual(self.status("app", g.NOT_INSTALLED, "2.0", offered=True), "OPTIONAL")
        self.assertEqual(self.status("module", g.NOT_INSTALLED, "2.0", offered=True), "OPTIONAL")

    def test_installed_app_and_module_can_update(self):
        self.assertEqual(self.status("app", "1.0", "2.0", offered=True), "UPDATE")
        self.assertEqual(self.status("module", "1.0", "2.0", offered=True), "UPDATE")

    def test_ok_when_current_or_newer(self):
        self.assertEqual(self.status(), "ok")
        self.assertEqual(self.status(installed="8.05.04.516", latest="8.03.25.247"), "ok")

    def test_unknown_when_the_version_cant_be_read(self):
        self.assertEqual(self.status(installed=g.UNKNOWN), "unknown")


MANIFEST = """[Info]
ApName=GBT_VGA
DisplayName=Gigabyte VGA tool
Ver=26.10.13.01
ApNum=0
Size=48262112
Date=2026/10/21

[Description]
eng=Gigabyte  VGA Lib
chs=
cht=提供
"""


class ModuleTests(unittest.TestCase):
    def make(self, installed=g.NOT_INSTALLED, size=48262112, name="GBT_VGA", text=MANIFEST):
        return g.module_item(name, text, lambda url: size, installed)

    def test_names_are_unique_and_keep_their_order(self):
        text = '{"Class_name":"a","LIB":"MBStorage"}\n\n{"Class_name":"b","LIB":"MBStorage"}\n{"Class_name":"c","LIB":"GBT VGA"}\n'
        self.assertEqual(g.module_names(text), ["MBStorage", "GBT VGA"])

    def test_item_from_the_manifest(self):
        item = self.make("26.08.24.01")
        self.assertEqual((item.name, item.latest, item.size), ("Gigabyte VGA tool", "26.10.13.01", 48262112))
        self.assertEqual(item.description, "Gigabyte VGA Lib")
        self.assertEqual(item.args, ("/S",))
        self.assertEqual(item.url, g.MODULE_URL + "GBT_VGA/GBT_VGA_26.10.13.01.exe")

    def test_url_escapes_spaces(self):
        self.assertEqual(g.module_url(g.HOST_MODULE, "1"),
                         g.MODULE_URL + "GIGABYTE%20Control%20Center/GIGABYTE%20Control%20Center_1.exe")

    def test_status_follows_the_installed_version(self):
        self.assertEqual(g.status_of(self.make("26.08.24.01")), "UPDATE")
        self.assertEqual(g.status_of(self.make("26.10.13.01")), "ok")
        self.assertEqual(g.status_of(self.make(g.NOT_INSTALLED)), "OPTIONAL")

    def test_newer_installed_version_is_ok(self):
        self.assertEqual(g.status_of(self.make("27.01.01.01")), "ok")

    def test_size_mismatch_or_wrong_name_is_skipped(self):
        self.assertIsNone(self.make(size=5))
        self.assertIsNone(self.make(name="GBT_Mouse"))

    def test_type(self):
        self.assertEqual(g.type_of(self.make()), "Module")

    def host(self, installed=g.NOT_INSTALLED):
        return self.make(installed, name=g.HOST_MODULE, text=MANIFEST.replace("GBT_VGA", g.HOST_MODULE))

    def test_install_order_never_adds_anything(self):
        vga = self.make()
        driver = g.Item("1", "n", "driver", "1", "2", "u", 1, True)
        self.assertEqual([i.num for i in g.install_order([driver, vga])], ["GBT_VGA", "1"])

    def test_install_order_runs_a_chosen_host_first(self):
        vga = self.make()
        self.assertEqual([i.num for i in g.install_order([vga, self.host()])], [g.HOST_MODULE, "GBT_VGA"])

    def test_missing_host_lists_the_stranded_modules(self):
        vga, driver = self.make(), g.Item("1", "n", "driver", "1", "2", "u", 1, True)
        host = self.host()
        self.assertEqual(g.modules_missing_host([vga, driver, host], [vga, driver]), [vga])

    def test_no_warning_when_host_is_installed_or_chosen_or_for_drivers(self):
        vga, driver = self.make(), g.Item("1", "n", "driver", "1", "2", "u", 1, True)
        self.assertEqual(g.modules_missing_host([vga, self.host("1.0")], [vga]), [])
        host = self.host()
        self.assertEqual(g.modules_missing_host([vga, host], [vga, host]), [])
        self.assertEqual(g.modules_missing_host([driver, host], [driver]), [])

    def test_unlisted_host_is_read_from_the_registry(self):
        vga = self.make()
        saved = g.read_registry
        try:
            g.read_registry = lambda view, path, name: None
            self.assertEqual(g.modules_missing_host([vga], [vga]), [vga])
            g.read_registry = lambda view, path, name: "26.08.28.01"
            self.assertEqual(g.modules_missing_host([vga], [vga]), [])
        finally:
            g.read_registry = saved

    def test_notebook_module_only_on_laptops(self):
        pages = {"ver2.ini": '{"LIB":"GBT_Notebook"}\n{"LIB":"GBT_VGA"}\n', "GBT_VGA/ver2.ini": MANIFEST,
                 "GBT_Notebook/ver2.ini": MANIFEST.replace("GBT_VGA", "GBT_Notebook")}

        class Response:
            def __init__(self, text):
                self.text = text

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return self.text.encode("utf-8")

        saved = g.http, g.remote_size, g.has_signature
        g.http = lambda url, method="GET": Response(pages[url.replace(g.MODULE_URL, "")])
        g.remote_size = lambda url: 48262112
        g.has_signature = lambda url: True
        try:
            desktop = g.fetch_modules(g.System("m", 124, "", "", ""))
            laptop = g.fetch_modules(g.System("m", 124, "", "", "", True))
            g.has_signature = lambda url: False
            unsigned = g.fetch_modules(g.System("m", 124, "", "", ""))
        finally:
            g.http, g.remote_size, g.has_signature = saved
        self.assertEqual([i.num for i in desktop], ["GBT_VGA"])
        self.assertEqual([i.num for i in laptop], ["GBT_Notebook", "GBT_VGA"])
        self.assertEqual(unsigned, [])

    def test_signature_directory_is_read_from_the_header(self):
        def header(magic, directories, size):
            head = bytearray(1024)
            struct.pack_into("<I", head, 0x3C, 0x80)
            struct.pack_into("<H", head, 0x80 + 24, magic)
            struct.pack_into("<II", head, 0x80 + 24 + directories + 32, 500, size)
            return bytes(head)

        self.assertTrue(g.pe_has_signature(header(0x10B, 96, 10408)))
        self.assertFalse(g.pe_has_signature(header(0x10B, 96, 0)))
        self.assertTrue(g.pe_has_signature(header(0x20B, 112, 10408)))
        self.assertFalse(g.pe_has_signature(header(0x20B, 112, 0)))

    def test_a_failed_module_list_doesnt_break_the_scan(self):
        saved = g.http

        def fail(url, method="GET"):
            raise OSError("offline")

        g.http = fail
        try:
            self.assertEqual(g.fetch_modules(g.System("m", 124, "", "", "")), [])
        finally:
            g.http = saved


class ScanFilterTests(unittest.TestCase):
    """Items whose file isn't on the server are never listed, on any path through scan()."""

    def test_missing_files_are_dropped(self):
        package = {"DriverID": "9", "DisplayName": "", "PackageType": "1", "RegistryKeyView": "256",
                   "RegistryKeyPath": r"SOFTWARE\none", "RegistryKeyName": "V"}
        row = {"Num": "9", "Driver_Desp_Num": "1", "Driver_Path": "A/", "Current_Ver": "1.0", "Driver_Exe_File": "b.exe",
               "Driver_Ver": "99", "Win1164": "1", "W1064": "1"}
        tables = {"DriverDesp": [], "DriverTable": [row], "IDtoDriver": [], "PCITable": [], "Package": [package],
                  "Package-AMD": []}
        system = g.System("B550M DS3H", 124, "", "", "")
        saved = g.read_system, g.fetch_tables, g.list_devices, g.remote_size, g.fetch_modules, g.read_hardware
        g.read_system = lambda: system
        g.fetch_tables = lambda platform: ("1", tables)
        g.list_devices = lambda: []
        g.fetch_modules = lambda s: []
        g.read_hardware = lambda: g.Hardware()
        try:
            g.remote_size = lambda url: 0
            self.assertEqual(g.scan(True).items, [])
            g.remote_size = lambda url: 5
            self.assertEqual([i.num for i in g.scan(True).items], ["9"])
        finally:
            g.read_system, g.fetch_tables, g.list_devices, g.remote_size, g.fetch_modules, g.read_hardware = saved


class DowngradeTests(unittest.TestCase):
    def downgrade(self, installed, latest):
        return g.would_downgrade(g.Item("1", "n", "driver", installed, latest, "u", 1, False))

    def test_older_file(self):
        self.assertTrue(self.downgrade("8.05.04.516", "8.03.25.247"))

    def test_unequal_part_counts_count_as_not_known_newer(self):
        self.assertTrue(self.downgrade("26.6.4", "25.10.42.0"))

    def test_same_or_newer_file(self):
        self.assertFalse(self.downgrade("1.0.0.0", "1.0.0.0"))
        self.assertFalse(self.downgrade("1.0.0.0", "2.0.0.0"))

    def test_not_installed_or_unreadable(self):
        self.assertFalse(self.downgrade(g.NOT_INSTALLED, "1.0"))
        self.assertFalse(self.downgrade(g.UNKNOWN, "1.0"))


class PackageOverrideTests(unittest.TestCase):
    def test_am4_raid_version_only_on_am4(self):
        for platform, expected in ((124, g.AM4_RAID_VERSION), (111, "1.0")):
            t = {"DriverTable": [{"Num": "641", "Current_Ver": "1.0"}]}
            g.apply_platform_overrides(t, platform)
            self.assertEqual(t["DriverTable"][0]["Current_Ver"], expected)


if __name__ == "__main__":
    unittest.main()
