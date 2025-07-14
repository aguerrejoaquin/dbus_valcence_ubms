#!/usr/bin/env python3

import sys
import os
import platform
import logging
from argparse import ArgumentParser
from gi.repository import GLib
import dbus.mainloop.glib

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService

from ubmsbattery import UbmsBattery

VERSION = "2.5.0"

class DbusBatteryService:
    def __init__(self, servicename, deviceinstance, voltage, capacity, modules=16, strings=4, connection="can0"):
        self._bat = UbmsBattery(capacity=capacity, voltage=voltage, connection=connection, numberOfModules=modules, numberOfStrings=strings)
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

        # All 8 required Min/Max cell voltage and temperature paths for Victron
        self._dbusservice.add_path("/System/MinVoltageCell", 0.0)
        self._dbusservice.add_path("/System/MinVoltageCellId", "1.1")
        self._dbusservice.add_path("/System/MaxVoltageCell", 0.0)
        self._dbusservice.add_path("/System/MaxVoltageCellId", "1.1")
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperatureId", "1.1")
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperatureId", "1.1")

        # Real-time values
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)

        # Standard Victron alarm paths
        self._dbusservice.add_path("/Alarms/LowVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighVoltage", 0)
        self._dbusservice.add_path("/Alarms/LowTemperature", 0)
        self._dbusservice.add_path("/Alarms/HighTemperature", 0)
        self._dbusservice.add_path("/Alarms/InternalFailure", 0)
        self._dbusservice.add_path("/Alarms/HighChargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/HighDischargeCurrent", 0)

        # Extended: custom alarms, errors, balancing, shutdown
        self._dbusservice.add_path("/Alarms/VoltageAndCellT", 0)
        self._dbusservice.add_path("/Alarms/CurrentAndPcbT", 0)
        self._dbusservice.add_path("/Alarms/InternalErrors", 0)
        self._dbusservice.add_path("/Alarms/ShutdownReason", 0)
        self._dbusservice.add_path("/Status/Balanced", 1)
        self._dbusservice.add_path("/Status/ModulesCommunicating", 0)
        self._dbusservice.add_path("/Status/ModulesBalancing", 0)
        self._dbusservice.add_path("/Status/ChargeComplete", 0)
        self._dbusservice.add_path("/Status/MaxPcbTemperature", 0)
        self._dbusservice.add_path("/Status/PackMaxCellVoltage", 0.0)
        self._dbusservice.add_path("/Status/PackMinCellVoltage", 0.0)
        self._dbusservice.add_path("/Status/PackMaxCellTemperature", 0)
        self._dbusservice.add_path("/Status/PackMinCellTemperature", 0)

        # Per-module voltage, soc, temp
        for m in range(self.modules):
            self._dbusservice.add_path(f"/System/Module/{m+1}/Voltage", 0.0)
            self._dbusservice.add_path(f"/System/Module/{m+1}/Soc", 0)
            self._dbusservice.add_path(f"/System/Module/{m+1}/Temperature", 0.0)
            for c in range(self.cells_per_module):
                cell_idx = m * self.cells_per_module + c
                self._dbusservice.add_path(f"/System/Cell/{cell_idx+1}/Voltage", 0.0)

        GLib.timeout_add_seconds(1, self.update)

    def update(self):
        # --- Gather data ---
        bat = self._bat
        # Pack voltage (sum of modules in series)
        pack_voltage = bat.get_pack_voltage() if hasattr(bat, "get_pack_voltage") else 0.0
        # Pack current
        pack_current = getattr(bat, "current", 0.0)
        # SOC
        soc = getattr(bat, "soc", 0)
        # Power
        power = pack_voltage * pack_current
        # State/mode
        state = getattr(bat, "state", 14)
        mode = getattr(bat, "mode", 1)
        # Temperatures
        max_cell_temp = getattr(bat, "maxCellTemperature", 0)
        min_cell_temp = getattr(bat, "minCellTemperature", 0)
        max_pcb_temp = getattr(bat, "maxPcbTemperature", 0)
        # Alarms/errors
        voltage_cellt_alarms = getattr(bat, "voltageAndCellTAlarms", 0)
        current_pcbt_alarms = getattr(bat, "currentAndPcbTAlarms", 0)
        internal_errors = getattr(bat, "internalErrors", 0)
        shutdown_reason = getattr(bat, "shutdownReason", 0)
        balanced = int(getattr(bat, "balanced", True))
        # Modules comm/balancing
        modules_comm = getattr(bat, "numberOfModulesCommunicating", 0)
        modules_bal = getattr(bat, "numberOfModulesBalancing", 0)
        charge_complete = getattr(bat, "chargeComplete", 0)
        # Max/min cell voltage (pack)
        pack_max_cell_voltage = getattr(bat, "maxCellVoltage", 0.0)
        pack_min_cell_voltage = getattr(bat, "minCellVoltage", 0.0)
        # Per-module
        module_voltage = getattr(bat, "moduleVoltage", [0]*self.modules)
        module_soc = getattr(bat, "moduleSoc", [0]*self.modules)
        module_temp = getattr(bat, "moduleTemp", [0]*self.modules)
        # Per-cell
        cellVoltages = getattr(bat, "cellVoltages", [])
        if cellVoltages and isinstance(cellVoltages[0], tuple):
            cellVoltages = [list(t) for t in cellVoltages]

        # Min/max cell voltage and location
        cell_voltages = []
        min_v = max_v = 0.0
        min_m = min_c = max_m = max_c = 0
        for m, mod in enumerate(cellVoltages):
            for c, v in enumerate(mod):
                v_v = v / 1000.0 if v is not None else 0.0  # mV to V
                cell_voltages.append((v_v, m+1, c+1))
                cell_idx = m * self.cells_per_module + c
                self._dbusservice[f"/System/Cell/{cell_idx+1}/Voltage"] = v_v
        cell_voltages_nonzero = [x for x in cell_voltages if x[0] > 0]
        if cell_voltages_nonzero:
            min_v, min_m, min_c = min(cell_voltages_nonzero, key=lambda x: x[0])
            max_v, max_m, max_c = max(cell_voltages_nonzero, key=lambda x: x[0])
            min_cell_id = f"{min_m}.{min_c}"
            max_cell_id = f"{max_m}.{max_c}"
        else:
            min_v = max_v = 0.0
            min_cell_id = max_cell_id = "1.1"

        # Min/max cell temperature and location
        # (No per-cell temp: use pack min/max and always "1.1")
        min_t = min_cell_temp
        max_t = max_cell_temp
        min_temp_id = max_temp_id = "1.1"

        # Per-module voltage/soc/temp
        for m in range(self.modules):
            self._dbusservice[f"/System/Module/{m+1}/Voltage"] = module_voltage[m] / 1000.0 if m < len(module_voltage) else 0.0
            self._dbusservice[f"/System/Module/{m+1}/Soc"] = module_soc[m] if m < len(module_soc) else 0
            self._dbusservice[f"/System/Module/{m+1}/Temperature"] = module_temp[m] if m < len(module_temp) else 0.0

        # --- Standard Venus OS/Victron alarms ---
        # These thresholds are examples! Tweak them to your battery specs.
        self._dbusservice["/Alarms/LowVoltage"] = 1 if pack_voltage < 44 else 0
        self._dbusservice["/Alarms/HighVoltage"] = 1 if pack_voltage > 58 else 0
        self._dbusservice["/Alarms/LowTemperature"] = 1 if min_cell_temp < 0 else 0
        self._dbusservice["/Alarms/HighTemperature"] = 1 if max_cell_temp > 45 else 0
        self._dbusservice["/Alarms/InternalFailure"] = 1 if internal_errors else 0
        self._dbusservice["/Alarms/HighChargeCurrent"] = 1 if abs(pack_current) > getattr(bat, "maxChargeCurrent", 1000) else 0
        self._dbusservice["/Alarms/HighDischargeCurrent"] = 0  # You may set based on your logic

        # ---- Custom/extended alarm info (VRM only) ----
        self._dbusservice["/Alarms/VoltageAndCellT"] = voltage_cellt_alarms
        self._dbusservice["/Alarms/CurrentAndPcbT"] = current_pcbt_alarms
        self._dbusservice["/Alarms/InternalErrors"] = internal_errors
        self._dbusservice["/Alarms/ShutdownReason"] = shutdown_reason
        self._dbusservice["/Status/Balanced"] = balanced
        self._dbusservice["/Status/ModulesCommunicating"] = modules_comm
        self._dbusservice["/Status/ModulesBalancing"] = modules_bal
        self._dbusservice["/Status/ChargeComplete"] = charge_complete
        self._dbusservice["/Status/MaxPcbTemperature"] = max_pcb_temp
        self._dbusservice["/Status/PackMaxCellVoltage"] = pack_max_cell_voltage
        self._dbusservice["/Status/PackMinCellVoltage"] = pack_min_cell_voltage
        self._dbusservice["/Status/PackMaxCellTemperature"] = max_cell_temp
        self._dbusservice["/Status/PackMinCellTemperature"] = min_cell_temp

        # ---- D-Bus core ----
        self._dbusservice["/Dc/0/Voltage"] = pack_voltage
        self._dbusservice["/Dc/0/Current"] = pack_current
        self._dbusservice["/Soc"] = soc
        self._dbusservice["/Dc/0/Power"] = power
        self._dbusservice["/Dc/0/Temperature"] = max_cell_temp
        self._dbusservice["/State"] = state
        self._dbusservice["/Mode"] = mode

        # --- ALL 8 Min/Max cells/temps required by Victron ---
        self._dbusservice["/System/MinVoltageCell"] = min_v
        self._dbusservice["/System/MinVoltageCellId"] = min_cell_id
        self._dbusservice["/System/MaxVoltageCell"] = max_v
        self._dbusservice["/System/MaxVoltageCellId"] = max_cell_id
        self._dbusservice["/System/MinCellTemperature"] = min_t
        self._dbusservice["/System/MinCellTemperatureId"] = min_temp_id
        self._dbusservice["/System/MaxCellTemperature"] = max_t
        self._dbusservice["/System/MaxCellTemperatureId"] = max_temp_id

        # --- Debug: print all published values ---
        print("--- D-BUS PUBLISH ---")
        print(f"PackVoltage: {pack_voltage:.3f} V, PackCurrent: {pack_current} A, SOC: {soc}%, Power: {power:.2f} W")
        print(f"State: {state}, Mode: {mode}, MaxCellTemp: {max_cell_temp}C, MinCellTemp: {min_cell_temp}C, MaxPCBTemp: {max_pcb_temp}C")
        print(f"Alarms: LV={self._dbusservice['/Alarms/LowVoltage']} HV={self._dbusservice['/Alarms/HighVoltage']} LT={self._dbusservice['/Alarms/LowTemperature']} HT={self._dbusservice['/Alarms/HighTemperature']} Internal={self._dbusservice['/Alarms/InternalFailure']} CC={self._dbusservice['/Alarms/HighChargeCurrent']}")
        print(f"Balanced: {balanced}, ModulesComm: {modules_comm}, ModulesBal: {modules_bal}, ChargeComplete: {charge_complete}")
        print(f"PackMaxCellVoltage: {pack_max_cell_voltage:.3f}V, PackMinCellVoltage: {pack_min_cell_voltage:.3f}V")
        print(f"Min cell voltage: {min_v} @ {min_cell_id}, Max cell voltage: {max_v} @ {max_cell_id}")
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
