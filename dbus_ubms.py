#!/usr/bin/env python3

"""
D-Bus battery service for Victron, publishing all stats directly from UbmsBattery,
with robust debug output and compatible with argument structure and data access patterns
shown in your provided files. Publishes both to /Dc/0/Voltage and /Dc/Battery/Voltage.
Now correctly publishes min/max cell location to /System/MinVoltageCellId and /System/MaxVoltageCellId,
and min/max temperature location to /System/MinTemperatureCellId and /System/MaxTemperatureCellId
(for Venus OS compatibility). Alarms, TimeToGo, Parameters, History, and full module/cell topology and BMS info are published.

Now also publishes each module's SOC to /Custom/ModuleSOC/N and cell voltages to /Custom/ModuleN/CellM for use in custom GUI pages.

Includes an interactive debug prompt: type 'pack' for pack voltage, 'dbus' for all DBus values, 'exit' to quit the prompt.
"""

import platform
import logging
import sys
import os
import itertools
import threading

from gi.repository import GLib
import dbus
from time import time
from datetime import datetime
from argparse import ArgumentParser

from ubmsbattery import UbmsBattery

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService
from ve_utils import exit_on_error

VERSION = "2.4.4"

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
        # Cell voltages (all cells, flat list)
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
        # Alarm paths for Venus OS
        self._dbusservice.add_path("/Alarms/CellImbalance", 0)
        self._dbusservice.add_path("/Alarms/LowVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighDischargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/HighChargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/LowSoc", 0)
        self._dbusservice.add_path("/Alarms/LowTemperature", 0)
        self._dbusservice.add_path("/Alarms/HighTemperature", 0)
        # TimeToGo path
        self._dbusservice.add_path("/TimeToGo", 0)
        # Parameters section (Info)
        self._dbusservice.add_path("/Info/MaxChargeCurrent", float(getattr(self._bat, "maxChargeCurrent", 0)))
        self._dbusservice.add_path("/Info/MaxDischargeCurrent", float(getattr(self._bat, "maxDischargeCurrent", 0)))
        self._dbusservice.add_path("/Info/MaxChargeVoltage", float(getattr(self._bat, "maxChargeVoltage", 0)))
        self._dbusservice.add_path("/Info/MinCellVoltage", float(getattr(self._bat, "minCellVoltage", 0)))
        self._dbusservice.add_path("/Info/MaxCellVoltage", float(getattr(self._bat, "maxCellVoltage", 0)))
        self._dbusservice.add_path("/Info/MinCellTemperature", float(getattr(self._bat, "minCellTemperature", 0)))
        self._dbusservice.add_path("/Info/MaxCellTemperature", float(getattr(self._bat, "maxCellTemperature", 0)))
        self._dbusservice.add_path("/Info/CellsPerModule", int(getattr(self._bat, "cellsPerModule", 0)))
        self._dbusservice.add_path("/Info/ModuleCount", int(getattr(self._bat, "numberOfModules", 0)))
        self._dbusservice.add_path("/Info/SeriesCount", int(getattr(self._bat, "modulesInSeries", 0)))
        self._dbusservice.add_path("/Info/StringCount", int(getattr(self._bat, "numberOfStrings", 0)))
        self._dbusservice.add_path("/Info/CellCount", int(getattr(self._bat, "numberOfModules", 0)) * int(getattr(self._bat, "cellsPerModule", 0)))
        self._dbusservice.add_path("/Info/MinVoltageCellId", "M1C1")
        self._dbusservice.add_path("/Info/MaxVoltageCellId", "M1C1")
        # BMS and diagnostic info
        self._dbusservice.add_path("/Info/NumberOfModulesCommunicating", int(getattr(self._bat, "numberOfModulesCommunicating", 0)))
        self._dbusservice.add_path("/Info/NumberOfModulesBalancing", int(getattr(self._bat, "numberOfModulesBalancing", 0)))
        self._dbusservice.add_path("/Info/Balanced", int(getattr(self._bat, "balanced", 0)))
        self._dbusservice.add_path("/Info/InternalErrors", int(getattr(self._bat, "internalErrors", 0)))
        self._dbusservice.add_path("/Info/ShutdownReason", int(getattr(self._bat, "shutdownReason", 0)))
        self._dbusservice.add_path("/Info/ChargeComplete", int(getattr(self._bat, "chargeComplete", 0)))
        self._dbusservice.add_path("/Info/BmsMode", int(getattr(self._bat, "mode", 0)))
        self._dbusservice.add_path("/Info/BmsState", str(getattr(self._bat, "state", "")))
        self._dbusservice.add_path("/Info/PartNumber", int(getattr(self._bat, "partnr", 0)))
        self._dbusservice.add_path("/Info/FirmwareVersion", int(getattr(self._bat, "firmwareVersion", 0)))
        self._dbusservice.add_path("/Info/BmsType", int(getattr(self._bat, "bms_type", 0)))
        self._dbusservice.add_path("/Info/HwRev", int(getattr(self._bat, "hw_rev", 0)))
        self._dbusservice.add_path("/Info/VoltageAndCellTAlarms", int(getattr(self._bat, "voltageAndCellTAlarms", 0)))
        self._dbusservice.add_path("/Info/CurrentAndPcbTAlarms", int(getattr(self._bat, "currentAndPcbTAlarms", 0)))
        # History section
        self._history = {
            "MinimumCellVoltage": float(getattr(self._bat, "minCellVoltage", 0)),
            "MaximumCellVoltage": float(getattr(self._bat, "maxCellVoltage", 0)),
            "MinimumCellTemperature": float(getattr(self._bat, "minCellTemperature", 0)),
            "MaximumCellTemperature": float(getattr(self._bat, "maxCellTemperature", 0)),
            "MinimumSoc": float(getattr(self._bat, "soc", 100)),
            "MaximumSoc": float(getattr(self._bat, "soc", 0)),
            "TotalAhDrawn": 0,
            "ChargeCycles": 0,
            "DeepDischarges": 0,
            "FullDischarges": 0,
            "LastFullCharge": 0,
            "TimeSinceLastFullCharge": 0,
            "TotalChargeTime": 0,
            "TotalDischargeTime": 0,
        }
        for key, val in self._history.items():
            self._dbusservice.add_path(f"/History/{key}", val)

        # --- Custom: Per-module SOC and cell voltages publishing for GUI use ---
        self._module_soc_paths = []
        self._custom_cell_voltage_paths = []
        module_count = int(getattr(self._bat, "numberOfModules", modules))
        cells_per_module = int(getattr(self._bat, "cellsPerModule", 4))
        for midx in range(module_count):
            # Per-module SOC
            path = f"/Custom/ModuleSOC/{midx+1}"
            self._dbusservice.add_path(path, 0.0)
            self._module_soc_paths.append(path)
            # Per-module cell voltages
            cell_paths = []
            for cidx in range(cells_per_module):
                cpath = f"/Custom/Module{midx+1}/Cell{cidx+1}"
                self._dbusservice.add_path(cpath, 0.0)
                cell_paths.append(cpath)
            self._custom_cell_voltage_paths.append(cell_paths)

        self._dbusservice.register()
        GLib.timeout_add(1000, exit_on_error, self._update)

