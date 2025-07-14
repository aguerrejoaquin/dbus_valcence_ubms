#!/usr/bin/env python3

"""
dbus_ubms.py - Valence U-BMS D-Bus service for Venus OS
- Broadcasts cell voltages and temperatures from UbmsBattery to D-Bus in Victron format.
- Ensures min/max cell voltages and temperatures (plus their locations) always appear in Venus OS "Details".
- Shows debug output for all key values.
"""

import sys
import os
import platform
import logging
from argparse import ArgumentParser
from gi.repository import GLib
import dbus.mainloop.glib

# Insert velib_python path before importing VeDbusService
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService

from ubmsbattery import UbmsBattery

VERSION = "2.2.0-debug"

class DbusBatteryService:
    def __init__(self, servicename, deviceinstance, voltage, capacity, modules=16, strings=4, connection="can0"):
        self._bat = UbmsBattery(capacity=capacity, voltage=voltage, connection=connection)
        self.modules = modules
        self.strings = strings
        # Default: 4 cells/module, but override if battery object has the attribute
        self.cells_per_module = getattr(self._bat, "cellsPerModule", 4)

        self._dbusservice = VeDbusService(
            f"{servicename}.socketcan_{connection}_di{deviceinstance}", register=True
        )

        # Management objects
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion", VERSION + " running on Python " + platform.python_version())
        self._dbusservice.add_path("/Mgmt/Connection", connection)

        # Mandatory objects
        self._dbusservice.add_path("/DeviceInstance", deviceinstance)
        self._dbusservice.add_path("/ProductId", 0)
        self._dbusservice.add_path("/ProductName", "Valence U-BMS")
        self._dbusservice.add_path("/Manufacturer", "Valence")
        self._dbusservice.add_path("/FirmwareVersion", getattr(self._bat, "get_firmware_version", lambda: "1.0")())
        self._dbusservice.add_path("/HardwareVersion", f"type: {getattr(self._bat, 'get_bms_type', lambda: 'UBMS')()} rev. {hex(getattr(self._bat, 'get_hw_rev', lambda: 1)())}")
        self._dbusservice.add_path("/Connected", 1)
        self._dbusservice.add_path("/State", 14, writeable=True)
        self._dbusservice.add_path("/Mode", 1, writeable=True)
        self._dbusservice.add_path("/Soh", 100)
        self._dbusservice.add_path("/Capacity", int(capacity))
        self._dbusservice.add_path("/InstalledCapacity", int(capacity))
        self._dbusservice.add_path("/Dc/0/Temperature", 25)
        self._dbusservice.add_path("/Info/MaxChargeCurrent", getattr(self._bat, "maxChargeCurrent", 0))
        self._dbusservice.add_path("/Info/MaxDischargeCurrent", getattr(self._bat, "maxDischargeCurrent", 0))
        self._dbusservice.add_path("/Info/MaxChargeVoltage", float(voltage))
        self._dbusservice.add_path("/System/NrOfModules", modules)
        self._dbusservice.add_path("/System/NrOfStrings", strings)
        self._dbusservice.add_path("/System/NrOfModulesPerString", modules // strings)

        # Min/max cell voltage & temperature and IDs for Victron Details page
        self._dbusservice.add_path("/System/MinVoltageCell", 0.0)
        self._dbusservice.add_path("/System/MinVoltageCellId", "M_C_")
        self._dbusservice.add_path("/System/MaxVoltageCell", 0.0)
        self._dbusservice.add_path("/System/MaxVoltageCellId", "M_C_")
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperatureId", "M_C_")
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperatureId", "M_C_")

        # Real-time values
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)

        # Per-cell voltage and temperature paths (optional, for debugging/extra)
        for m in range(self.modules):
            for c in range(self.cells_per_module):
                cell_idx = m * self.cells_per_module + c
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Voltage", 0.0)
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Temperature", 0.0)

        GLib.timeout_add_seconds(1, self.update)

    def update(self):
        # 1. Print raw battery values
        print("=== BATTERY RAW DATA ===")
        print("cellVoltages:", getattr(self._bat, "cellVoltages", None))
        print("cellTemperatures:", getattr(self._bat, "cellTemperatures", None))
        print("get_total_voltage:", getattr(self._bat, "get_total_voltage", lambda: None)())
        print("get_soc:", getattr(self._bat, "get_soc", lambda: None)())
        print("get_current:", getattr(self._bat, "get_current", lambda: None)())
        print("get_temperature:", getattr(self._bat, "get_temperature", lambda: None)())
        print("========================")

        # 2. Use battery pack voltage as-is
        voltage = 0.0
        if hasattr(self._bat, "get_total_voltage"):
            voltage = self._bat.get_total_voltage()
            if voltage is not None and voltage > 100:  # likely mV
                voltage = voltage / 1000.0
        soc = getattr(self._bat, "get_soc", lambda: 0)()
        current = getattr(self._bat, "get_current", lambda: 0.0)()
        temperature = getattr(self._bat, "get_temperature", lambda: 25.0)()

        # 3. Find min/max cell voltage and location
        cell_voltages = []
        for m, mod in enumerate(getattr(self._bat, "cellVoltages", [])):
            for c, v in enumerate(mod):
                # If in mV, convert ONCE
                if v is not None and v > 100:
                    v = v / 1000.0
                cell_voltages.append((v, m+1, c+1))
                # Also update debug per-cell path
                cell_idx = m * self.cells_per_module + c
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Voltage"] = v if v is not None else 0.0
        cell_voltages_nonzero = [x for x in cell_voltages if x[0] and x[0] > 0]
        if cell_voltages_nonzero:
            min_v, min_m, min_c = min(cell_voltages_nonzero, key=lambda x: x[0])
            max_v, max_m, max_c = max(cell_voltages_nonzero, key=lambda x: x[0])
        else:
            min_v = max_v = 0.0
            min_m = min_c = max_m = max_c = 0

        # 4. Find min/max temperature and location
        cell_temps = []
        for m, mod in enumerate(getattr(self._bat, "cellTemperatures", [])):
            for c, t in enumerate(mod):
                cell_temps.append((t, m+1, c+1))
                cell_idx = m * self.cells_per_module + c
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Temperature"] = t if t is not None else 0.0
        cell_temps_nonzero = [x for x in cell_temps if x[0] and x[0] > 0]
        if cell_temps_nonzero:
            min_t, min_tm, min_tc = min(cell_temps_nonzero, key=lambda x: x[0])
            max_t, max_tm, max_tc = max(cell_temps_nonzero, key=lambda x: x[0])
        else:
            min_t = max_t = 0.0
            min_tm = min_tc = max_tm = max_tc = 0

        # 5. Print values to be sent to dbus
        print("--- VALUES TO DBUS ---")
        print("Voltage (pack):", voltage)
        print("Current:", current)
        print("SOC:", soc)
        print("Temperature:", temperature)
        print("Min cell voltage:", min_v, f"M{min_m}C{min_c}")
        print("Max cell voltage:", max_v, f"M{max_m}C{max_c}")
        print("Min cell temp:", min_t, f"M{min_tm}C{min_tc}")
        print("Max cell temp:", max_t, f"M{max_tm}C{max_tc}")
        print("----------------------")

        # 6. Now update the D-Bus
        self._dbusservice["/Dc/0/Voltage"] = voltage if voltage is not None else 0.0
        self._dbusservice["/Dc/0/Current"] = current if current is not None else 0.0
        self._dbusservice["/Soc"] = soc if soc is not None else 0
        self._dbusservice["/Dc/0/Power"] = (voltage if voltage is not None else 0.0) * (current if current is not None else 0.0)
        self._dbusservice["/Dc/0/Temperature"] = temperature if temperature is not None else 0.0

        self._dbusservice["/System/MinVoltageCell"] = min_v if min_v is not None else 0.0
        self._dbusservice["/System/MinVoltageCellId"] = f"M{min_m}C{min_c}" if min_m and min_c else "M_C_"
        self._dbusservice["/System/MaxVoltageCell"] = max_v if max_v is not None else 0.0
        self._dbusservice["/System/MaxVoltageCellId"] = f"M{max_m}C{max_c}" if max_m and max_c else "M_C_"
        self._dbusservice["/System/MinCellTemperature"] = min_t if min_t is not None else 0.0
        self._dbusservice["/System/MinCellTemperatureId"] = f"M{min_tm}C{min_tc}" if min_tm and min_tc else "M_C_"
        self._dbusservice["/System/MaxCellTemperature"] = max_t if max_t is not None else 0.0
        self._dbusservice["/System/MaxCellTemperatureId"] = f"M{max_tm}C{max_tc}" if max_tm and max_tc else "M_C_"

        return True

def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    parser = ArgumentParser()
    parser.add_argument("--capacity", "-c", type=int, default=650)
    parser.add_argument("--voltage", "-v", type=float, default=29.0)
    parser.add_argument("--interface", "-i", type=str, default="can0")
    parser.add_argument("--deviceinstance", "-d", type=int, default=0)
    parser.add_argument("--modules", type=int, default=16)
    parser.add_argument("--strings", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    DbusBatteryService(
        servicename="com.victronenergy.battery",
        deviceinstance=args.deviceinstance,
        voltage=args.voltage,
        capacity=args.capacity,
        modules=args.modules,
        strings=args.strings,
        connection=args.interface
    )
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
