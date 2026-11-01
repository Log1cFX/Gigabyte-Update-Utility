# Gigabyte Update Utility

Unofficial Windows app that finds and installs drivers and software for Gigabyte motherboards, without Gigabyte Control Center. It explains what each item does and whether you need it.

> **Not affiliated with or endorsed by GIGABYTE.** "Gigabyte" and related names belong to their owner. This project contains no Gigabyte code or data files. It downloads them from Gigabyte's servers when you run it.

> **Experimental.** It has only been run on one PC, a B550M DS3H on Windows 11, where one real install completed successfully. Other boards, module installs and restoring from its restore points are untested. Installing drivers can make a PC unstable, so use it at your own risk.

## Getting started

**Packaged app:** download `GigabyteUpdateUtility.exe` from [Releases](../../releases) and run it. You don't need Python. The file isn't code-signed yet, so Windows SmartScreen will probably warn about an unknown publisher.

**From source:** install Python 3.13 and run:

```
pyw -3.13 gigabyte_update_utility_ui.pyw
```

Approve the administrator prompt. If you decline, the window still opens but can't install anything.

## Using the window

**Updates** lists software already on the PC. **Update All** installs every `UPDATE` row, and it's greyed out when there are none.

**Downloads** lists software you can add, sorted by whether you need it:

| Section | Status | What it means |
|---|---|---|
| You need these | `NEW` | Hardware in this PC has no driver version recorded. **Install Needed** installs these |
| These will make your PC better | `RECOMMENDED` | It matches hardware found in this PC |
| Totally optional | `OPTIONAL` | Only useful if you own the product or use the software described |
| Control Center modules | Any of these | Plugins grouped by theme, such as Lighting or Graphics. They do nothing without Control Center |

- Every row has a hint and a box. Tick any box and the main button becomes **Install Selected**, which installs exactly the ticked rows, including up-to-date ones.
- **Select all** takes every row in Updates, but skips `OPTIONAL` rows in Downloads.
- If a file is older than the installed version, the window asks first.
- Control Center is never installed for you. If a module needs it and it's missing, the window warns you.
- If Control Center is installed, installing a module closes it and stops `EasyTuneEngineService`, `AorusLcdService`, `GBTECService` and `Gdrv3`, then restarts those services.
- Each run tries to create a restore point, then runs one installer at a time. If an item fails, the rest continue.
- **Cancel** stops after the current step. A running installer can't be stopped.

Downloads and logs go to `%LOCALAPPDATA%\Gigabyte Update Utility`, which is safe to delete.

## What it won't do

- **BIOS updates:** none are listed, for motherboards or graphics cards.
- **Revert a driver:** use the restore point or Device Manager's **Roll Back Driver**.
- **List unavailable items:** anything whose file isn't on Gigabyte's server is hidden.
- **Run unsigned installers:** a file must have a valid signature from Gigabyte, or it's refused and deleted. Module installers with no signature aren't listed.

## How accurate the guidance is

- `NEW` can be wrong. Some drivers only record their version if a Gigabyte installer put them there, so a working driver from elsewhere can look missing.
- `RECOMMENDED` is a best guess from what Windows reports: a Gigabyte lighting controller, graphics card, drive or monitor, or a laptop. Mice, keyboards and coolers can't be detected this way, so they stay optional.
- Some hints are educated guesses from module names, because Gigabyte's own descriptions say little.
- Every module is listed, except the notebook module on desktops, because Control Center's hardware map isn't available separately.

## Command line

```
py -3.13 gigabyte_update_utility.py check [--catalog]
py -3.13 gigabyte_update_utility.py download [IDs] [--all] [--catalog]
py -3.13 gigabyte_update_utility.py install [IDs] [--all] [--catalog] [--yes] [--no-restore-point]
```

- `check` lists items and changes nothing.
- `download` saves and verifies files without installing.
- `install` needs an administrator terminal and only installs what's offered.
- `--all` includes optional apps and modules.
- `--catalog` also lists other Windows-compatible drivers, whether or not the hardware is present. Those only install by ID, and many are for other platforms.

## Limits

- It uses Gigabyte's undocumented update service, which can change or stop working at any time.
- Some special cases for other boards aren't implemented, mainly Intel and Aquantia LAN, Intel DTT and one X3D firmware check.
- Without administrator rights it can't read the board's tag, so the A.I. Boost and prebuilt-system rules aren't applied.

## Building and testing

Build `dist\GigabyteUpdateUtility.exe`:

```
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

Run the tests, which use sample data and run no real installers:

```
py -3.13 -m unittest
```

## AI disclosure

This project was built almost entirely with AI, using Claude Opus 5.5 from Anthropic in Claude Code. A person set the goals, made the product decisions and tried the app, including one real install.

- **Written by AI:** the application code, tests, build script, hints, this README and the script that drew the icon.
- **Analysis by AI:** the AI worked out the update behavior by analyzing Gigabyte's software and its public update files.
- **Tested by AI:** the AI wrote and ran the automated tests and checked the window with live data. The limits at the top of this page still apply.

AI can make mistakes that look correct, and you shouldn't assume every line has had a detailed human review. Please check anything important yourself before relying on it, and report problems as issues.

## License

This project's own code is released under the [MIT License](LICENSE). That doesn't cover Gigabyte's software or trademarks, or the files this app downloads from Gigabyte. The packaged `.exe` also includes Python and Tcl/Tk under their own licenses.
