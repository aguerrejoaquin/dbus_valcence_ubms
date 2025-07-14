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

VERSION = "2.0.0"

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
        self.cellsPerModule = getattr(self._bat, "cellsPerModule", 4)

        self._dbusservice = VeDbusService(
            f"{servicename}.socketcan_{connection}_di{deviceinstance}"
        )

        # Management & mandatory objects
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion", VERSION + " running on Python " + platform.python_version())
        self._dbusservice.add_path("/Mgmt/Connection", connection)
        self._dbusservice.add_path("/DeviceInstance", deviceinstance)
        self._dbusservice.add_path("/ProductId", 0)
        self._dbusservice.add_path("/ProductName", "Valence U-BMS")
        self._dbusservice.add_path("/Manufacturer", "Valence")
        self._dbusservice.add_path("/FirmwareVersion", str(getattr(self._bat, "firmwareVersion", "1.0")))
        self._dbusservice.add_path("/HardwareVersion", "type: {} rev. {}".format(getattr(self._bat, "bms_type", "UBMS"), hex(getattr(self._bat, "hw_rev", 1))))
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
        self._dbusservice.add_path("/Info/BatteryLowVoltage", 44.8)

        # Alarms
        self._dbusservice.add_path("/Alarms/CellImbalance", 0)
        self._dbusservice.add_path("/Alarms/LowVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighDischargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/HighChargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/LowSoc", 0)
        self._dbusservice.add_path("/Alarms/LowTemperature", 0)
        self._dbusservice.add_path("/Alarms/HighTemperature", 0)

        self._dbusservice.add_path("/Balancing", 0)
        self._dbusservice.add_path("/System/HasTemperature", 1)

        # System info
        self._dbusservice.add_path("/System/NrOfBatteries", self._bat.numberOfModules)
        self._dbusservice.add_path("/System/NrOfModulesOnline", self._bat.numberOfModules)
        self._dbusservice.add_path("/System/NrOfModulesOffline", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingDischarge", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingCharge", 0)
        self._dbusservice.add_path("/System/NrOfBatteriesBalancing", 0)
        self._dbusservice.add_path("/System/BatteriesParallel", self._bat.numberOfStrings)
        self._dbusservice.add_path("/System/BatteriesSeries", getattr(self._bat, "modulesInSeries", numberOfModules))
        self._dbusservice.add_path("/System/NrOfCellsPerBattery", self._bat.cellsPerModule)

        # Min/max cell voltage & temperature and IDs for Victron Details page
        self._dbusservice.add_path("/System/MinVoltageCell", 0.0)
        self._dbusservice.add_path("/System/MinVoltageCellId", "M_C_")
        self._dbusservice.add_path("/System/MaxVoltageCell", 0.0)
        self._dbusservice.add_path("/System/MaxVoltageCellId", "M_C_")
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperatureId", "M_C_")
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperatureId", "M_C_")
        self._dbusservice.add_path("/System/MaxPcbTemperature", 0.0)

        # Real-time values
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)

        # Per-module SoC and Temperature
        for idx in range(self.numberOfModules):
            self._dbusservice.add_path(f"/System/Module/{idx+1}/Soc", 0.0)
            self._dbusservice.add_path(f"/System/Module/{idx+1}/Temperature", 0.0)

        # Per-cell Voltage and Temperature
        for mod in range(self.numberOfModules):
            for cell in range(self.cellsPerModule):
                cell_idx = mod * self.cellsPerModule + cell
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Voltage", 0.0)
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Temperature", 0.0)

        GLib.timeout_add_seconds(1, self.update)

    def update(self):
        # Collect per-cell voltages and temperatures
        cell_voltages = []
        cell_temps = []
        pack_voltage = 0.0

        for mod in range(self.numberOfModules):
            for cell in range(self.cellsPerModule):
                cell_idx = mod * self.cellsPerModule + cell
                v = 0.0
                t = 0.0
                # Cell voltage
                if hasattr(self._bat, "cellVoltages") and mod < len(self._bat.cellVoltages) and cell < len(self._bat.cellVoltages[mod]):
                    v = float(self._bat.cellVoltages[mod][cell]) * 0.001
                # Cell temperature (if available)
                if hasattr(self._bat, "cellTemperatures") and mod < len(self._bat.cellTemperatures) and cell < len(self._bat.cellTemperatures[mod]):
                    t = float(self._bat.cellTemperatures[mod][cell])
                # Fallback to moduleTemp if available
                elif hasattr(self._bat, "moduleTemp") and mod < len(self._bat.moduleTemp):
                    t = float(self._bat.moduleTemp[mod])
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Voltage"] = v
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Temperature"] = t
                cell_voltages.append((v, mod + 1, cell + 1))
                cell_temps.append((t, mod + 1, cell + 1))
                if v > 0:
                    pack_voltage += v

        # Use provided pack voltage if available, else sum of cell voltages
        voltage = getattr(self._bat, "get_pack_voltage", lambda: 0.0)()
        if voltage <= 0 and pack_voltage > 0:
            voltage = pack_voltage

        soc = getattr(self._bat, "soc", 0)
        current = getattr(self._bat, "current", 0.0)

        # Use maxCellTemperature if available, else average min/max, else fallback to 0
        max_cell_temp = float(getattr(self._bat, "maxCellTemperature", 0.0))
        min_cell_temp = float(getattr(self._bat, "minCellTemperature", 0.0))
        battery_temp = max_cell_temp if max_cell_temp else (
            (max_cell_temp + min_cell_temp) / 2 if min_cell_temp else 0.0
        )

        self._dbusservice["/Dc/0/Voltage"] = voltage
        self._dbusservice["/Dc/0/Current"] = current
        self._dbusservice["/Soc"] = soc
        self._dbusservice["/Dc/0/Power"] = voltage * current
        self._dbusservice["/Dc/0/Temperature"] = battery_temp

        # Per-module SoC and Temperature
        for idx in range(self.numberOfModules):
            soc_mod = self._bat.moduleSoc[idx] if hasattr(self._bat, "moduleSoc") and idx < len(self._bat.moduleSoc) else 0.0
            temp_mod = self._bat.moduleTemp[idx] if hasattr(self._bat, "moduleTemp") and idx < len(self._bat.moduleTemp) else battery_temp
            self._dbusservice[f"/System/Module/{idx+1}/Soc"] = soc_mod
            self._dbusservice[f"/System/Module/{idx+1}/Temperature"] = temp_mod

        # Min/max cell voltage and location, skip zeros
        cell_voltages_nonzero = [x for x in cell_voltages if x[0] > 0]
        cell_temps_nonzero = [x for x in cell_temps if x[0] > 0]

        if cell_voltages_nonzero:
            min_v, min_mod, min_cell = min(cell_voltages_nonzero, key=lambda x: x[0])
            max_v, max_mod, max_cell = max(cell_voltages_nonzero, key=lambda x: x[0])
            self._dbusservice["/System/MinVoltageCell"] = float(min_v)
            self._dbusservice["/System/MinVoltageCellId"] = f"M{min_mod}C{min_cell}"
            self._dbusservice["/System/MaxVoltageCell"] = float(max_v)
            self._dbusservice["/System/MaxVoltageCellId"] = f"M{max_mod}C{max_cell}"
        else:
            self._dbusservice["/System/MinVoltageCell"] = 0.0
            self._dbusservice["/System/MinVoltageCellId"] = "M_C_"
            self._dbusservice["/System/MaxVoltageCell"] = 0.0
            self._dbusservice["/System/MaxVoltageCellId"] = "M_C_"

        if cell_temps_nonzero:
            min_t, min_mod_t, min_cell_t = min(cell_temps_nonzero, key=lambda x: x[0])
            max_t, max_mod_t, max_cell_t = max(cell_temps_nonzero, key=lambda x: x[0])
            self._dbusservice["/System/MinCellTemperature"] = float(min_t)
            self._dbusservice["/System/MinCellTemperatureId"] = f"M{min_mod_t}C{min_cell_t}"
            self._dbusservice["/System/MaxCellTemperature"] = float(max_t)
            self._dbusservice["/System/MaxCellTemperatureId"] = f"M{max_mod_t}C{max_cell_t}"
        else:
            self._dbusservice["/System/MinCellTemperature"] = 0.0
            self._dbusservice["/System/MinCellTemperatureId"] = "M_C_"
            self._dbusservice["/System/MaxCellTemperature"] = 0.0
            self._dbusservice["/System/MaxCellTemperatureId"] = "M_C_"

        self._dbusservice["/System/MaxPcbTemperature"] = float(getattr(self._bat, "maxPcbTemperature", 0.0))

        # Alarms
        self._dbusservice["/Alarms/LowVoltage"] = int(bool(cell_voltages_nonzero) and min_v < 2.5) if cell_voltages_nonzero else 0
        self._dbusservice["/Alarms/HighVoltage"] = int(bool(cell_voltages_nonzero) and max_v > 3.65) if cell_voltages_nonzero else 0
        self._dbusservice["/Alarms/LowTemperature"] = int(bool(cell_temps_nonzero) and min_t < 5) if cell_temps_nonzero else 0
        self._dbusservice["/Alarms/HighTemperature"] = int(bool(cell_temps_nonzero) and max_t > 60) if cell_temps_nonzero else 0
        self._dbusservice["/Alarms/CellImbalance"] = int(bool(cell_voltages_nonzero) and (max_v - min_v) > 0.08) if cell_voltages_nonzero else 0
        self._dbusservice["/Alarms/HighDischargeCurrent"] = 0  # Set real logic if available
        self._dbusservice["/Alarms/HighChargeCurrent"] = 0     # Set real logic if available
        self._dbusservice["/Alarms/LowSoc"] = int(soc < 10)

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
