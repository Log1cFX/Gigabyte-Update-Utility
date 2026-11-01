"""Driver and software updates without GIGABYTE Control Center.

Uses Gigabyte's undocumented public update files. Fetches Gigabyte's data at run time and bundles
none of it. BIOS updates are never listed.
"""

import argparse
import configparser
import csv
import ctypes
import io
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import winreg
from dataclasses import dataclass
from pathlib import Path

UPDATE_ROOT = "https://mb.download.gigabyte.com/FileList/Swhttp/"
BASE_URL = UPDATE_ROOT + "DriverUpd/"
MODULE_URL = UPDATE_ROOT + "LiveUpdate4/GCC/"
USER_AGENT = "gigabyte-update-utility"
TIMEOUT = 60
HOME = Path(os.environ["LOCALAPPDATA"]) / "Gigabyte Update Utility"
TABLES = ("DriverDesp", "DriverTable", "IDtoDriver", "PCITable", "Package")
NOT_INSTALLED = "N/A"
NO_DRIVER = "0.0.0.0"
UNKNOWN = "unknown"
KIND_ORDER = {"driver": 0, "module": 1, "app": 2, "catalog": 3}
MODULE_KEY = r"SOFTWARE\GIGABYTE\Control Center"
HOST_MODULE = "GIGABYTE Control Center"
NOTEBOOK_MODULE = "GBT_Notebook"
LAPTOP_CHASSIS = {9, 10, 14}
# These hold module files open during an install
MODULE_SERVICES = ("EasyTuneEngineService", "AorusLcdService", "GBTECService", "Gdrv3")
SERVICE_WAIT_SECONDS = 15
# The ITE lighting chips Gigabyte boards use over USB
RGB_CONTROLLER_PIDS = ("5702", "5711", "8297", "8950")
GIGABYTE_SUBSYSTEM_RE = re.compile(r"SUBSYS_[0-9A-F]{4}1458")
PE_HEADER_BYTES = 4096
PE32_MAGIC = 0x10B
PE32_DIRECTORIES = 96
PE32_PLUS_DIRECTORIES = 112
PE_SECURITY_ENTRY = 4 * 8

# Order matters: APM-A520 contains A520
PLATFORMS = (
    (("Z990", "Z970", "W980", "B960"), 201),
    (("Z890", "B860", "H810", "Q870", "W880"), 31),
    (("Z790", "B760"), 24),
    (("Z690", "B660", "H610"), 22),
    (("W790",), 29),
    (("X670", "B650", "A620", "TRX50", "APM-A520"), 111),
    (("A520", "B550", "X570", "X470", "B450"), 124),
    (("X870", "B850", "B840"), 119),
)
AM4_PLATFORM = 124
AM5_NEW_PLATFORM = 119
AM4_RAID_DRIVERS = {"641", "672", "579"}
AM4_RAID_VERSION = "9.3.0.296"
RAID_NAME = "AMD AM4/AM5 FCH RAID driver"
RAID_FALLBACK_NAME = "AMD AM4 FCH RAID driver"
AMD_INTEL_LAN_IDS = {0x15F28086, 0x15F38086, 0x125C8086}
AMD_DRIVER_REMAP = {"2693": "4714"}
AMD_INTEL_LAN_REMAP = {"664": "635"}
VER_FROM_REGISTRY = 99
VER_FROM_UNINSTALL = 98
PACKAGE_TYPE_DRIVER = "2"
PACKAGE_TYPE_FIRMWARE = "4"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
BIOS_KEY = r"HARDWARE\DESCRIPTION\System\BIOS"
CPU_KEY = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
NORTON_OEM_KEY = r"SOFTWARE\Norton\oem-suite"
XTU_NAME = "Intel(R) Extreme Tuning Utility"
SYSTEM_PROJECT_APPS = ("673", "3710", "3711")
# LAN chips Realtek Dragon applies to: (device, subsystem, revision), revision None for any
DRAGON_LAN = ((0x812510EC, 0, None), (0x816810EC, 0xE0001458, 0x16))
SMART_BACKUP_EXCLUDED = "8ARPT053"
AI_BOOST_PART = "8ARPT711"
CHROME = "5733"
HKLM_VIEW_MASK = winreg.KEY_WOW64_64KEY | winreg.KEY_WOW64_32KEY
SAFE_NAME_RE = re.compile(r"^[\w.\- ]+$")
HEX = set("0123456789abcdefABCDEF")
ALLOWED_SIGNERS = ("GIGA-BYTE",)
# Windows installer code for success with a restart pending
RESTART_REQUIRED = 3010
MODULE_NOTE = "Control Center will be closed and its services stopped while these install."
MISSING_HOST_NOTE = "Control Center isn't installed, so these modules won't work until you install it"
BOARD_TAG_NOTE ="Board tag needs administrator rights. Without it, A.I. Boost and prebuilt-system rules can't be applied."


