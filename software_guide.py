"""Plain-language categories, hints and importance for everything the window lists."""

from dataclasses import dataclass

NEEDED, RECOMMENDED, OPTIONAL = "NEEDED", "RECOMMENDED", "OPTIONAL"
TIER_ORDER = (NEEDED, RECOMMENDED, OPTIONAL)
TIER_TEXT = {
    NEEDED: ("You need these", "Hardware in this PC has no driver from Gigabyte recorded. Install these first."),
    RECOMMENDED: ("These will make your PC better", "They match hardware found in this PC."),
    OPTIONAL: ("Totally optional", "Only useful if you own the product or use the software described. Skip the rest."),
}

MODULES_TITLE = "Control Center modules"
MODULES_NOTE = "These run inside Gigabyte Control Center and do nothing without it. RECOMMENDED means it matches hardware found in this PC."
MODULES_WARNING = "Control Center isn't installed. These modules won't work until you install it."

CHIPSET, NETWORK, AUDIO, GRAPHICS, OTHER_DRIVERS ="Chipset and storage", "Network", "Audio", "Graphics", "Other drivers"
CONTROL_CENTER, LIGHTING, PERFORMANCE, STORAGE, MONITORS, PERIPHERALS, LAPTOP, APPS = (
    "Control Center", "Lighting", "Performance and cooling", "Storage", "Monitors", "Peripherals", "Laptop", "Apps",
)
CATEGORY_ORDER = (CHIPSET, NETWORK, AUDIO, GRAPHICS, OTHER_DRIVERS, CONTROL_CENTER, LIGHTING, PERFORMANCE, STORAGE,
                  MONITORS, PERIPHERALS, LAPTOP, APPS)

# First folder of a driver's download path: (category, what it does)
DRIVER_FOLDERS = {
    "CHIPSET": (CHIPSET, "Lets Windows talk properly to your motherboard. Keeps USB, storage and power working reliably."),
    "BOOTDRV": (CHIPSET, "Runs your motherboard's RAID or storage controller, which your disks can depend on."),
    "NETWORK": (NETWORK, "Runs your wired or wireless network connection."),
    "WLAN": (NETWORK, "Runs your Wi-Fi connection."),
    "BLUETOOTH": (NETWORK, "Runs your Bluetooth connection."),
    "AUDIO": (AUDIO, "Runs your motherboard's sound, including speaker and microphone jacks."),
    "VGA": (GRAPHICS, "Runs your graphics. Needed for full display performance."),
    "UTILITY": (OTHER_DRIVERS, "A hardware utility matched to a component in this PC."),
    "OTHER": (OTHER_DRIVERS, "Runs a motherboard component detected in this PC."),
}
DEFAULT_DRIVER = (OTHER_DRIVERS, "Runs a component detected in this PC.")


@dataclass(frozen=True)
class Entry:
    category: str
    what: str
    # Hardware attribute that makes this worth having, or None if nothing can show it
    wanted_by: str = None
    yes: str = ""
    no: str = ""


