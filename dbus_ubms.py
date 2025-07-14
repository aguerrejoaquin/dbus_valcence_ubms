#!/usr/bin/env python3

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

VERSION = "2.2.1"

class DbusBatteryService:
    def __init__(self, servicename, deviceinstance, voltage, capacity, modules=16, strings=4, connection="can0"):
        self._bat = UbmsBattery(capacity=capacity, voltage=voltage, connection=connection)
        self.modules = modules
        self.strings = strings
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
        self._dbusservice.add_path("/FirmwareVersion", "1.0")
        self._dbusservice.add_path("/HardwareVersion", "type: UBMS rev. 0x1")
        self._dbusservice.add_path("/Connected", 1)
        self._dbusservice.add_path("/State", 14, writeable=True)
        self._dbusservice.add_path("/Mode", 1, writeable=True)
        self._dbusservice.add_path("/Soh", 100)
        self._dbusservice.add_path("/Capacity", int(capacity))
        self._dbusservice.add_path("/InstalledCapacity", int(capacity))
        self._dbusservice.add_path("/Dc/0/Temperature", 25)
        self._dbusservice.add_path("/Info/MaxChargeCurrent", 0)
        self._dbusservice.add_path("/Info/MaxDischargeCurrent", 0)
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
        # --- Gather cell voltages from battery object (list of tuples in mV) ---
        cellVoltages = getattr(self._bat, "cellVoltages", [])
        # Defensive: ensure cellVoltages is a list of lists, not list of tuples
        if cellVoltages and isinstance(cellVoltages[0], tuple):
            cellVoltages = [list(tup) for tup in cellVoltages]

        cell_voltages = []
        pack_voltage = 0.0
        for m, mod in enumerate(cellVoltages):
            for c, v in enumerate(mod):
                v_v = v / 1000.0 if v is not None else 0.0  # convert mV to V
                cell_voltages.append((v_v, m+1, c+1))
                pack_voltage += v_v
                cell_idx = m * self.cells_per_module + c
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Voltage"] = v_v

        # Min/max cell voltage and location
        cell_voltages_nonzero = [x for x in cell_voltages if x[0] > 0]
        if cell_voltages_nonzero:
            min_v, min_m, min_c = min(cell_voltages_nonzero, key=lambda x: x[0])
            max_v, max_m, max_c = max(cell_voltages_nonzero, key=lambda x: x[0])
        else:
            min_v = max_v = 0.0
            min_m = min_c = max_m = max_c = 0

        # No cell temperatures, so always 0
        min_t = max_t = 0.0
        min_tm = min_tc = max_tm = max_tc = 0

        # Print debug
        print("--- VALUES TO DBUS ---")
        print("Voltage (pack):", pack_voltage)
        print("Min cell voltage:", min_v, f"M{min_m}C{min_c}")
        print("Max cell voltage:", max_v, f"M{max_m}C{max_c}")
        print("----------------------")

        # Send to D-Bus
        self._dbusservice["/Dc/0/Voltage"] = pack_voltage
        self._dbusservice["/Dc/0/Current"] = 0.0
        self._dbusservice["/Soc"] = 0
        self._dbusservice["/Dc/0/Power"] = 0.0
        self._dbusservice["/Dc/0/Temperature"] = 25.0

        self._dbusservice["/System/MinVoltageCell"] = min_v
        self._dbusservice["/System/MinVoltageCellId"] = f"M{min_m}C{min_c}" if min_m and min_c else "M_C_"
        self._dbusservice["/System/MaxVoltageCell"] = max_v
        self._dbusservice["/System/MaxVoltageCellId"] = f"M{max_m}C{max_c}" if max_m and max_c else "M_C_"
        self._dbusservice["/System/MinCellTemperature"] = 0.0
        self._dbusservice["/System/MinCellTemperatureId"] = "M_C_"
        self._dbusservice["/System/MaxCellTemperature"] = 0.0
        self._dbusservice["/System/MaxCellTemperatureId"] = "M_C_"

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
