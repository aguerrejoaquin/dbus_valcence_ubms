#!/usr/bin/env python3

"""
A class to put a battery service on the dbus, according to victron standards, with constantly updating
paths.
"""

import sys
import os
import platform
import logging
import itertools
import math
from argparse import ArgumentParser
from gi.repository import GLib
import dbus
from time import time
from datetime import datetime

from ubmsbattery import UbmsBattery

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService
from ve_utils import exit_on_error
from settingsdevice import SettingsDevice

VERSION = "1.3.6"

def safe_getattr(obj, attr, default):
    try:
        value = getattr(obj, attr, default)
        if value is None:
            return default
        return value
    except Exception as e:
        logging.warning(f"Error accessing {attr} on {obj}: {e}")
        return default

def handle_changed_setting(setting, oldvalue, newvalue):
    logging.debug(
        "setting changed, setting: %s, old: %s, new: %s" % (setting, oldvalue, newvalue)
    )

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
        self.minUpdateDone = 0
        self.dailyResetDone = 0
        self.lastUpdated = 0
        self._bat = UbmsBattery(
            capacity=capacity,
            voltage=voltage,
            connection=connection,
            numberOfModules=modules,
            numberOfStrings=strings
        )

        try:
            self._dbusservice = VeDbusService(
                servicename + ".socketcan_" + connection + "_di" + str(deviceinstance),
                register=False
            )
        except Exception as e:
            logging.error(f"VeDbusService error: {e}")
            exit

        # Management objects
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path(
            "/Mgmt/ProcessVersion",
            VERSION + " running on Python " + platform.python_version(),
        )
        self._dbusservice.add_path("/Mgmt/Connection", connection)

        # Mandatory objects
        self._dbusservice.add_path("/DeviceInstance", deviceinstance)
        self._dbusservice.add_path("/ProductId", 0)
        self._dbusservice.add_path("/ProductName", productname)
        self._dbusservice.add_path("/Manufacturer", "Valence")
        self._dbusservice.add_path("/FirmwareVersion", safe_getattr(self._bat, "firmwareVersion", "1.0"))
        self._dbusservice.add_path("/HardwareVersion", "type: " + str(safe_getattr(self._bat, "bms_type", "UBMS")) + " rev. "+ hex(safe_getattr(self._bat, "hw_rev", 1)))
        self._dbusservice.add_path("/Connected", 0)
        self._dbusservice.add_path("/State", 14, writeable=True)
        self._dbusservice.add_path("/Mode", 1, writeable=True, onchangecallback=self._transmit_mode) 
        self._dbusservice.add_path("/Soh", 100)
        self._dbusservice.add_path("/Capacity", int(capacity))
        self._dbusservice.add_path("/InstalledCapacity", int(capacity))
        self._dbusservice.add_path("/Info/MaxChargeCurrent", 0)
        self._dbusservice.add_path("/Info/MaxDischargeCurrent", 0)
        self._dbusservice.add_path("/Info/MaxChargeVoltage", float(voltage))
        self._dbusservice.add_path("/Info/BatteryLowVoltage", 44.8)
        self._dbusservice.add_path("/Alarms/CellImbalance", 0)
        self._dbusservice.add_path("/Alarms/LowVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighDischargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/HighChargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/LowSoc", 0)
        self._dbusservice.add_path("/Alarms/LowTemperature", 0)
        self._dbusservice.add_path("/Alarms/HighTemperature", 0)
        self._dbusservice.add_path("/Alarms/InternalFailure", 0)
        self._dbusservice.add_path("/Balancing", 0)
        self._dbusservice.add_path("/System/HasTemperature", 1)
        self._dbusservice.add_path("/System/NrOfBatteries", safe_getattr(self._bat, "numberOfModules", modules))
        self._dbusservice.add_path("/System/NrOfModulesOnline", safe_getattr(self._bat, "numberOfModules", modules))
        self._dbusservice.add_path("/System/NrOfModulesOffline", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingDischarge", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingCharge", 0)
        self._dbusservice.add_path("/System/NrOfBatteriesBalancing", 0)
        self._dbusservice.add_path("/System/BatteriesParallel", safe_getattr(self._bat, "numberOfStrings", strings))
        self._dbusservice.add_path("/System/BatteriesSeries", safe_getattr(self._bat, "modulesInSeries", 1))
        self._dbusservice.add_path("/System/NrOfCellsPerBattery", safe_getattr(self._bat, "cellsPerModule", 4))

        # Battery cell voltage/temp location/values
        self._dbusservice.add_path("/System/MinCellVoltageId", "M1C1")
        self._dbusservice.add_path("/System/MaxCellVoltageId", "M1C1")
        self._dbusservice.add_path("/System/MinCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MaxCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperatureId", "M1C1")
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperatureId", "M1C1")
        self._dbusservice.add_path("/System/MaxPcbTemperature", 0.0)

        # Core battery stats
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)
        self._dbusservice.add_path("/Dc/0/Temperature", 0.0)
        self._dbusservice.add_path("/Soc", 0)

        # Per-cell voltages
        cells_total = safe_getattr(self._bat, "cellsPerModule", 4) * safe_getattr(self._bat, "numberOfModules", modules)
        for i in range(1, cells_total + 1):
            self._dbusservice.add_path(f"/Voltages/Cell{i}", 0.0)
            self._dbusservice.add_path(f"/Balances/Cell{i}", 0.0)

        self._dbusservice.add_path("/Voltages/Sum", 0.0)
        self._dbusservice.add_path("/Voltages/Diff", 0.0)

        # History/Statistics
        self._dbusservice.add_path("/History/TimeSinceLastFullCharge", 0)
        self._dbusservice.add_path("/History/MinCellVoltage", 0.0)
        self._dbusservice.add_path("/History/MaxCellVoltage", 0.0)
        self._dbusservice.add_path("/History/ChargedEnergy", 0.0)
        self._dbusservice.add_path("/History/DischargedEnergy", 0.0)
        self._dbusservice.add_path("/History/AverageDischarge", 0.0)
        self._dbusservice.add_path("/History/TotalAhDrawn", 0.0)
        self._dbusservice.add_path("/ConsumedAmphours", 0.0)
        self._dbusservice.add_path("/TimeToGo", 0)

        self._dbusservice.register()
        GLib.timeout_add(1000, exit_on_error, self._update)

    def _transmit_mode(self, path, value):
        mode = safe_getattr(self._bat, "guiModeKey", {}).get(value)
        if callable(getattr(self._bat, "set_mode", None)) and self._bat.set_mode(mode) is True:
            self._dbusservice[path] = value

    def _update(self):
        # Connection status
        self._dbusservice["/Connected"] = 1 if safe_getattr(self._bat, "updated", 0) != -1 else 0

        # Alarms
        self._dbusservice["/Alarms/CellImbalance"] = int(safe_getattr(self._bat, "internalErrors", 0) & 0x20) >> 5
        self._dbusservice["/Alarms/LowVoltage"] = int(safe_getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x10) >> 3
        self._dbusservice["/Alarms/HighVoltage"] = int(safe_getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x20) >> 4
        self._dbusservice["/Alarms/LowSoc"] = int(safe_getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x08) >> 3
        self._dbusservice["/Alarms/HighDischargeCurrent"] = int(safe_getattr(self._bat, "currentAndPcbTAlarms", 0) & 0x3)
        self._dbusservice["/Alarms/HighTemperature"] = (int(safe_getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x6) >> 1) | (int(safe_getattr(self._bat, "currentAndPcbTAlarms", 0) & 0x18) >> 3)
        self._dbusservice["/Alarms/LowTemperature"] = (int(safe_getattr(self._bat, "mode", 0) & 0x60) >> 5)
        self._dbusservice["/Alarms/InternalFailure"] = int(safe_getattr(self._bat, "internalErrors", 0) != 0)
        self._dbusservice["/Alarms/HighChargeCurrent"] = int(safe_getattr(self._bat, "current", 0) > safe_getattr(self._bat, "maxChargeCurrent", 1000))
        self._dbusservice["/Alarms/HighDischargeCurrent"] = int(safe_getattr(self._bat, "current", 0) < -safe_getattr(self._bat, "maxDischargeCurrent", 1000))

        # Battery stats
        self._dbusservice["/Soc"] = safe_getattr(self._bat, "soc", 0)
        self._dbusservice["/State"] = safe_getattr(self._bat, "state", 14)
        self._dbusservice["/Mode"] = safe_getattr(self._bat, "mode", 1)

        # --- PATCH: Import all cell voltages and temperatures, min and max values directly from ubmsbattery ---
        # Pack voltage calculation
        pack_voltage = safe_getattr(self._bat, "voltage", None)
        debug_voltage_source = "ubmsbattery.voltage"
        if pack_voltage is None or pack_voltage == 0:
            module_voltages = []
            if hasattr(self._bat, "moduleVoltages"):
                try:
                    module_voltages = [float(v) for v in self._bat.moduleVoltages]
                except Exception:
                    module_voltages = []
            modules_in_series = safe_getattr(self._bat, "modulesInSeries", 4)
            if module_voltages and len(module_voltages) >= modules_in_series:
                pack_voltage = sum(module_voltages[:modules_in_series]) / 1000.0
                debug_voltage_source = "moduleVoltages[:modules_in_series]"
            elif module_voltages:
                pack_voltage = sum(module_voltages) / 1000.0
                debug_voltage_source = "moduleVoltages"
            else:
                pack_voltage = 0.0
                debug_voltage_source = "fallback 0.0"
        self._dbusservice["/Dc/0/Voltage"] = float(pack_voltage)

        current = safe_getattr(self._bat, "current", 0.0)
        temperature = safe_getattr(self._bat, "maxCellTemperature", 0.0)
        self._dbusservice["/Dc/0/Current"] = float(current)
        self._dbusservice["/Dc/0/Temperature"] = float(temperature)
        self._dbusservice["/Dc/0/Power"] = float(pack_voltage) * float(current)

        # Capacity calculation
        installed_capacity = float(self._dbusservice["/InstalledCapacity"])
        soc = float(self._dbusservice["/Soc"])
        self._dbusservice["/Capacity"] = int(installed_capacity * soc * 0.01)

        # Cell voltages and balances
        # Directly import all cell voltages from ubmsbattery.cellVoltages
        flatVList = []
        cells_per_module = safe_getattr(self._bat, "cellsPerModule", 4)
        number_of_modules = safe_getattr(self._bat, "numberOfModules", 16)
        try:
            cellVoltages = safe_getattr(self._bat, "cellVoltages", [])
            if cellVoltages and isinstance(cellVoltages, list):
                # Flatten and import
                flatVList = list(itertools.chain(*cellVoltages))
                for i in range(len(flatVList)):
                    voltage_v = float(flatVList[i]) / 1000.0 if flatVList[i] is not None else 0.0
                    self._dbusservice[f"/Voltages/Cell{i+1}"] = voltage_v
                    self._dbusservice[f"/Balances/Cell{i+1}"] = voltage_v
        except Exception as e:
            logging.warning(f"Could not process cellVoltages: {e}")

        # Cell temperatures
        # Directly import all cell temperatures from ubmsbattery.cellTemperatures
        try:
            cellTemperatures = safe_getattr(self._bat, "cellTemperatures", [])
            flatTList = []
            if cellTemperatures and isinstance(cellTemperatures, list):
                flatTList = list(itertools.chain(*cellTemperatures))
            else:
                flatTList = []
            # For min/max cell temperature:
            if flatTList:
                min_temp = min(flatTList)
                max_temp = max(flatTList)
                self._dbusservice["/System/MinCellTemperature"] = float(min_temp)
                self._dbusservice["/System/MaxCellTemperature"] = float(max_temp)
            else:
                self._dbusservice["/System/MinCellTemperature"] = safe_getattr(self._bat, "minCellTemperature", 0.0)
                self._dbusservice["/System/MaxCellTemperature"] = safe_getattr(self._bat, "maxCellTemperature", 0.0)
        except Exception as e:
            logging.warning(f"Could not process cellTemperatures: {e}")

        # Min/Max Cell Voltage and ID
        try:
            if flatVList:
                min_voltage = min(flatVList)
                max_voltage = max(flatVList)
                index_max = flatVList.index(max_voltage)
                m_max = math.floor(index_max / cells_per_module)
                c_max = index_max % cells_per_module
                index_min = flatVList.index(min_voltage)
                m_min = math.floor(index_min / cells_per_module)
                c_min = index_min % cells_per_module
                self._dbusservice["/System/MaxCellVoltageId"] = f"M{m_max+1}C{c_max+1}"
                self._dbusservice["/System/MaxCellVoltage"] = float(max_voltage) / 1000.0
                self._dbusservice["/System/MinCellVoltageId"] = f"M{m_min+1}C{c_min+1}"
                self._dbusservice["/System/MinCellVoltage"] = float(min_voltage) / 1000.0
            else:
                self._dbusservice["/System/MaxCellVoltageId"] = "M1C1"
                self._dbusservice["/System/MaxCellVoltage"] = 0.0
                self._dbusservice["/System/MinCellVoltageId"] = "M1C1"
                self._dbusservice["/System/MinCellVoltage"] = 0.0
        except Exception as e:
            logging.warning(f"Could not update min/max cell voltage: {e}")

        # PCB temp
        self._dbusservice["/System/MaxPcbTemperature"] = float(safe_getattr(self._bat, "maxPcbTemperature", 0.0))

        # Info
        self._dbusservice["/Info/MaxChargeCurrent"] = float(safe_getattr(self._bat, "maxChargeCurrent", 0))
        self._dbusservice["/Info/MaxDischargeCurrent"] = float(safe_getattr(self._bat, "maxDischargeCurrent", 0))
        self._dbusservice["/Info/MaxChargeVoltage"] = float(safe_getattr(self._bat, "maxChargeVoltage", 0))

        # Modules
        self._dbusservice["/System/NrOfModulesOnline"] = int(safe_getattr(self._bat, "numberOfModulesCommunicating", 1))
        self._dbusservice["/System/NrOfModulesOffline"] = max(0, int(safe_getattr(self._bat, "numberOfModules", 1)) - int(safe_getattr(self._bat, "numberOfModulesCommunicating", 1)))
        self._dbusservice["/System/NrOfBatteriesBalancing"] = int(safe_getattr(self._bat, "numberOfModulesBalancing", 0))

        # History/Statistics
        self._dbusservice["/History/MinCellVoltage"] = self._dbusservice["/System/MinCellVoltage"]
        self._dbusservice["/History/MaxCellVoltage"] = self._dbusservice["/System/MaxCellVoltage"]

        # Debugging output for diagnostics
        print(f"[DEBUG] UbmsBattery.voltage: {safe_getattr(self._bat, 'voltage', None)}")
        print(f"[DEBUG] D-Bus /Dc/0/Voltage = {self._dbusservice['/Dc/0/Voltage']} (type={type(self._dbusservice['/Dc/0/Voltage'])}, source={debug_voltage_source})")
        print(f"[DEBUG] D-Bus /Dc/0/Current = {self._dbusservice['/Dc/0/Current']}")
        print(f"[DEBUG] D-Bus /Dc/0/Temperature = {self._dbusservice['/Dc/0/Temperature']}")
        print(f"[DEBUG] D-Bus /System/MinCellVoltage = {self._dbusservice['/System/MinCellVoltage']}")
        print(f"[DEBUG] D-Bus /System/MaxCellVoltage = {self._dbusservice['/System/MaxCellVoltage']}")
        print(f"[DEBUG] D-Bus /System/MinCellTemperature = {self._dbusservice['/System/MinCellTemperature']}")
        print(f"[DEBUG] D-Bus /System/MaxCellTemperature = {self._dbusservice['/System/MaxCellTemperature']}")
        print(f"[DEBUG] Cell Voltages: {flatVList}")
        print(f"[DEBUG] Cell Temperatures: {safe_getattr(self._bat, 'cellTemperatures', [])}")

        return True