LIGHTS_YES = "Your motherboard has a lighting controller."
LIGHTS_NO = "Only useful if your PC has Gigabyte RGB lighting."
MODULES = {
    "GIGABYTE Control Center": Entry(
        CONTROL_CENTER, "Gigabyte's main app. Every other module in this section runs inside it, so it needs installing "
        "first.", "any_gigabyte_feature", "It's what lets you use the modules recommended here.",
        "You only need it if you want one of the modules in this section."),
    "GBT_MB_Update": Entry(
        CONTROL_CENTER, "Control Center's own driver update checker.", None, "",
        "Not needed for updates: this app already does that job."),
    "GBT_rgbMotherboard_UC": Entry(LIGHTING, "Sets the colors and effects of your motherboard's lights.",
                                   "rgb_controller", LIGHTS_YES, LIGHTS_NO),
    "GBT_RGB_Sync_Control": Entry(LIGHTING, "RGB Fusion: syncs lighting effects across your Gigabyte devices.",
                                  "rgb_controller", LIGHTS_YES, LIGHTS_NO),
    "GBT_Dynamic_Lighting_Lib_UC": Entry(LIGHTING, "Lets Windows Dynamic Lighting control supported Gigabyte lights.", None, "",
                                         "Only useful if you manage your lights from Windows Settings."),
    "GBT_SingleLed": Entry(LIGHTING, "Lighting control for some Gigabyte LED products.", None, "",
                           "Only useful if you own a supported Gigabyte LED product."),
    "GBT_rgbDongle_UC": Entry(LIGHTING, "Lighting control through a Gigabyte USB RGB dongle.", None, "",
                              "Only useful if you own that dongle."),
    "MBEasyTune": Entry(PERFORMANCE, "Fan control and overclocking from Windows.", None, "",
                        "Your BIOS can set fan curves too, so you only need this for changes from Windows."),
    "GBT_Cooler": Entry(PERFORMANCE, "Controls Gigabyte liquid and air coolers.", None, "",
                        "Only useful if you own a Gigabyte cooler with software control."),
    "GBT_Power": Entry(PERFORMANCE, "Monitors and controls supported Gigabyte power supplies.", None, "",
                       "Only useful if you own a supported Gigabyte power supply."),
    "GBT_VGA": Entry(GRAPHICS, "Fan, clock and lighting control for Gigabyte graphics cards.", "gigabyte_gpu",
                     "A Gigabyte graphics card was found in this PC.",
                     "Only useful if you own a Gigabyte graphics card."),
    "MBStorage": Entry(STORAGE, "Detection and control for Gigabyte SSDs and memory, including their lights.",
                       "gigabyte_storage", "A Gigabyte drive was found in this PC.",
                       "Only useful if you own Gigabyte SSDs or RGB memory."),
    "Sidekick": Entry(MONITORS, "Changes Gigabyte monitor settings from Windows.", "gigabyte_monitor",
                      "A Gigabyte monitor was found on this PC.", "Only useful if you own a Gigabyte monitor."),
    "GBT_rgbMonitor_UC": Entry(MONITORS, "Controls the lights on Gigabyte monitors.", "gigabyte_monitor",
                               "A Gigabyte monitor was found on this PC.", "Only useful if your Gigabyte monitor has lights."),
    "GBT_Mouse": Entry(PERIPHERALS, "Buttons, lighting and settings for Gigabyte and AORUS mice.", None, "",
                       "Only useful if you own one of those mice."),
    "GBT_Keyboard": Entry(PERIPHERALS, "Keys, lighting and settings for Gigabyte and AORUS keyboards.", None, "",
                          "Only useful if you own one of those keyboards."),
    "GBT_WiFiCompass": Entry(PERIPHERALS, "A Wi-Fi helper tool for supported Gigabyte motherboards.", None, "",
                             "Only useful if your motherboard supports it."),
    "GBT_Notebook": Entry(LAPTOP, "Fan, performance and AI Boost controls for Gigabyte laptops.", "laptop",
                          "This PC is a laptop.", "Only useful on a Gigabyte laptop."),
}
APPS_INFO = {
    "378": Entry(APPS, "Norton antivirus.", None, "", "Windows already includes Microsoft Defender, so you can skip this."),
    "5733": Entry(APPS, "The Google Chrome web browser.", None, "", "Only if you want Chrome as your browser."),
    "661": Entry(APPS, "Realtek Dragon: prioritizes your network traffic.", None, "", "Only useful if you want to manage network priority."),
    "670": Entry(APPS, "cFosSpeed: prioritizes your network traffic.", None, "", "Only useful if you want to manage network priority."),
    "673": Entry(APPS, "Gigabyte Smart Backup: backs up your files.", None, "", "Only useful if you don't already use a backup tool."),
    "677": Entry(APPS, "Intel XTU: tunes and overclocks Intel processors.", None, "", "Only useful if you overclock."),
    "2690": Entry(APPS, "A.I. Boost: automatic performance tuning.", None, "", "Only useful if you want automatic tuning."),
    "3710": Entry(APPS, "A.I. Snatch: automatic overclocking for Intel processors.", None, "", "Only useful if you overclock."),
    "3711": Entry(APPS, "A.I. Snatch: automatic overclocking for AMD processors.", None, "", "Only useful if you overclock."),
    "4719": Entry(APPS, "Switches X3D gaming modes on supported AMD processors.", None, "", "Only useful with a supported X3D processor."),
}
DEFAULT_APP = Entry(APPS, "Optional software offered for this PC.", None, "", "Only if you know you want it.")
DEFAULT_MODULE = Entry(CONTROL_CENTER, "A Gigabyte Control Center module.", None, "", "Only if you know you want it.")
NEW_DRIVER_NOTE = "No installed version was found for detected hardware."
INSTALLED_DRIVER_NOTE = "Updates can fix bugs and improve compatibility."


@dataclass(frozen=True)
class Guide:
    category: str
    tier: str
    hint: str


def driver_folder(url):
    """The first folder after DriverUpd/ in a driver's download link."""
    marker = "/DriverUpd/"
    at = url.find(marker)
    if at < 0:
        return ""
    return url[at + len(marker):].split("/", 1)[0].upper()


def wanted(entry, hardware, laptop):
    if not entry.wanted_by:
        return False
    if entry.wanted_by == "laptop":
        return laptop
    if entry.wanted_by == "any_gigabyte_feature":
        return laptop or any((hardware.rgb_controller, hardware.gigabyte_gpu, hardware.gigabyte_storage,
                              hardware.gigabyte_monitor))
    return getattr(hardware, entry.wanted_by)


def guide_for(item, hardware, laptop=False):
    if item.kind == "driver":
        category, what = DRIVER_FOLDERS.get(driver_folder(item.url), DEFAULT_DRIVER)
        if item.installed == "N/A":
            return Guide(category, NEEDED, f"{what} {NEW_DRIVER_NOTE}")
        return Guide(category, RECOMMENDED, f"{what} {INSTALLED_DRIVER_NOTE}")
    entry = MODULES.get(item.num, DEFAULT_MODULE) if item.kind == "module" else APPS_INFO.get(item.num, DEFAULT_APP)
    if wanted(entry, hardware, laptop):
        return Guide(entry.category, RECOMMENDED, f"{entry.what} {entry.yes}")
    return Guide(entry.category, OPTIONAL, f"{entry.what} {entry.no}")