class ToolError(Exception):
    pass


class Cancelled(ToolError):
    pass


@dataclass(frozen=True)
class Device:
    dev_id: int
    sub_id: int
    version: str
    rev: int = 0


@dataclass
class System:
    model: str
    platform: int
    part_number: str
    system_version: str
    cpu_name: str
    laptop: bool = False


@dataclass
class Item:
    num: str
    name: str
    kind: str
    installed: str
    latest: str
    url: str
    size: int
    offered: bool
    args: tuple = ()
    description: str = ""


@dataclass(frozen=True)
class Hardware:
    """What the window's recommendations rest on. Each one is a best guess from Windows device data."""
    rgb_controller: bool = False
    gigabyte_gpu: bool = False
    gigabyte_storage: bool = False
    gigabyte_monitor: bool = False


@dataclass
class ScanResult:
    system: System
    folder: str
    items: list
    hardware: Hardware = Hardware()


def package_file(platform):
    """Platforms 104-200 except 107 are AMD."""
    if is_amd(platform):
        return "Package-AMD"
    if platform in (22, 23, 26, 30):
        return "Package-Intel-600"
    if platform in (24, 25, 27, 28):
        return "Package-Intel-700"
    if platform == 29:
        return "Package-Intel-W790"
    if platform == 31:
        return "Package-Intel-800"
    return "Package-Intel-900"


def is_amd(platform):
    return 104 <= platform < 201 and platform != 107


def platform_of(model):
    model = model.upper()
    for names, platform in PLATFORMS:
        if any(name in model for name in names):
            return platform
    raise ToolError(f"Unsupported board model: {model}")


def is_newer(latest, installed):
    """Not installed counts as older, unequal part counts never update."""
    if installed == NOT_INSTALLED:
        return True
    new_parts, old_parts = latest.split("."), installed.split(".")
    if len(new_parts) != len(old_parts):
        return False
    return version_key(new_parts) > version_key(old_parts)


def version_key(parts):
    return [int(p) if p.isdigit() else 0 for p in parts[:4]]


def driver_url(row):
    """The server paths are case-sensitive and uppercase."""
    path = f"{row['Driver_Path']}{row['Num']}_{row['Current_Ver']}_{row['Driver_Exe_File']}"
    return BASE_URL + urllib.parse.quote(path.replace("\\", "/").upper())


def hex_after(text, marker, length):
    at = text.find(marker)
    if at < 0:
        return 0
    try:
        return int(text[at + len(marker):at + len(marker) + length], 16)
    except ValueError:
        return 0


def dev_id_of(hardware_id):
    """Device then the 4 characters before &DEV_, or device alone if those are not hex."""
    at = hardware_id.find("&DEV_")
    if at < 0:
        return 0
    text = hardware_id[at + 5:at + 9] + hardware_id[at - 4:at]
    good = 0
    while good < len(text) and text[good] in HEX:
        good += 1
    if good < len(text) - 1:
        text = text[:4]
    try:
        return int(text, 16)
    except ValueError:
        return 0


def parse_hardware_id(hardware_id):
    """(device, subsystem, revision), or None for devices that don't apply."""
    rev = hex_after(hardware_id, "&REV_", 2) if "&REV_" in hardware_id else 0
    if "PCI\\VEN_" in hardware_id or "HDAUDIO\\FUNC_" in hardware_id:
        return dev_id_of(hardware_id), hex_after(hardware_id, "&SUBSYS_", 8), rev
    if "ACPI\\VEN_" in hardware_id:
        if "_ITE&" not in hardware_id and "PNP&DEV_0000" in hardware_id:
            return None
        return dev_id_of(hardware_id), hex_after(hardware_id, "&SUBSYS_", 8), 0
    if "USB\\VID_" in hardware_id or "HID\\VID_" in hardware_id:
        return hex_after(hardware_id, "VID_", 4), hex_after(hardware_id, "&PID_", 4), rev
    if "MONITOR\\GBT" in hardware_id:
        return hex_after(hardware_id, "\\GBT", 4), 0, 0
    return None


def pci_table_id(row, dev_id, sub_id):
    """Returns the row Num, or None without a match."""
    if int(row["VendorDeviceID"], 16) != dev_id:
        return None
    if row["ispeccase"].strip() == "0":
        return row["Num"]
    sub = row["SubSystemID"].strip()
    return row["Num"] if sub and int(sub, 16) == sub_id else None


