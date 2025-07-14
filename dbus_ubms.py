#!/usr/bin/env python3

"""
Put a battery service on the dbus, according to Victron standards, with constantly updating paths.
Adapted for 16 modules and 4 strings on can0 for Venus OS compatibility.
"""

from gi.repository import GLib
import dbus.mainloop.glib
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

import platform
import logging
import sys
import os
from argparse import ArgumentParser

from ubmsbattery import UbmsBattery

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService

VERSION = "1.2.0"

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
        self._dbusservice.add_path("/FirmwareVersion", self._bat.get_firmware_version())
        self._dbusservice.add_path("/HardwareVersion", f"type: {self._bat.get_bms_type()} rev. {hex(self._bat.get_hw_rev())}")
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

        GLib.timeout_add_seconds(1, self.update)

    def update(self):
        # --- Cell voltage min/max logic ---
        cell_voltages = []
        cell_temps = []

        # Gather voltages and temps, and track their location
        for m in range(self.modules):
            for c in range(self.cells_per_module):
                v = 0.0
                t = 0.0
                if hasattr(self._bat, "cellVoltages") and m < len(self._bat.cellVoltages) and c < len(self._bat.cellVoltages[m]):
                    v = float(self._bat.cellVoltages[m][c]) * 0.001  # mV to V
                if hasattr(self._bat, "cellTemperatures") and m < len(self._bat.cellTemperatures) and c < len(self._bat.cellTemperatures[m]):
                    t = float(self._bat.cellTemperatures[m][c])
                elif hasattr(self._bat, "moduleTemps") and m < len(self._bat.moduleTemps):
                    t = float(self._bat.moduleTemps[m])
                cell_voltages.append((v, m + 1, c + 1))
                cell_temps.append((t, m + 1, c + 1))

        # Find min/max cell voltage and location (skip zeros)
        cell_voltages_nonzero = [x for x in cell_voltages if x[0] > 0]
        if cell_voltages_nonzero:
            min_v, min_m, min_c = min(cell_voltages_nonzero, key=lambda x: x[0])
            max_v, max_m, max_c = max(cell_voltages_nonzero, key=lambda x: x[0])
            self._dbusservice["/System/MinVoltageCell"] = min_v
            self._dbusservice["/System/MinVoltageCellId"] = f"M{min_m}C{min_c}"
            self._dbusservice["/System/MaxVoltageCell"] = max_v
            self._dbusservice["/System/MaxVoltageCellId"] = f"M{max_m}C{max_c}"
        else:
            self._dbusservice["/System/MinVoltageCell"] = 0.0
            self._dbusservice["/System/MinVoltageCellId"] = "M_C_"
            self._dbusservice["/System/MaxVoltageCell"] = 0.0
            self._dbusservice["/System/MaxVoltageCellId"] = "M_C_"

        # Find min/max cell temperature and location (skip zeros)
        cell_temps_nonzero = [x for x in cell_temps if x[0] > 0]
        if cell_temps_nonzero:
            min_t, min_m, min_c = min(cell_temps_nonzero, key=lambda x: x[0])
            max_t, max_m, max_c = max(cell_temps_nonzero, key=lambda x: x[0])
            self._dbusservice["/System/MinCellTemperature"] = min_t
            self._dbusservice["/System/MinCellTemperatureId"] = f"M{min_m}C{min_c}"
            self._dbusservice["/System/MaxCellTemperature"] = max_t
            self._dbusservice["/System/MaxCellTemperatureId"] = f"M{max_m}C{max_c}"
        else:
            self._dbusservice["/System/MinCellTemperature"] = 0.0
            self._dbusservice["/System/MinCellTemperatureId"] = "M_C_"
            self._dbusservice["/System/MaxCellTemperature"] = 0.0
            self._dbusservice["/System/MaxCellTemperatureId"] = "M_C_"

        # Normal battery values
        voltage = self._bat.get_total_voltage() / 1000.0 if hasattr(self._bat, "get_total_voltage") else 0.0
        soc = self._bat.get_soc() if hasattr(self._bat, "get_soc") else 0
        current = self._bat.get_current() if hasattr(self._bat, "get_current") else 0.0
        temperature = self._bat.get_temperature() if hasattr(self._bat, "get_temperature") else 25.0

        self._dbusservice["/Dc/0/Voltage"] = voltage
        self._dbusservice["/Dc/0/Current"] = current
        self._dbusservice["/Soc"] = soc
        self._dbusservice["/Dc/0/Power"] = voltage * current
        self._dbusservice["/Dc/0/Temperature"] = temperature

        return True  # Continue running

def main():
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
