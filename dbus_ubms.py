#!/usr/bin/env python3

import sys
import os
import platform
import logging
import itertools
import math
from argparse import ArgumentParser
from gi.repository import GLib
import dbus.mainloop.glib

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService

from ubmsbattery import UbmsBattery

VERSION = "2.7.0"

class DbusBatteryService:
    def __init__(
        self,
        servicename,
        deviceinstance,
        voltage,
        capacity,
        productname="Valence U-BMS",
        connection="can0",
        modules=16,
        strings=4
    ):
        self._bat = UbmsBattery(
            capacity=capacity,
            voltage=voltage,
            connection=connection,
            numberOfModules=modules,
            numberOfStrings=strings
        )
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
        self._dbusservice.add_path("/ProductName", productname)
        self._dbusservice.add_path("/Manufacturer", "Valence")
        self._dbusservice.add_path("/FirmwareVersion", getattr(self._bat, "firmwareVersion", "1.0"))
        self._dbusservice.add_path("/HardwareVersion", f"type: {getattr(self._bat, 'bms_type', 'UBMS')} rev. {hex(getattr(self._bat, 'hw_rev', 1))}")
        self._dbusservice.add_path("/Connected", 1)
        self._dbusservice.add_path("/State", getattr(self._bat, "state", 14), writeable=True)
        self._dbusservice.add_path("/Mode", getattr(self._bat, "mode", 1), writeable=True)
        self._dbusservice.add_path("/Soh", 100)
        self._dbusservice.add_path("/Capacity", int(capacity))
        self._dbusservice.add_path("/InstalledCapacity", int(capacity))
        self._dbusservice.add_path("/Dc/0/Temperature", getattr(self._bat, "maxCellTemperature", 25))
        self._dbusservice.add_path("/Info/MaxChargeCurrent", getattr(self._bat, "maxChargeCurrent", 0))
        self._dbusservice.add_path("/Info/MaxDischargeCurrent", getattr(self._bat, "maxDischargeCurrent", 0))
        self._dbusservice.add_path("/Info/MaxChargeVoltage", float(voltage))
        self._dbusservice.add_path("/System/NrOfModules", modules)
        self._dbusservice.add_path("/System/NrOfStrings", strings)
        self._dbusservice.add_path("/System/NrOfModulesPerString", modules // strings)

        # Correct paths for Victron UI
        self._dbusservice.add_path("/System/MinCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MinVoltageCellId", "M1C1")
        self._dbusservice.add_path("/System/MaxCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MaxVoltageCellId", "M1C1")
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)

        # Standard alarms
        self._dbusservice.add_path("/Alarms/LowVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighVoltage", 0)
        self._dbusservice.add_path("/Alarms/LowTemperature", 0)
        self._dbusservice.add_path("/Alarms/HighTemperature", 0)
        self._dbusservice.add_path("/Alarms/InternalFailure", 0)
        self._dbusservice.add_path("/Alarms/HighChargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/HighDischargeCurrent", 0)

        # Per-cell voltages as expected by Victron
        for i in range(1, self.cells_per_module * self.modules + 1):
            self._dbusservice.add_path(f"/Voltages/Cell{i}", 0.0)

        GLib.timeout_add_seconds(1, self.update)

    def update(self):
        bat = self._bat
        pack_voltage = getattr(bat, "voltage", 0.0)
        pack_current = getattr(bat, "current", 0.0)
        soc = getattr(bat, "soc", 0)
        power = pack_voltage * pack_current
        state = getattr(bat, "state", 14)
        mode = getattr(bat, "mode", 1)
        max_cell_temp = getattr(bat, "maxCellTemperature", 0)
        min_cell_temp = getattr(bat, "minCellTemperature", 0)
        internal_errors = getattr(bat, "internalErrors", 0)

        cellVoltages = getattr(bat, "cellVoltages", [])
        # Flatten cell list
        flatVList = list(itertools.chain(*cellVoltages)) if cellVoltages else []
        # Update per-cell voltages
        for i, v in enumerate(flatVList):
            voltage = v / 1000.0 if v is not None else 0.0
            self._dbusservice[f"/Voltages/Cell{i+1}"] = voltage

        # Find min/max voltage and IDs
        if flatVList:
            max_v = max(flatVList) / 1000.0
            min_v = min(flatVList) / 1000.0
            max_index = flatVList.index(max(flatVList))
            min_index = flatVList.index(min(flatVList))
            m_max = math.floor(max_index / self.cells_per_module)
            c_max = max_index % self.cells_per_module
            m_min = math.floor(min_index / self.cells_per_module)
            c_min = min_index % self.cells_per_module
            max_id = f"M{m_max+1}C{c_max+1}"
            min_id = f"M{m_min+1}C{c_min+1}"
        else:
            max_v = min_v = 0.0
            max_id = min_id = "M1C1"

        self._dbusservice["/System/MaxCellVoltage"] = max_v
        self._dbusservice["/System/MaxVoltageCellId"] = max_id
        self._dbusservice["/System/MinCellVoltage"] = min_v
        self._dbusservice["/System/MinVoltageCellId"] = min_id
        self._dbusservice["/System/MaxCellTemperature"] = max_cell_temp
        self._dbusservice["/System/MinCellTemperature"] = min_cell_temp

        # D-Bus core
        self._dbusservice["/Dc/0/Voltage"] = pack_voltage
        self._dbusservice["/Dc/0/Current"] = pack_current
        self._dbusservice["/Soc"] = soc
        self._dbusservice["/Dc/0/Power"] = power
        self._dbusservice["/State"] = state
        self._dbusservice["/Mode"] = mode

        # Alarms (example thresholds, adjust for your battery)
        self._dbusservice["/Alarms/LowVoltage"] = 1 if pack_voltage < 44 else 0
        self._dbusservice["/Alarms/HighVoltage"] = 1 if pack_voltage > 58 else 0
        self._dbusservice["/Alarms/LowTemperature"] = 1 if min_cell_temp < 0 else 0
        self._dbusservice["/Alarms/HighTemperature"] = 1 if max_cell_temp > 45 else 0
        self._dbusservice["/Alarms/InternalFailure"] = 1 if internal_errors else 0
        self._dbusservice["/Alarms/HighChargeCurrent"] = 1 if abs(pack_current) > getattr(bat, "maxChargeCurrent", 1000) else 0
        self._dbusservice["/Alarms/HighDischargeCurrent"] = 0

        # Debug printout
        print("--- D-BUS PUBLISH ---")
        print(f"PackVoltage: {pack_voltage:.3f} V, PackCurrent: {pack_current} A, SOC: {soc}%, Power: {power:.2f} W")
        print(f"Min cell voltage: {min_v:.3f} V Cell: {min_id} | Max cell voltage: {max_v:.3f} V Cell: {max_id}")
        print(f"Min cell temp: {min_cell_temp}°C | Max cell temp: {max_cell_temp}°C")
        print("---------------------")

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