def debug_prompt(bat, dbusservice):
    print("Interactive debug prompt started.")
    print("Commands: pack (pack voltage), dbus (all DBus values), exit (quit prompt)")
    while True:
        try:
            cmd = input("DEBUG> ").strip()
        except EOFError:
            break
        if cmd == "pack":
            print("Pack voltage:", bat.get_pack_voltage())
        elif cmd == "dbus":
            for k, v in sorted(dbusservice.items()):
                print(f"{k}: {v}")
        elif cmd == "exit":
            print("Exiting debug prompt.")
            break
        else:
            print("Commands: pack, dbus, exit")

def _update(self):
    voltage = self._bat.get_pack_voltage() if hasattr(self._bat, "get_pack_voltage") else getattr(self._bat, "voltage", 0.0)
    current = getattr(self._bat, "current", 0.0)
    temperature = getattr(self._bat, "maxCellTemperature", 0.0)
    soc = getattr(self._bat, "soc", 0)
    capacity = getattr(self._bat, "capacity", 0)
    power = float(voltage) * float(current)
    min_cell_v = getattr(self._bat, "minCellVoltage", 0.0)
    max_cell_v = getattr(self._bat, "maxCellVoltage", 0.0)
    min_cell_t = getattr(self._bat, "minCellTemperature", 0.0)
    max_cell_t = getattr(self._bat, "maxCellTemperature", 0.0)
    max_pcb_t = getattr(self._bat, "maxPcbTemperature", 0.0)
    cell_voltages = list(itertools.chain(*getattr(self._bat, "cellVoltages", [])))
    cell_temperatures = list(itertools.chain(*getattr(self._bat, "cellTemperatures", [])))
    cells_per_module = int(getattr(self._bat, "cellsPerModule", 4))
    module_count = int(getattr(self._bat, "numberOfModules", 16))

    # Min/max cell voltage and temperature locations
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

    # Main battery stats
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
    self._dbusservice["/System/MinVoltageCellId"] = str(min_voltage_cell_id)
    self._dbusservice["/System/MaxVoltageCellId"] = str(max_voltage_cell_id)
    self._dbusservice["/System/MinCellTemperature"] = float(min_cell_t)
    self._dbusservice["/System/MaxCellTemperature"] = float(max_cell_t)
    self._dbusservice["/System/MaxPcbTemperature"] = float(max_pcb_t)
    self._dbusservice["/System/MinTemperatureCellId"] = str(min_temp_cell_id)
    self._dbusservice["/System/MaxTemperatureCellId"] = str(max_temp_cell_id)
    for i, v in enumerate(cell_voltages):
        self._dbusservice[f"/Voltages/Cell{i+1}"] = float(v) / 1000.0 if v else 0.0

    # --- Per-module SOC publishing ---
    module_soc_list = getattr(self._bat, "moduleSoc", None)
    if module_soc_list is not None:
        for idx, path in enumerate(self._module_soc_paths):
            try:
                self._dbusservice[path] = float(module_soc_list[idx])
            except (IndexError, ValueError, TypeError):
                self._dbusservice[path] = 0.0

    # --- Per-module per-cell voltages publishing ---
    cell_voltages_matrix = getattr(self._bat, "cellVoltages", None)
    if cell_voltages_matrix is not None:
        for midx, cell_paths in enumerate(self._custom_cell_voltage_paths):
            try:
                module_cells = cell_voltages_matrix[midx]
                for cidx, cpath in enumerate(cell_paths):
                    try:
                        self._dbusservice[cpath] = float(module_cells[cidx]) / 1000.0 if module_cells[cidx] else 0.0
                    except (IndexError, ValueError, TypeError):
                        self._dbusservice[cpath] = 0.0
            except (IndexError, TypeError):
                for cpath in cell_paths:
                    self._dbusservice[cpath] = 0.0

    # Alarms
    deltaCellVoltage = self._bat.maxCellVoltage - self._bat.minCellVoltage
    if deltaCellVoltage > 0.25:
        self._dbusservice["/Alarms/CellImbalance"] = 2
    elif deltaCellVoltage >= 0.18:
        self._dbusservice["/Alarms/CellImbalance"] = 1
    else:
        self._dbusservice["/Alarms/CellImbalance"] = 0
    self._dbusservice["/Alarms/LowVoltage"] = (self._bat.voltageAndCellTAlarms & 0x10) >> 4
    self._dbusservice["/Alarms/HighVoltage"] = (self._bat.voltageAndCellTAlarms & 0x20) >> 5
    self._dbusservice["/Alarms/HighDischargeCurrent"] = (self._bat.currentAndPcbTAlarms & 0x3)
    self._dbusservice["/Alarms/HighChargeCurrent"] = (self._bat.currentAndPcbTAlarms & 0xC) >> 2
    self._dbusservice["/Alarms/LowSoc"] = (self._bat.voltageAndCellTAlarms & 0x08) >> 3
    self._dbusservice["/Alarms/LowTemperature"] = (self._bat.mode & 0x60) >> 5
    self._dbusservice["/Alarms/HighTemperature"] = ((self._bat.voltageAndCellTAlarms & 0x6) >> 1) | ((self._bat.currentAndPcbTAlarms & 0x18) >> 3)

    # --- TimeToGo calculation (seconds) ---
    try:
        if abs(current) > 0.01:
            remaining_ah = float(capacity) * (float(soc) / 100.0)
            time_to_go = int((remaining_ah / abs(current)) * 3600)
            time_to_go = max(0, min(time_to_go, 999999))
        else:
            time_to_go = 0
    except Exception as e:
        print(f"[DEBUG] TimeToGo calculation error: {e}")
        time_to_go = 0
    self._dbusservice["/TimeToGo"] = time_to_go

    # --- Parameters (Info) update ---
    self._dbusservice["/Info/MaxChargeCurrent"] = float(getattr(self._bat, "maxChargeCurrent", 0))
    self._dbusservice["/Info/MaxDischargeCurrent"] = float(getattr(self._bat, "maxDischargeCurrent", 0))
    self._dbusservice["/Info/MaxChargeVoltage"] = float(getattr(self._bat, "maxChargeVoltage", 0))
    self._dbusservice["/Info/MinCellVoltage"] = float(getattr(self._bat, "minCellVoltage", 0))
    self._dbusservice["/Info/MaxCellVoltage"] = float(getattr(self._bat, "maxCellVoltage", 0))
    self._dbusservice["/Info/MinCellTemperature"] = float(getattr(self._bat, "minCellTemperature", 0))
    self._dbusservice["/Info/MaxCellTemperature"] = float(getattr(self._bat, "maxCellTemperature", 0))
    self._dbusservice["/Info/CellsPerModule"] = int(getattr(self._bat, "cellsPerModule", 0))
    self._dbusservice["/Info/ModuleCount"] = int(getattr(self._bat, "numberOfModules", 0))
    self._dbusservice["/Info/SeriesCount"] = int(getattr(self._bat, "modulesInSeries", 0))
    self._dbusservice["/Info/StringCount"] = int(getattr(self._bat, "numberOfStrings", 0))
    self._dbusservice["/Info/CellCount"] = int(getattr(self._bat, "numberOfModules", 0)) * int(getattr(self._bat, "cellsPerModule", 0))
    self._dbusservice["/Info/MinVoltageCellId"] = str(min_voltage_cell_id)
    self._dbusservice["/Info/MaxVoltageCellId"] = str(max_voltage_cell_id)
    self._dbusservice["/Info/NumberOfModulesCommunicating"] = int(getattr(self._bat, "numberOfModulesCommunicating", 0))
    self._dbusservice["/Info/NumberOfModulesBalancing"] = int(getattr(self._bat, "numberOfModulesBalancing", 0))
    self._dbusservice["/Info/Balanced"] = int(getattr(self._bat, "balanced", 0))
    self._dbusservice["/Info/InternalErrors"] = int(getattr(self._bat, "internalErrors", 0))
    self._dbusservice["/Info/ShutdownReason"] = int(getattr(self._bat, "shutdownReason", 0))
    self._dbusservice["/Info/ChargeComplete"] = int(getattr(self._bat, "chargeComplete", 0))
    self._dbusservice["/Info/BmsMode"] = int(getattr(self._bat, "mode", 0))
    self._dbusservice["/Info/BmsState"] = str(getattr(self._bat, "state", ""))
    self._dbusservice["/Info/PartNumber"] = int(getattr(self._bat, "partnr", 0))
    self._dbusservice["/Info/FirmwareVersion"] = int(getattr(self._bat, "firmwareVersion", 0))
    self._dbusservice["/Info/BmsType"] = int(getattr(self._bat, "bms_type", 0))
    self._dbusservice["/Info/HwRev"] = int(getattr(self._bat, "hw_rev", 0))
    self._dbusservice["/Info/VoltageAndCellTAlarms"] = int(getattr(self._bat, "voltageAndCellTAlarms", 0))
    self._dbusservice["/Info/CurrentAndPcbTAlarms"] = int(getattr(self._bat, "currentAndPcbTAlarms", 0))

    # --- History update ---
    self._history["MinimumCellVoltage"] = min(self._history["MinimumCellVoltage"], min_cell_v) if min_cell_v else self._history["MinimumCellVoltage"]
    self._history["MaximumCellVoltage"] = max(self._history["MaximumCellVoltage"], max_cell_v) if max_cell_v else self._history["MaximumCellVoltage"]
    self._history["MinimumCellTemperature"] = min(self._history["MinimumCellTemperature"], min_cell_t) if min_cell_t else self._history["MinimumCellTemperature"]
    self._history["MaximumCellTemperature"] = max(self._history["MaximumCellTemperature"], max_cell_t) if max_cell_t else self._history["MaximumCellTemperature"]
    self._history["MinimumSoc"] = min(self._history["MinimumSoc"], soc) if soc else self._history["MinimumSoc"]
    self._history["MaximumSoc"] = max(self._history["MaximumSoc"], soc) if soc else self._history["MaximumSoc"]
    for key, val in self._history.items():
        self._dbusservice[f"/History/{key}"] = val

    return True

# Patch the class method
DbusBatteryService._update = _update

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
    dbus_battery_service = DbusBatteryService(
        servicename="com.victronenergy.battery",
        connection=args.interface,
        deviceinstance=args.deviceinstance,
        capacity=int(args.capacity),
        voltage=float(args.voltage),
        modules=args.modules,
        strings=args.strings,
    )
    # Start the interactive debug prompt in a background thread
    threading.Thread(target=debug_prompt, args=(dbus_battery_service._bat, dbus_battery_service._dbusservice), daemon=True).start()
    print("Connected to dbus, switching to GLib.MainLoop()")
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