def main():
    parser = ArgumentParser(description="dbus_ubms", add_help=True)
    parser.add_argument(
        "-d", "--debug", help="enable debug logging", action="store_true"
    )
    parser.add_argument("-i", "--interface", help="CAN interface", default="can0")
    parser.add_argument("-c", "--capacity", help="capacity in Ah", default=130)
    parser.add_argument("-v", "--voltage", help="maximum charge voltage V", required=True)
    parser.add_argument("--modules", type=int, default=16, help="number of modules")
    parser.add_argument("--strings", type=int, default=4, help="number of strings")
    parser.add_argument("--deviceinstance", type=int, default=0, help="device instance")
    parser.add_argument("-p", "--print", help="print only")

    args = parser.parse_args()

    logging.basicConfig(
        format="%(levelname)-8s %(message)s",
        level=(logging.DEBUG if args.debug else logging.INFO),
    )

    os.system(f"ip link set {args.interface} type can bitrate 250000")
    os.system(f"ifconfig {args.interface} up")

    logging.info("Starting dbus_ubms %s on %s " % (VERSION, args.interface))

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

    logging.debug(
        "Connected to dbus, and switching over to GLib.MainLoop() (= event based)"
    )
    mainloop = GLib.MainLoop()
    mainloop.run()

if __name__ == "__main__":
    main()
