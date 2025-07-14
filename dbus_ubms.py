#!/usr/bin/env python3

import sys
import os
import logging
import platform
from gi.repository import GLib
import dbus.mainloop.glib
from argparse import ArgumentParser

# Insert velib_python path before importing VeDbusService
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService

from ubmsbattery import UbmsBattery

VERSION = "1.4.0"

class DbusBatteryService:
    def __init__(
        self, servicename, deviceinstance, voltage, capacity, numberOfModules=16, numberOfStrings=4, connection="can0"
    ):
        self._bat = UbmsBattery(
            voltage=voltage,
            capacity=capacity,
            connection=connection,
            numberOfModules=numberOfModules,
            numberOfStrings=numberOfStrings
        )
        self.numberOfModules = numberOfModules
        self.numberOfStrings = numberOfStrings
        self.cellsPerModule = 4  # Fixed in UbmsBattery

        self._dbusservice = VeDbusService(
            f"{servicename}.socketcan_{connection}_di{deviceinstance}"
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
        self._dbusservice.add_path("/FirmwareVersion", self.get_firmware_version())
        self._dbusservice.add_path("/HardwareVersion", f"type: {self.get_bms_type()} rev. {hex(self.get_hw_rev())}")
        self._dbusservice.add_path("/Connected", 1)
        self._dbusservice.add_path("/State", 14, writeable=True)
        self._dbusservice.add_path("/Mode", 1, writeable=True)
        self._dbusservice.add_path("/Soh", 100)
        self._dbusservice.add_path("/Capacity", int(capacity))
        self._dbusservice.add_path("/InstalledCapacity", int(capacity))
        self._dbusservice.add_path("/Dc/0/Temperature", 25)
        self._dbusservice.add_path("/Info/MaxChargeCurrent", self._bat.maxChargeCurrent)
        self._dbusservice.add_path("/Info/MaxDischargeCurrent", self._bat.maxDischargeCurrent)
        self._dbusservice.add_path("/Info/MaxChargeVoltage", float(voltage))
        self._dbusservice.add_path("/System/NrOfModules", self.numberOfModules)
        self._dbusservice.add_path("/System/NrOfStrings", self.numberOfStrings)
        self._dbusservice.add_path("/System/NrOfModulesPerString", self._bat.modulesInSeries)
        self._dbusservice.add_path("/System/NrOfCellsPerModule", self.cellsPerModule)
        self._dbusservice.add_path("/System/NrOfCells", self.numberOfModules * self.cellsPerModule)

        # Real-time values
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)

        # Per-module SoC and Temperature
        for idx in range(self.numberOfModules):
            self._dbusservice.add_path(f"/System/Module/{idx+1}/Soc", 0.0)
            self._dbusservice.add_path(f"/System/Module/{idx+1}/Temperature", 0.0)

        # Per-cell Voltage (and dummy Temperature)
        for mod in range(self.numberOfModules):
            for cell in range(self.cellsPerModule):
                cell_idx = mod * self.cellsPerModule + cell
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Voltage", 0.0)
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Temperature", 0.0)

        # Min/Max cell voltage and their locations
        self._dbusservice.add_path("/System/Cell/MinVoltage", 0.0)
        self._dbusservice.add_path("/System/Cell/MinVoltageModule", 0)
        self._dbusservice.add_path("/System/Cell/MinVoltageCell", 0)
        self._dbusservice.add_path("/System/Cell/MaxVoltage", 0.0)
        self._dbusservice.add_path("/System/Cell/MaxVoltageModule", 0)
        self._dbusservice.add_path("/System/Cell/MaxVoltageCell", 0)

        # Alarms and errors
        self._dbusservice.add_path("/Alarms/LowVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighVoltage", 0)
        self._dbusservice.add_path("/Alarms/LowTemperature", 0)
        self._dbusservice.add_path("/Alarms/HighTemperature", 0)
        self._dbusservice.add_path("/Alarms/InternalError", 0)
        self._dbusservice.add_path("/Alarms/CellImbalance", 0)
        self._dbusservice.add_path("/Alarms/CommunicationError", 0)

        GLib.timeout_add_seconds(1, self.update)

    # Dummy info for compatibility
    def get_firmware_version(self):
        return str(getattr(self._bat, "firmwareVersion", "1.0"))
    def get_bms_type(self):
        return str(getattr(self._bat, "bms_type", "UBMS"))
    def get_hw_rev(self):
        return getattr(self._bat, "hw_rev", 1)

    def update(self):
        # Battery voltage = pack voltage (in V)
        voltage = self._bat.get_pack_voltage()
        soc = self._bat.soc
        current = self._bat.current
        temperature = self._bat.temperature

        self._dbusservice["/Dc/0/Voltage"] = voltage
        self._dbusservice["/Dc/0/Current"] = current
        self._dbusservice["/Soc"] = soc
        self._dbusservice["/Dc/0/Power"] = voltage * current
        self._dbusservice["/Dc/0/Temperature"] = temperature

        # Per-module SoC and Temperature
        for idx in range(self.numberOfModules):
            soc = self._bat.moduleSoc[idx] if idx < len(self._bat.moduleSoc) else 0
            temp = self._bat.moduleTemp[idx] if idx < len(self._bat.moduleTemp) else 0
            self._dbusservice[f"/System/Module/{idx+1}/Soc"] = soc
            self._dbusservice[f"/System/Module/{idx+1}/Temperature"] = temp

        # Per-cell Voltage & dummy Temperature
        cell_voltages = []
        for mod in range(self.numberOfModules):
            for cell in range(self.cellsPerModule):
                cell_idx = mod * self.cellsPerModule + cell
                if mod < len(self._bat.cellVoltages) and cell < len(self._bat.cellVoltages[mod]):
                    cell_voltage = self._bat.cellVoltages[mod][cell] * 0.001
                else:
                    cell_voltage = 0
                cell_voltages.append((cell_voltage, mod + 1, cell + 1))  # store for min/max
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Voltage"] = cell_voltage
                # As you have no per-cell temp, use module temp or 0 as fallback
                temp = self._bat.moduleTemp[mod] if mod < len(self._bat.moduleTemp) else 0
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Temperature"] = temp

        # Find min/max cell voltage and their locations
        min_v, min_mod, min_cell = min(cell_voltages, key=lambda x: x[0]) if cell_voltages else (0, 0, 0)
        max_v, max_mod, max_cell = max(cell_voltages, key=lambda x: x[0]) if cell_voltages else (0, 0, 0)

        self._dbusservice["/System/Cell/MinVoltage"] = min_v
        self._dbusservice["/System/Cell/MinVoltageModule"] = min_mod
        self._dbusservice["/System/Cell/MinVoltageCell"] = min_cell
        self._dbusservice["/System/Cell/MaxVoltage"] = max_v
        self._dbusservice["/System/Cell/MaxVoltageModule"] = max_mod
        self._dbusservice["/System/Cell/MaxVoltageCell"] = max_cell

        # Alarms (example logic, adapt to your protocol as needed)
        self._dbusservice["/Alarms/LowVoltage"] = int(min_v < 2.5)
        self._dbusservice["/Alarms/HighVoltage"] = int(max_v > 3.65)
        self._dbusservice["/Alarms/LowTemperature"] = int(getattr(self._bat, "minCellTemperature", 999) < 5)
        self._dbusservice["/Alarms/HighTemperature"] = int(getattr(self._bat, "maxCellTemperature", -999) > 60)
        self._dbusservice["/Alarms/InternalError"] = int(getattr(self._bat, "internalErrors", 0) != 0)
        self._dbusservice["/Alarms/CellImbalance"] = int((max_v - min_v) > 0.08)
        self._dbusservice["/Alarms/CommunicationError"] = int(getattr(self._bat, "numberOfModulesCommunicating", self.numberOfModules) < self.numberOfModules)

        return True  # Continue running

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
        numberOfModules=args.modules,
        numberOfStrings=args.strings,
        connection=args.interface
    )
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