def remap_driver(num, platform, has_intel_lan):
    """Applies the driver substitutions for AMD platforms."""
    if not is_amd(platform):
        return num
    if has_intel_lan:
        num = AMD_INTEL_LAN_REMAP.get(num, num)
    return AMD_DRIVER_REMAP.get(num, num)


def matched_drivers(devices, tables, platform, os_column):
    """Yields (driver row, device) once per driver."""
    has_intel_lan = is_amd(platform) and any(d.dev_id in AMD_INTEL_LAN_IDS for d in devices)
    driver_rows = {r["Num"]: r for r in tables["DriverTable"]}
    seen = set()
    for device in devices:
        for pci_row in tables["PCITable"]:
            try:
                pci_id = pci_table_id(pci_row, device.dev_id, device.sub_id)
            except ValueError:
                continue
            if pci_id is None:
                continue
            for link in tables["IDtoDriver"]:
                if link["ID_num"] != pci_id or link[os_column] != "1":
                    continue
                num = remap_driver(link["Driver_Num"], platform, has_intel_lan)
                if num in driver_rows and num not in seen:
                    seen.add(num)
                    yield driver_rows[num], device


def has_dragon_lan(devices, tables):
    """A listed LAN chip whose ID is also in PCITable."""
    for dev_id, sub_id, rev in DRAGON_LAN:
        present = any(d.dev_id == dev_id and (rev is None or d.rev == rev) for d in devices)
        if present and any(table_row_matches(row, dev_id, sub_id) for row in tables["PCITable"]):
            return True
    return False


def table_row_matches(row, dev_id, sub_id):
    try:
        return pci_table_id(row, dev_id, sub_id) is not None
    except ValueError:
        return False


def is_system_project(part_number):
    """Prebuilt-system part numbers all match the third-from-last rule."""
    return len(part_number) >= 3 and part_number[-3] == "9"


def app_listed(num, system, devices, tables, system_project):
    """Whether an optional app is listed for this PC."""
    if system_project:
        if num == "673":
            return True
        if num == "3710":
            return intel_ai_oc(system)
        return num == "3711" and amd_ai_oc(system)
    amd = is_amd(system.platform)
    if num == "378":
        return read_registry_int(NORTON_OEM_KEY, "success") != 1
    if num == "661":
        return dragon_allowed(system.platform) and has_dragon_lan(devices, tables)
    if num == "670":
        return "-CF" in system.system_version.upper()
    if num == "672":
        return True
    if num == "673":
        return system.model.upper() != SMART_BACKUP_EXCLUDED
    if num == "677":
        return not amd and xtu_supported(system.cpu_name)
    if num == "2690":
        return system.part_number.upper() == AI_BOOST_PART
    if num == "3710":
        return intel_ai_oc(system)
    if num == "3711":
        return amd_ai_oc(system)
    if num == "4719":
        return amd and "-XT2" in system.system_version.upper()
    return num == CHROME


def dragon_allowed(platform):
    """Dragon is off for AMD 8-series and for Intel 800 and newer."""
    return platform != AM5_NEW_PLATFORM and package_file(platform) not in ("Package-Intel-800", "Package-Intel-900")


def intel_ai_oc(system):
    version = system.system_version.upper()
    return "-ICO" in version or "-IDO" in version


def amd_ai_oc(system):
    version = system.system_version.upper()
    return "-ACO" in version or "-ADO" in version


def xtu_supported(cpu_name):
    """A K or X after the dash, as in i7-14700K, not as the first character."""
    at = cpu_name.find("-")
    if at < 0:
        return False
    suffix = cpu_name[at + 1:]
    return suffix.find("K") > 0 or suffix.find("X") > 0


def read_registry(view, path, name):
    try:
        access = winreg.KEY_READ | (int(view) & HKLM_VIEW_MASK)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path.replace("\\\\", "\\").strip("\\"), 0, access) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except (OSError, ValueError):
        return None


def read_registry_int(path, name):
    try:
        return int(read_registry(0, path, name) or 0)
    except ValueError:
        return 0


def find_uninstall_version(view, display_name):
    """Exact DisplayName."""
    try:
        access = winreg.KEY_READ | (int(view) & HKLM_VIEW_MASK)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0, access) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                with winreg.OpenKey(root, winreg.EnumKey(root, i), 0, access) as key:
                    try:
                        if str(winreg.QueryValueEx(key, "DisplayName")[0]) == display_name:
                            return str(winreg.QueryValueEx(key, "DisplayVersion")[0])
                    except OSError:
                        continue
    except (OSError, ValueError):
        pass
    return None


