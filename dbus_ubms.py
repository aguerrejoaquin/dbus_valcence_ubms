#!/usr/bin/env python3

"""
D-Bus battery service for Victron, publishing all stats directly from UbmsBattery,
with robust debug output and compatible with argument structure and data access patterns
shown in your provided files. Publishes both to /Dc/0/Voltage and /Dc/Battery/Voltage.
Now correctly publishes min/max cell location to /System/MinVoltageCellId and /System/MaxVoltageCellId,
and min/max temperature location to /System/MinTemperatureCellId and /System/MaxTemperatureCellId
(for Venus OS compatibility).
"""

import platform
import logging
import sys
import os
import itertools
import math

from gi.repository import GLib
import dbus
from time import time
from datetime import datetime
from argparse import ArgumentParser

from ubmsbattery import UbmsBattery

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService
from ve_utils import exit_on_error

VERSION = "2.4.0"

class DbusBatteryService:
    def __init__(
        self,
        servicename,
        deviceinstance,
        voltage,
        capacity,
        modules=16,
        strings=4,
        productname="Valence U-BMS",
        connection="can0",
    ):
        self._bat = UbmsBattery(
            capacity=capacity,
            voltage=voltage,
            connection=connection,
            numberOfModules=modules,
            numberOfStrings=strings
        )
        self._dbusservice = VeDbusService(
            f"{servicename}.socketcan_{connection}_di{deviceinstance}",
            register=False
        )
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion", f"{VERSION} Python {platform.python_version()}")
        self._dbusservice.add_path("/Mgmt/Connection", connection)
        self._dbusservice.add_path("/DeviceInstance", deviceinstance)
        self._dbusservice.add_path("/ProductId", 0)
        self._dbusservice.add_path("/ProductName", productname)
        self._dbusservice.add_path("/Manufacturer", "Valence")
        self._dbusservice.add_path("/FirmwareVersion", getattr(self._bat, "firmwareVersion", 0))
        self._dbusservice.add_path("/HardwareVersion", f"type: {getattr(self._bat, 'bms_type', 0)} rev. {hex(getattr(self._bat, 'hw_rev', 0))}")
        self._dbusservice.add_path("/Connected", 0)
        self._dbusservice.add_path("/State", 14, writeable=True)
        self._dbusservice.add_path("/Mode", 1, writeable=True)
        self._dbusservice.add_path("/Soh", 100)
        self._dbusservice.add_path("/Capacity", int(capacity))
        self._dbusservice.add_path("/InstalledCapacity", int(capacity))
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/Battery/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Dc/Battery/Current", 0.0)
        self._dbusservice.add_path("/Dc/0/Temperature", 0.0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        # Cell voltages
        for i in range(modules * 4):
            self._dbusservice.add_path(f"/Voltages/Cell{i+1}", 0.0)
        self._dbusservice.add_path("/System/MinCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MaxCellVoltage", 0.0)
        # Venus OS-compatible cell voltage location paths
        self._dbusservice.add_path("/System/MinVoltageCellId", "M1C1")
        self._dbusservice.add_path("/System/MaxVoltageCellId", "M1C1")
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxPcbTemperature", 0.0)
        # Venus OS-compatible cell temperature location paths
        self._dbusservice.add_path("/System/MinTemperatureCellId", "M1C1")
        self._dbusservice.add_path("/System/MaxTemperatureCellId", "M1C1")
        self._dbusservice.register()
        GLib.timeout_add(1000, exit_on_error, self._update)

    def _update(self):
        # Get values directly from UbmsBattery instance
        voltage = self._bat.get_pack_voltage() if hasattr(self._bat, "get_pack_voltage") else getattr(self._bat, "voltage", 0.0)
        current = getattr(self._bat, "current", 0.0)
        temperature = getattr(self._bat, "maxCellTemperature", 0.0)
        soc = getattr(self._bat, "soc", 0)
        power = float(voltage) * float(current)
        min_cell_v = getattr(self._bat, "minCellVoltage", 0.0)
        max_cell_v = getattr(self._bat, "maxCellVoltage", 0.0)
        min_cell_t = getattr(self._bat, "minCellTemperature", 0.0)
        max_cell_t = getattr(self._bat, "maxCellTemperature", 0.0)
        max_pcb_t = getattr(self._bat, "maxPcbTemperature", 0.0)
        cell_voltages = list(itertools.chain(*getattr(self._bat, "cellVoltages", [])))
        cell_temperatures = list(itertools.chain(*getattr(self._bat, "cellTemperatures", [])))

        cells_per_module = 4

        # Min/max cell voltage location (Venus OS expects "M#C#" format, 1-based indices)
        min_voltage_cell_id = "M1C1"
        max_voltage_cell_id = "M1C1"
        if cell_voltages:
            min_v = min(cell_voltages)
            max_v = max(cell_voltages)
            min_idx = cell_voltages.index(min_v)
            max_idx = cell_voltages.index(max_v)
            min_module = min_idx // cells_per_module + 1
            min_cell = min_idx % cells_per_module + 1
            max_module = max_idx // cells_per_module + 1
            max_cell = max_idx % cells_per_module + 1
            min_voltage_cell_id = f"M{min_module}C{min_cell}"
            max_voltage_cell_id = f"M{max_module}C{max_cell}"

        # Min/max cell temperature location (Venus OS expects "M#C#" format, 1-based indices)
        min_temp_cell_id = "M1C1"
        max_temp_cell_id = "M1C1"
        if cell_temperatures:
            min_temp = min(cell_temperatures)
            max_temp = max(cell_temperatures)
            min_idx = cell_temperatures.index(min_temp)
            max_idx = cell_temperatures.index(max_temp)
            min_module = min_idx // cells_per_module + 1
            min_cell = min_idx % cells_per_module + 1
            max_module = max_idx // cells_per_module + 1
            max_cell = max_idx % cells_per_module + 1
            min_temp_cell_id = f"M{min_module}C{min_cell}"
            max_temp_cell_id = f"M{max_module}C{max_cell}"

        # Debug output for cell locations
        print(f"[DEBUG] UbmsBattery.get_pack_voltage() = {voltage} (type={type(voltage)})")
        print(f"[DEBUG] UbmsBattery.current = {current}")
        print(f"[DEBUG] UbmsBattery.maxCellTemperature = {temperature}")
        print(f"[DEBUG] UbmsBattery.soc = {soc}")
        print(f"[DEBUG] Published /Dc/0/Voltage = {float(voltage)}")
        print(f"[DEBUG] Published /Dc/Battery/Voltage = {float(voltage)}")
        print(f"[DEBUG] Cell voltages: {cell_voltages}")
        print(f"[DEBUG] Cell temperatures: {cell_temperatures}")
        print(f"[DEBUG] Publishing /System/MinVoltageCellId = {min_voltage_cell_id} (type={type(min_voltage_cell_id)})")
        print(f"[DEBUG] Publishing /System/MaxVoltageCellId = {max_voltage_cell_id} (type={type(max_voltage_cell_id)})")
        print(f"[DEBUG] Publishing /System/MinTemperatureCellId = {min_temp_cell_id} (type={type(min_temp_cell_id)})")
        print(f"[DEBUG] Publishing /System/MaxTemperatureCellId = {max_temp_cell_id} (type={type(max_temp_cell_id)})")

        self._dbusservice["/Dc/0/Voltage"] = float(voltage)
        self._dbusservice["/Dc/Battery/Voltage"] = float(voltage)
        self._dbusservice["/Dc/0/Current"] = float(current)
        self._dbusservice["/Dc/Battery/Current"] = float(current)
        self._dbusservice["/Dc/0/Temperature"] = float(temperature)
        self._dbusservice["/Dc/0/Power"] = power
        self._dbusservice["/Soc"] = float(soc)
        self._dbusservice["/Connected"] = 1 if getattr(self._bat, "updated", -1) != -1 else 0
        self._dbusservice["/System/MinCellVoltage"] = float(min_cell_v)
        self._dbusservice["/System/MaxCellVoltage"] = float(max_cell_v)
        # Actually update the correct cell voltage location paths
        self._dbusservice["/System/MinVoltageCellId"] = str(min_voltage_cell_id)
        self._dbusservice["/System/MaxVoltageCellId"] = str(max_voltage_cell_id)
        self._dbusservice["/System/MinCellTemperature"] = float(min_cell_t)
        self._dbusservice["/System/MaxCellTemperature"] = float(max_cell_t)
        self._dbusservice["/System/MaxPcbTemperature"] = float(max_pcb_t)
        # Actually update the correct cell temperature location paths
        self._dbusservice["/System/MinTemperatureCellId"] = str(min_temp_cell_id)
        self._dbusservice["/System/MaxTemperatureCellId"] = str(max_temp_cell_id)
        # Publish all cell voltages
        for i, v in enumerate(cell_voltages):
            self._dbusservice[f"/Voltages/Cell{i+1}"] = float(v) / 1000.0 if v else 0.0

        return True

def main():
    parser = ArgumentParser(description="dbus_ubms", add_help=True)
    parser.add_argument("-i", "--interface", help="CAN interface", default="can0")
    parser.add_argument("-c", "--capacity", help="capacity in Ah", default=130, type=int)
    parser.add_argument("-v", "--voltage", help="maximum charge voltage V", required=True, type=float)
    parser.add_argument("--modules", type=int, default=16, help="number of modules")
    parser.add_argument("--strings", type=int, default=4, help="number of strings")
    parser.add_argument("--deviceinstance", type=int, default=0, help="device instance")
    args = parser.parse_args()
    logging.basicConfig(format="%(levelname)-8s %(message)s", level=logging.INFO)
    os.system(f"ip link set {args.interface} type can bitrate 250000")
    os.system(f"ifconfig {args.interface} up")
    print(f"Starting dbus_ubms {VERSION} on {args.interface}")
    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    DbusBatteryService(
        servicename="com.victronenergy.battery",
        connection=args.interface,
        deviceinstance=args.deviceinstance,
        capacity=int(args.capacity),
        voltage=float(args.voltage),
        modules=args.modules,
        strings=args.strings,
    )
    print("Connected to dbus, switching to GLib.MainLoop()")
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
