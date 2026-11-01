"""Tests for the categories, hints and importance shown in the window."""

import unittest

import gigabyte_update_utility as core
import software_guide as guide


def item(num="1", kind="driver", url=core.BASE_URL + "CHIPSET/A/1_2_B.EXE", installed="1.0"):
    return core.Item(num, "n", kind, installed, "2.0", url, 1, True)


class DriverTests(unittest.TestCase):
    def test_missing_driver_is_needed(self):
        self.assertEqual(guide.guide_for(item(installed=core.NOT_INSTALLED), core.Hardware()).tier, guide.NEEDED)

    def test_installed_driver_is_recommended(self):
        self.assertEqual(guide.guide_for(item(), core.Hardware()).tier, guide.RECOMMENDED)

    def test_categories_come_from_the_download_folder(self):
        for folder, category in (("CHIPSET", guide.CHIPSET), ("BOOTDRV", guide.CHIPSET), ("NETWORK", guide.NETWORK),
                                 ("AUDIO", guide.AUDIO), ("VGA", guide.GRAPHICS), ("OTHER", guide.OTHER_DRIVERS),
                                 ("SOMETHINGNEW", guide.OTHER_DRIVERS)):
            driver = item(url=f"{core.BASE_URL}{folder}/A/1_2_B.EXE")
            self.assertEqual(guide.guide_for(driver, core.Hardware()).category, category, folder)


class ModuleTests(unittest.TestCase):
    def test_every_module_on_the_server_has_a_hint(self):
        names = ("GIGABYTE Control Center", "GBT_MB_Update", "GBT_rgbMotherboard_UC", "GBT_RGB_Sync_Control",
                 "GBT_Dynamic_Lighting_Lib_UC", "GBT_SingleLed", "GBT_rgbDongle_UC", "MBEasyTune", "GBT_Cooler",
                 "GBT_Power", "GBT_VGA", "MBStorage", "Sidekick", "GBT_rgbMonitor_UC", "GBT_Mouse", "GBT_Keyboard",
                 "GBT_WiFiCompass", "GBT_Notebook")
        self.assertEqual(set(names), set(guide.MODULES))

    def recommended(self, name, hardware=core.Hardware(), laptop=False):
        return guide.guide_for(item(name, "module"), hardware, laptop).tier == guide.RECOMMENDED

    def test_matching_hardware_makes_a_module_recommended(self):
        for name, hardware in (("GBT_VGA", core.Hardware(gigabyte_gpu=True)),
                               ("GBT_rgbMotherboard_UC", core.Hardware(rgb_controller=True)),
                               ("GBT_RGB_Sync_Control", core.Hardware(rgb_controller=True)),
                               ("MBStorage", core.Hardware(gigabyte_storage=True)),
                               ("Sidekick", core.Hardware(gigabyte_monitor=True))):
            self.assertTrue(self.recommended(name, hardware), name)
            self.assertFalse(self.recommended(name), name)

    def test_notebook_needs_a_laptop(self):
        self.assertTrue(self.recommended("GBT_Notebook", laptop=True))
        self.assertFalse(self.recommended("GBT_Notebook"))

    def test_control_center_follows_the_modules_it_would_host(self):
        self.assertTrue(self.recommended(core.HOST_MODULE, core.Hardware(gigabyte_gpu=True)))
        self.assertFalse(self.recommended(core.HOST_MODULE))

    def test_modules_nothing_can_detect_stay_optional(self):
        everything = core.Hardware(True, True, True, True)
        for name in ("GBT_Mouse", "GBT_Keyboard", "GBT_MB_Update", "GBT_Cooler", "GBT_Power"):
            self.assertFalse(self.recommended(name, everything, True), name)

    def test_the_hint_says_why(self):
        found = guide.guide_for(item("GBT_VGA", "module"), core.Hardware(gigabyte_gpu=True))
        missing = guide.guide_for(item("GBT_VGA", "module"), core.Hardware())
        self.assertIn("was found", found.hint)
        self.assertIn("Only useful", missing.hint)

    def test_unknown_module_gets_a_safe_default(self):
        result = guide.guide_for(item("GBT_Future", "module"), core.Hardware(True, True, True, True))
        self.assertEqual(result.tier, guide.OPTIONAL)
        self.assertTrue(result.hint)


class AppTests(unittest.TestCase):
    def test_apps_are_optional_with_a_hint(self):
        for num in ("378", "5733", "661", "670", "673", "677", "2690", "3710", "3711", "4719", "9999"):
            result = guide.guide_for(item(num, "app"), core.Hardware(True, True, True, True), True)
            self.assertEqual((result.category, result.tier), (guide.APPS, guide.OPTIONAL), num)
            self.assertTrue(result.hint, num)


class HardwareDetectionTests(unittest.TestCase):
    def test_lighting_controller(self):
        self.assertTrue(core.detect_hardware([r"USB\VID_048D&PID_5702&REV_0001"], [], []).rgb_controller)
        self.assertFalse(core.detect_hardware([r"USB\VID_048D&PID_0001"], [], []).rgb_controller)

    def test_gigabyte_graphics_card(self):
        card = [("AMD Radeon RX 7600", r"PCI\VEN_1002&DEV_7480&SUBSYS_242B1458&REV_CF\6")]
        self.assertTrue(core.detect_hardware([], card, []).gigabyte_gpu)

    def test_other_brand_graphics_card(self):
        card = [("AMD Radeon RX 7600", r"PCI\VEN_1002&DEV_7480&SUBSYS_0E771DA2&REV_CF\6")]
        self.assertFalse(core.detect_hardware([], card, []).gigabyte_gpu)

    def test_processor_graphics_on_a_gigabyte_board_doesnt_count(self):
        card = [("AMD Radeon(TM) Graphics", r"PCI\VEN_1002&DEV_1638&SUBSYS_D0001458&REV_C8\3")]
        self.assertFalse(core.detect_hardware([], card, []).gigabyte_gpu)

    def test_storage_and_monitor(self):
        found = core.detect_hardware([r"MONITOR\GBT3204", None], [], ["AORUS NVMe Gen4 SSD"])
        self.assertTrue(found.gigabyte_storage)
        self.assertTrue(found.gigabyte_monitor)
        self.assertFalse(core.detect_hardware([], [], ["SPCC M.2 PCIe SSD"]).gigabyte_storage)


if __name__ == "__main__":
    unittest.main()