def installed_version(row, package, device_version):
    """None means the version can't be read, so no update is offered."""
    try:
        source = int(row["Driver_Ver"])
    except ValueError:
        return None
    if source == VER_FROM_REGISTRY:
        if not package or not package["RegistryKeyPath"] or not package["RegistryKeyName"]:
            return None
        name = package["RegistryKeyName"]
        value = read_registry(package["RegistryKeyView"], package["RegistryKeyPath"], name)
        if value is None and name == RAID_NAME:
            value = read_registry(package["RegistryKeyView"], package["RegistryKeyPath"], RAID_FALLBACK_NAME)
        return value or NOT_INSTALLED
    if source == VER_FROM_UNINSTALL:
        if not package or not package["DisplayName"]:
            return None
        return find_uninstall_version(package["RegistryKeyView"], package["DisplayName"]) or NOT_INSTALLED
    return device_version


def app_installed_version(row, package):
    """XTU is read by name and everything else by installed_version."""
    if row["Num"] == "677":
        return find_uninstall_version(winreg.KEY_WOW64_64KEY, XTU_NAME) or NOT_INSTALLED
    return installed_version(row, package, "")


def http(url, method="GET", byte_range=None):
    if not url.startswith(UPDATE_ROOT):
        raise ToolError(f"Refusing URL outside the update path: {url}")
    headers = {"User-Agent": USER_AGENT}
    if byte_range:
        headers["Range"] = f"bytes=0-{byte_range - 1}"
    request = urllib.request.Request(url, method=method, headers=headers)
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def pe_has_signature(head):
    """Reads the PE security directory from the start of a file, without downloading the rest."""
    pe = struct.unpack_from("<I", head, 0x3C)[0]
    is_pe32 = struct.unpack_from("<H", head, pe + 24)[0] == PE32_MAGIC
    directory = pe + 24 + (PE32_DIRECTORIES if is_pe32 else PE32_PLUS_DIRECTORIES) + PE_SECURITY_ENTRY
    return struct.unpack_from("<I", head, directory + 4)[0] > 0


def has_signature(url):
    with http(url, byte_range=PE_HEADER_BYTES) as response:
        return pe_has_signature(response.read(PE_HEADER_BYTES))


def remote_size(url):
    """0 means the file isn't on the server."""
    try:
        with http(url, "HEAD") as response:
            return int(response.headers.get("Content-Length") or 0)
    except urllib.error.URLError:
        return 0


