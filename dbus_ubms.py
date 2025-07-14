#!/usr/bin/env python3

"""
Put a battery service on the dbus, according to Victron standards, with constantly updating paths.
Adapted for arbitrary modules, strings, and cells-per-module on can0 for Venus OS compatibility.
"""
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

VERSION = "1.2.0"

class DbusBatteryService:
    def __init__(
        self, servicename, deviceinstance, voltage, capacity, modules=16, strings=4, cells_per_module=8, connection="can0"
    ):
        self._bat = UbmsBattery(
            capacity=capacity,
            voltage=voltage,
            modules=modules,
            strings=strings,
            cells_per_module=cells_per_module,
            connection=connection
        )
        self.modules = modules
        self.strings = strings
        self.cells_per_module = cells_per_module

        self._dbusservice = VeDbusService(
            f"{servicename}.socketcan_{connection}_di{deviceinstance}", register=False
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
        self._dbusservice.add_path("/System/NrOfModules", self.modules)
        self._dbusservice.add_path("/System/NrOfStrings", self.strings)
        self._dbusservice.add_path("/System/NrOfModulesPerString", self.modules // self.strings)
        self._dbusservice.add_path("/System/NrOfCellsPerModule", self.cells_per_module)
        self._dbusservice.add_path("/System/NrOfCells", self.modules * self.cells_per_module)

        # Real-time values
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)

        # Per-module SoC and Temperature
        for idx in range(self.modules):
            self._dbusservice.add_path(f"/System/Module/{idx+1}/Soc", 0.0)
            self._dbusservice.add_path(f"/System/Module/{idx+1}/Temperature", 0.0)

        # Per-string SoC and Temperature
        for idx in range(self.strings):
            self._dbusservice.add_path(f"/System/String/{idx+1}/Soc", 0.0)
            self._dbusservice.add_path(f"/System/String/{idx+1}/Temperature", 0.0)

        # Per-cell Voltage and Temperature (flat index; you can also group by module if you want)
        for mod in range(self.modules):
            for cell in range(self.cells_per_module):
                cell_idx = mod * self.cells_per_module + cell
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Voltage", 0.0)
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Temperature", 0.0)

        GLib.timeout_add_seconds(1, self.update)

    def update(self):
        voltage = self._bat.get_total_voltage() / 1000.0  # Assuming cell voltages in mV
        soc = self._bat.get_soc()
        current = self._bat.get_current()
        temperature = self._bat.get_temperature()

        self._dbusservice["/Dc/0/Voltage"] = voltage
        self._dbusservice["/Dc/0/Current"] = current
        self._dbusservice["/Soc"] = soc
        self._dbusservice["/Dc/0/Power"] = voltage * current
        self._dbusservice["/Dc/0/Temperature"] = temperature

        # Update per-module SoC and Temperature
        for idx in range(self.modules):
            module_soc = self._bat.get_module_soc(idx) if hasattr(self._bat, 'get_module_soc') else (
                self._bat.moduleSocs[idx] if hasattr(self._bat, "moduleSocs") else soc)
            module_temp = self._bat.get_module_temperature(idx) if hasattr(self._bat, 'get_module_temperature') else (
                self._bat.moduleTemperatures[idx] if hasattr(self._bat, "moduleTemperatures") else temperature)
            self._dbusservice[f"/System/Module/{idx+1}/Soc"] = module_soc
            self._dbusservice[f"/System/Module/{idx+1}/Temperature"] = module_temp

        # Update per-string SoC and Temperature
        for idx in range(self.strings):
            string_soc = self._bat.get_string_soc(idx) if hasattr(self._bat, 'get_string_soc') else (
                self._bat.stringSocs[idx] if hasattr(self._bat, "stringSocs") else soc)
            string_temp = self._bat.get_string_temperature(idx) if hasattr(self._bat, 'get_string_temperature') else (
                self._bat.stringTemperatures[idx] if hasattr(self._bat, "stringTemperatures") else temperature)
            self._dbusservice[f"/System/String/{idx+1}/Soc"] = string_soc
            self._dbusservice[f"/System/String/{idx+1}/Temperature"] = string_temp

        # Update per-cell Voltage and Temperature
        for mod in range(self.modules):
            for cell in range(self.cells_per_module):
                cell_idx = mod * self.cells_per_module + cell
                cell_voltage = (
                    self._bat.get_cell_voltage(mod, cell)
                    if hasattr(self._bat, "get_cell_voltage")
                    else (
                        self._bat.cellVoltages[cell_idx]
                        if hasattr(self._bat, "cellVoltages") and len(self._bat.cellVoltages) > cell_idx
                        else voltage/self.cells_per_module
                    )
                )
                cell_temp = (
                    self._bat.get_cell_temperature(mod, cell)
                    if hasattr(self._bat, "get_cell_temperature")
                    else (
                        self._bat.cellTemperatures[cell_idx]
                        if hasattr(self._bat, "cellTemperatures") and len(self._bat.cellTemperatures) > cell_idx
                        else temperature
                    )
                )
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Voltage"] = cell_voltage
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Temperature"] = cell_temp

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
    parser.add_argument("--cells", type=int, default=8, help="Cells per module")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    DbusBatteryService(
        servicename="com.victronenergy.battery",
        deviceinstance=args.deviceinstance,
        voltage=args.voltage,
        capacity=args.capacity,
        modules=args.modules,
        strings=args.strings,
        cells_per_module=args.cells,
        connection=args.interface
    )
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