def fetch_tables(platform):
    """ver2.ini names the folder of the current tables."""
    with http(BASE_URL + "Drvdata/ver2.ini") as response:
        raw = response.read()
    parser = configparser.ConfigParser()
    parser.read_string(raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"))
    folder = parser.get("Info", "path")
    if not folder.isdigit():
        raise ToolError(f"Unexpected data version: {folder!r}")
    tables = {}
    for name in (*TABLES, package_file(platform)):
        cached = HOME / "drvdata" / folder / f"{name}.csv"
        if not cached.exists():
            with http(f"{BASE_URL}Drvdata/{folder}/{name}.csv") as response:
                content = response.read()
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(content)
        text = cached.read_bytes().decode("utf-8-sig", "replace")
        tables[name] = list(csv.DictReader(io.StringIO(text)))
    return folder, tables


def module_names(text):
    """The module list holds one JSON object per line, several classes can share a module."""
    names = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            name = json.loads(line).get("LIB")
            if name and name not in names:
                names.append(name)
    return names


def parse_manifest(text):
    """Returns (ApName, Ver, Size, DisplayName, description) from a module's ver2.ini."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(text)
    return (parser.get("Info", "ApName", fallback=""), parser.get("Info", "Ver", fallback=""),
            parser.getint("Info", "Size", fallback=0), parser.get("Info", "DisplayName", fallback=""),
            parser.get("Description", "eng", fallback=""))


def module_url(name, version):
    return MODULE_URL + urllib.parse.quote(f"{name}/{name}_{version}.exe")


def module_item(name, manifest_text, size_of, installed):
    """A wrong name or a size that disagrees with the file is skipped."""
    ap_name, version, size, display, description = parse_manifest(manifest_text)
    if ap_name != name or not version or size <= 0:
        return None
    url = module_url(name, version)
    if size_of(url) != size:
        return None
    return Item(num=name, name=display or name, kind="module", installed=installed, latest=version, url=url, size=size,
                offered=is_newer(version, installed), args=("/S",), description=" ".join(description.split()))


def fetch_modules(system):
    """Lists every module with its own version file."""
    items = []
    try:
        with http(MODULE_URL + "ver2.ini") as response:
            names = module_names(response.read().decode("utf-8-sig", "replace"))
    except (urllib.error.URLError, ValueError, OSError):
        return items
    for name in names:
        if name == NOTEBOOK_MODULE and not system.laptop:
            continue
        try:
            with http(MODULE_URL + urllib.parse.quote(f"{name}/ver2.ini")) as response:
                text = response.read().decode("utf-8-sig", "replace")
            installed = read_registry(winreg.KEY_WOW64_64KEY, MODULE_KEY, f"{name} Version") or NOT_INSTALLED
            item = module_item(name, text, remote_size, installed)
            # verify_signature would refuse an unsigned file after the full download, so it isn't an option
            if item and has_signature(item.url):
                items.append(item)
        except (urllib.error.URLError, configparser.Error, ValueError, OSError, struct.error):
            continue
    return items


def install_order(chosen):
    """Control Center first when chosen, because the other modules install into it, then modules, then the rest."""
    return sorted(chosen, key=lambda i: (i.num != HOST_MODULE, i.kind != "module"))


def control_center_installed(items):
    host = next((i for i in items if i.num == HOST_MODULE), None)
    if host:
        return host.installed != NOT_INSTALLED
    return read_registry(winreg.KEY_WOW64_64KEY, MODULE_KEY, f"{HOST_MODULE} Version") is not None


def modules_missing_host(items, chosen):
    """Chosen modules that won't work, because Control Center is neither installed nor being installed."""
    if control_center_installed(items) or any(i.num == HOST_MODULE for i in chosen):
        return []
    return [i for i in chosen if i.kind == "module"]


def has_modules(items):
    return any(i.kind == "module" for i in items)


def run_quiet(*command):
    return subprocess.run(command, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)


def service_running(name):
    return "RUNNING" in run_quiet("sc", "query", name).stdout


def prepare_modules():
    """Closes Control Center and stops its services. Returns the services to start again."""
    run_quiet("taskkill", "/IM", "GCC.exe", "/F")
    stopped = []
    for name in MODULE_SERVICES:
        if service_running(name):
            run_quiet("sc", "stop", name)
            stopped.append(name)
    for _ in range(SERVICE_WAIT_SECONDS):
        if not any(service_running(name) for name in stopped):
            break
        time.sleep(1)
    return stopped


def restore_modules(stopped):
    """Restarts the stopped services in list order."""
    for name in MODULE_SERVICES:
        if name in stopped:
            run_quiet("sc", "start", name)


def apply_platform_overrides(tables, platform):
    """Uses a fixed RAID driver version on AM4 boards."""
    if platform == AM4_PLATFORM:
        for row in tables["DriverTable"]:
            if row["Num"] in AM4_RAID_DRIVERS:
                row["Current_Ver"] = AM4_RAID_VERSION


def powershell_json(script, env=None):
    wrapped = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; {script} | ConvertTo-Json -Depth 3 -Compress"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped],
        capture_output=True, encoding="utf-8", env={**os.environ, **(env or {})},
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise ToolError(f"PowerShell failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def as_list(value):
    return value if isinstance(value, list) else [] if value is None else [value]


def board_identity():
    """Reading the tag and project name needs administrator rights."""
    try:
        return powershell_json(
            "$g = [wmi]\"root\\WMI:GSA1_ACPIMethod.InstanceName='ACPI\\PNP0C14\\GSADEV0_0'\"; "
            "@{Tag=[string]$g.GsaGetFWTagString().Value; Project=[string]$g.GsaGetFWPrjString().Value}"
        ) or {}
    except (ToolError, json.JSONDecodeError):
        return {}


def read_system():
    board = powershell_json("Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product")
    if "gigabyte" not in (board["Manufacturer"] or "").lower():
        raise ToolError(f"Not a Gigabyte board: {board['Manufacturer']} {board['Product']}")
    identity = board_identity()
    model = identity.get("Project") or board["Product"]
    try:
        platform = platform_of(model)
    except ToolError:
        model = board["Product"]
        platform = platform_of(model)
    enclosure = powershell_json("(Get-CimInstance Win32_SystemEnclosure).ChassisTypes")
    return System(
        model=model,
        platform=platform,
        part_number=(identity.get("Tag") or "").strip(),
        system_version=read_registry(0, BIOS_KEY, "SystemVersion") or "",
        cpu_name=read_registry(0, CPU_KEY, "ProcessorNameString") or "",
        laptop=bool(LAPTOP_CHASSIS & set(as_list(enclosure))),
    )


def list_devices():
    """The first hardware ID of each present device, with its DriverVersion."""
    entities = powershell_json("Get-CimInstance Win32_PnPEntity | Select-Object PNPDeviceID,HardwareID")
    signed = powershell_json("Get-CimInstance Win32_PnPSignedDriver | Select-Object DeviceID,DriverVersion")
    versions = {(s["DeviceID"] or "").upper(): s["DriverVersion"] for s in as_list(signed)}
    devices = {}
    for entity in as_list(entities):
        hardware_ids = as_list(entity.get("HardwareID"))
        parsed = parse_hardware_id(hardware_ids[0]) if hardware_ids else None
        if parsed:
            version = versions.get((entity["PNPDeviceID"] or "").upper()) or NO_DRIVER
            devices.setdefault(parsed[:2], Device(parsed[0], parsed[1], version, parsed[2]))
    return list(devices.values())


def detect_hardware(hardware_ids, video_cards, disk_models):
    """video_cards holds (name, PNP ID) pairs. Names ending in Graphics are processor graphics on a Gigabyte board."""
    ids = [i.upper() for i in hardware_ids if i]
    return Hardware(
        rgb_controller=any(f"USB\\VID_048D&PID_{pid}" in i for i in ids for pid in RGB_CONTROLLER_PIDS),
        gigabyte_gpu=any(GIGABYTE_SUBSYSTEM_RE.search(pnp.upper()) and not name.strip().endswith("Graphics")
                         for name, pnp in video_cards),
        gigabyte_storage=any(word in model.upper() for model in disk_models for word in ("GIGABYTE", "AORUS")),
        gigabyte_monitor=any(i.startswith("MONITOR\\GBT") for i in ids),
    )


def read_hardware():
    data = powershell_json(
        "@{Ids=@(Get-CimInstance Win32_PnPEntity | ForEach-Object { $_.HardwareID | Select-Object -First 1 }); "
        "Video=@(Get-CimInstance Win32_VideoController | Select-Object Name,PNPDeviceID); "
        "Disks=@(Get-CimInstance Win32_DiskDrive | Select-Object -ExpandProperty Model)}"
    ) or {}
    video = [(v.get("Name") or "", v.get("PNPDeviceID") or "") for v in as_list(data.get("Video"))]
    return detect_hardware(as_list(data.get("Ids")), video, [d for d in as_list(data.get("Disks")) if d])


def build_item(row, kind, descriptions, installed, offered_if):
    url = driver_url(row)
    size = remote_size(url)
    shown = UNKNOWN if installed is None else NOT_INSTALLED if installed == NO_DRIVER else installed
    description = descriptions.get(row["Driver_Desp_Num"], {})
    return Item(
        num=row["Num"],
        name=" ".join((description.get("Driver_Desp") or "Unknown").split()),
        description=" ".join((description.get("Driver_Tip") or "").split()),
        kind=kind,
        installed=shown,
        latest=row["Current_Ver"],
        url=url,
        size=size,
        offered=size > 0 and offered_if,
        # Drivers and apps run with no switch, except Chrome
        args=("/S",) if row["Num"] == CHROME else (),
    )


def catalog_offered(installed, latest):
    """Refuses anything that would replace a newer or unreadable-as-older installed version."""
    return installed in (None, NO_DRIVER) or is_newer(latest, installed)


def scan(include_catalog=False):
    system = read_system()
    folder, tables = fetch_tables(system.platform)
    apply_platform_overrides(tables, system.platform)
    os_column = "Win1164" if sys.getwindowsversion().build >= 22000 else "W1064"
    # The platform file comes first and the first match wins
    packages = {}
    for row in tables[package_file(system.platform)] + tables["Package"]:
        packages.setdefault(row["DriverID"], row)
    descriptions = {r["Num"]: r for r in tables["DriverDesp"]}
    driver_rows = {r["Num"]: r for r in tables["DriverTable"]}
    system_project = is_system_project(system.part_number)
    devices = list_devices()

    items = []
    for row, device in matched_drivers(devices, tables, system.platform, os_column):
        installed = installed_version(row, packages.get(row["Num"]), device.version)
        newer = installed is not None and is_newer(row["Current_Ver"], installed)
        items.append(build_item(row, "driver", descriptions, installed, newer and not system_project))

    # Offering newer versions of installed apps keeps them updatable
    known = {item.num for item in items}
    for num, package in packages.items():
        row = driver_rows.get(num)
        if (not row or num in known or package["PackageType"] in (PACKAGE_TYPE_DRIVER, PACKAGE_TYPE_FIRMWARE)
                or not app_listed(num, system, devices, tables, system_project)):
            continue
        installed = app_installed_version(row, package)
        newer = installed is not None and is_newer(row["Current_Ver"], installed)
        items.append(build_item(row, "app", descriptions, installed, newer))

    items.extend(fetch_modules(system))

    # Beyond what's detected: the other Windows-compatible table rows
    if include_catalog:
        known = {item.num for item in items}
        for row in tables["DriverTable"]:
            package = packages.get(row["Num"])
            if (row["Num"] in known or row[os_column] != "1"
                    or (package and package["PackageType"] == PACKAGE_TYPE_FIRMWARE)):
                continue
            installed = installed_version(row, package, NO_DRIVER)
            items.append(build_item(row, "catalog", descriptions, installed,
                                    catalog_offered(installed, row["Current_Ver"])))

    # An item whose file isn't on the server can't be installed, so it isn't an option
    items = [i for i in items if i.size > 0]
    items.sort(key=lambda i: (KIND_ORDER[i.kind], i.name.lower()))
    return ScanResult(system, folder, items, read_hardware())


def window_items(items):
    """Detected drivers, Control Center's modules and optional apps, without the catalog."""
    return [i for i in items if i.kind != "catalog"]


def type_of(item):
    if item.kind == "module":
        return "Module"
    return "Software" if item.kind == "app" or "/UTILITY/" in item.url else "Driver"


def would_downgrade(item):
    """True when the installed version is readable and the file isn't known to be newer or the same."""
    if item.installed in (NOT_INSTALLED, UNKNOWN) or item.installed == item.latest:
        return False
    return not is_newer(item.latest, item.installed)


def status_of(item):
    if item.offered:
        if item.kind == "catalog":
            return "CATALOG"
        if item.installed != NOT_INSTALLED:
            return "UPDATE"
        return "NEW" if item.kind == "driver" else "OPTIONAL"
    return "unknown" if item.installed == UNKNOWN else "ok"


def print_items(items):
    width = max([len(i.num) for i in items] + [4]) + 2
    print(f"{'ID':<{width}}{'Type':<9}{'Name':<46}{'Installed':<18}{'Latest':<18}{'MB':>7}  Status")
    for i in items:
        print(f"{i.num:<{width}}{i.kind:<9}{i.name[:44]:<46}{i.installed[:16]:<18}{i.latest[:16]:<18}"
              f"{i.size / 1e6:>7.1f}  {status_of(i)}")


def console_progress(name, done, total):
    print(f"\r  {name}: {done / 1e6:.1f} / {total / 1e6:.1f} MB", end="\n" if done >= total else "", flush=True)


def download(item, progress=console_progress, cancelled=None, say=print):
    """Downloads a file, then checks its size and signature."""
    name = urllib.parse.unquote(item.url.rsplit("/", 1)[1])
    if not SAFE_NAME_RE.match(name):
        raise ToolError(f"Unsafe file name from server: {name!r}")
    target = HOME / "downloads" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target.exists() and target.stat().st_size == item.size):
        partial = target.with_name(name + ".part")
        done = 0
        stopped = False
        with http(item.url) as response, open(partial, "wb") as out:
            while chunk := response.read(1 << 20):
                if cancelled and cancelled():
                    stopped = True
                    break
                out.write(chunk)
                done += len(chunk)
                progress(name, done, item.size)
        if stopped:
            partial.unlink()
            raise Cancelled(f"Cancelled during download: {name}")
        if done != item.size:
            partial.unlink()
            raise ToolError(f"Size mismatch for {name}: expected {item.size}, got {done}")
        partial.replace(target)
    verify_signature(target, say)
    return target


def verify_signature(path, say=print):
    signature = powershell_json(
        "$s = Get-AuthenticodeSignature -LiteralPath $env:GBT_FILE; "
        "@{Status=[string]$s.Status; Signer=[string]$s.SignerCertificate.Subject}",
        {"GBT_FILE": str(path)},
    )
    if signature["Status"] != "Valid":
        path.unlink(missing_ok=True)
        raise ToolError(f"Rejected {path.name}: signature status {signature['Status']}")
    signer = signer_name(signature["Signer"])
    if not signer_allowed(signer):
        path.unlink(missing_ok=True)
        raise ToolError(f"Rejected {path.name}: unexpected signer {signer}. See ALLOWED_SIGNERS.")
    say(f"  Signature valid: {signer}")


def signer_name(subject):
    match = re.search(r'CN=(?:"([^"]+)"|([^,]+))', subject)
    return (match.group(1) or match.group(2)).strip() if match else subject


def signer_allowed(name):
    return name.upper().startswith(ALLOWED_SIGNERS)


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def create_restore_point():
    """Returns (created, detail). Windows skips it if one was made in the last 24 hours."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Checkpoint-Computer -Description 'Gigabyte Update Utility' -RestorePointType MODIFY_SETTINGS -WarningAction Continue"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    detail = (result.stdout + result.stderr).strip()
    return result.returncode == 0 and not detail, detail or str(result.returncode)


def run_installer(path, args=()):
    """Waits for the installer and returns its exit code."""
    return subprocess.run([str(path), *args], cwd=path.parent).returncode


def install(items, skip_restore_point):
    """One at a time, with each item's installer switch."""
    if not is_admin():
        raise ToolError("Installing needs an administrator terminal.")
    if not skip_restore_point:
        created, detail = create_restore_point()
        print("Restore point created." if created else f"WARNING: restore point may not have been created: {detail}")
    failed = 0
    stopped = prepare_modules() if has_modules(items) else []
    try:
        for item in items:
            print(f"\n{item.name} {item.installed} -> {item.latest}")
            path = download(item)
            print("  Running installer. It may show its own window.")
            code = run_installer(path, item.args)
            if code == RESTART_REQUIRED:
                print("  Done, restart required.")
            elif code == 0:
                print("  Installer finished.")
            else:
                failed += 1
                print(f"  Installer exited with code {code}.")
    finally:
        restore_modules(stopped)
    print("\nRun 'check' after any restart to confirm the new versions.")
    return 1 if failed else 0


def select(items, ids, include_apps):
    """Without IDs: updates, optional items with --all, and never catalog items."""
    if not ids:
        wanted = ("UPDATE", "NEW", "OPTIONAL") if include_apps else ("UPDATE", "NEW")
        return [i for i in items if status_of(i) in wanted]
    chosen = [i for i in items if i.num in ids and i.offered]
    missing = set(ids) - {i.num for i in chosen}
    if missing:
        raise ToolError(f"Not offered for this PC: {', '.join(sorted(missing))}")
    return chosen


def select_for_download(items, ids, include_apps):
    """A listed item can be downloaded by ID even when it is already up to date."""
    if not ids:
        return select(items, ids, include_apps)
    chosen = [i for i in items if i.num in ids]
    missing = set(ids) - {i.num for i in chosen}
    if missing:
        raise ToolError(f"Not available for this PC: {', '.join(sorted(missing))}")
    return chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("check", help="list everything available for this PC")
    get = commands.add_parser("download", help="download and verify without installing")
    run = commands.add_parser("install", help="download, verify and install (administrator)")
    for sub in (get, run):
        sub.add_argument("ids", nargs="*", help="IDs from check; default all offered drivers")
        sub.add_argument("--all", action="store_true", help="also include the optional apps")
    for sub in (show, get, run):
        sub.add_argument("--catalog", action="store_true",
                         help="also list drivers not detected for this PC; these only install by ID")
    run.add_argument("--yes", action="store_true", help="skip the confirmation")
    run.add_argument("--no-restore-point", action="store_true")
    args = parser.parse_args()

    try:
        result = scan(args.catalog)
        items = result.items
        print(f"Board: {result.system.model}   Driver data: {result.folder}")
        if not result.system.part_number:
            print(BOARD_TAG_NOTE)
        print()
        print_items(items)
        if args.command == "check":
            return 0
        if args.command == "download":
            for item in select_for_download(items, args.ids, args.all):
                print(f"\n{item.name}")
                print(f"  Saved: {download(item)}")
            return 0
        chosen = install_order(select(items, args.ids, args.all))
        if not chosen:
            print("\nNothing to install.")
            return 0
        print("\nTo install: " + ", ".join(i.name for i in chosen))
        unmatched = [i.name for i in chosen if i.kind == "catalog"]
        if unmatched:
            print("Not matched to hardware detected on this PC: " + ", ".join(unmatched))
        stranded = modules_missing_host(items, chosen)
        if stranded:
            print(f"WARNING: {MISSING_HOST_NOTE}: " + ", ".join(i.name for i in stranded))
        elif has_modules(chosen):
            print(MODULE_NOTE)
        if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return 0
        return install(chosen, args.no_restore_point)
    except (ToolError, urllib.error.URLError, OSError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
