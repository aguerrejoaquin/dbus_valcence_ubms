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

# our own packages
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService  # noqa: E402
from ve_utils import exit_on_error  # noqa: E402
from settingsdevice import SettingsDevice  # noqa: E402

VERSION = "1.2.0"

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
    ):
        self.minUpdateDone = 0
        self.dailyResetDone = 0
        self.lastUpdated = 0
        self._bat = UbmsBattery(
            capacity=capacity, voltage=voltage, connection=connection
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
        self._dbusservice.add_path("/FirmwareVersion", getattr(self._bat, "firmwareVersion", "1.0"))
        self._dbusservice.add_path("/HardwareVersion", "type: " + str(getattr(self._bat, "bms_type", "UBMS")) + " rev. "+ hex(getattr(self._bat, "hw_rev", 1)))
        self._dbusservice.add_path("/Connected", 0)
        self._dbusservice.add_path("/State", 14, writeable=True)
        self._dbusservice.add_path("/Mode", 1, writeable=True, onchangecallback=self._transmit_mode) 
        self._dbusservice.add_path("/Soh", 100)
        self._dbusservice.add_path("/Capacity", int(capacity))
        self._dbusservice.add_path("/InstalledCapacity", int(capacity))
        self._dbusservice.add_path("/Dc/0/Temperature", 25)
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
        self._dbusservice.add_path("/Alarms/HighChargeCurrent", 0)
        self._dbusservice.add_path("/Alarms/HighDischargeCurrent", 0)
        self._dbusservice.add_path("/Balancing", 0)
        self._dbusservice.add_path("/System/HasTemperature", 1)
        self._dbusservice.add_path("/System/NrOfBatteries", getattr(self._bat, "numberOfModules", 1))
        self._dbusservice.add_path("/System/NrOfModulesOnline", getattr(self._bat, "numberOfModules", 1))
        self._dbusservice.add_path("/System/NrOfModulesOffline", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingDischarge", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingCharge", 0)
        self._dbusservice.add_path("/System/NrOfBatteriesBalancing", 0)
        self._dbusservice.add_path("/System/BatteriesParallel", getattr(self._bat, "numberOfStrings", 1))
        self._dbusservice.add_path("/System/BatteriesSeries", getattr(self._bat, "modulesInSeries", 1))
        self._dbusservice.add_path("/System/NrOfCellsPerBattery", getattr(self._bat, "cellsPerModule", 4))
        self._dbusservice.add_path("/System/MinVoltageCellId", "M1C1")
        self._dbusservice.add_path("/System/MaxVoltageCellId", "M1C1")
        self._dbusservice.add_path("/System/MinCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MaxCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperatureId", "M1C1")
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperatureId", "M1C1")
        self._dbusservice.add_path("/System/MaxPcbTemperature", 0.0)
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)
        self._dbusservice.add_path("/Soc", 0)

        # Per-cell voltages
        cells_total = getattr(self._bat, "cellsPerModule", 4) * getattr(self._bat, "numberOfModules", 1)
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
        mode = getattr(self._bat, "guiModeKey", {}).get(value)
        if callable(getattr(self._bat, "set_mode", None)) and self._bat.set_mode(mode) is True:
            self._dbusservice[path] = value

    def _update(self):
        # Update battery connection status
        self._dbusservice["/Connected"] = 1 if getattr(self._bat, "updated", 0) != -1 else 0

        # Alarms
        self._dbusservice["/Alarms/CellImbalance"] = int(getattr(self._bat, "internalErrors", 0) & 0x20) >> 5
        self._dbusservice["/Alarms/LowVoltage"] = int(getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x10) >> 3
        self._dbusservice["/Alarms/HighVoltage"] = int(getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x20) >> 4
        self._dbusservice["/Alarms/LowSoc"] = int(getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x08) >> 3
        self._dbusservice["/Alarms/HighDischargeCurrent"] = int(getattr(self._bat, "currentAndPcbTAlarms", 0) & 0x3)
        self._dbusservice["/Alarms/HighTemperature"] = (int(getattr(self._bat, "voltageAndCellTAlarms", 0) & 0x6) >> 1) | (int(getattr(self._bat, "currentAndPcbTAlarms", 0) & 0x18) >> 3)
        self._dbusservice["/Alarms/LowTemperature"] = (int(getattr(self._bat, "mode", 0) & 0x60) >> 5)
        self._dbusservice["/Alarms/InternalFailure"] = int(getattr(self._bat, "internalErrors", 0) != 0)
        self._dbusservice["/Alarms/HighChargeCurrent"] = int(getattr(self._bat, "current", 0) > getattr(self._bat, "maxChargeCurrent", 1000))
        self._dbusservice["/Alarms/HighDischargeCurrent"] = int(getattr(self._bat, "current", 0) < -getattr(self._bat, "maxDischargeCurrent", 1000))

        # Basic stats
        self._dbusservice["/Soc"] = getattr(self._bat, "soc", 0)
        self._dbusservice["/State"] = getattr(self._bat, "state", 14)
        self._dbusservice["/Mode"] = getattr(self._bat, "mode", 1)

        # Voltage, Current, Power, Temp
        voltage = getattr(self._bat, "voltage", 0.0)
        current = getattr(self._bat, "current", 0.0)
        temperature = getattr(self._bat, "maxCellTemperature", 0.0)
        self._dbusservice["/Dc/0/Voltage"] = voltage
        self._dbusservice["/Dc/0/Current"] = current
        self._dbusservice["/Dc/0/Temperature"] = temperature
        self._dbusservice["/Dc/0/Power"] = voltage * current

        # Capacity calculation
        installed_capacity = float(self._dbusservice["/InstalledCapacity"])
        soc = float(self._dbusservice["/Soc"])
        self._dbusservice["/Capacity"] = int(installed_capacity * soc * 0.01)

        # Per-cell voltages
        flatVList = []
        if hasattr(self._bat, "cellVoltages"):
            chain = itertools.chain(*getattr(self._bat, "cellVoltages", [[]]))
            flatVList = list(chain)
            for i in range(len(flatVList)):
                voltage_v = flatVList[i] / 1000.0 if flatVList[i] is not None else 0.0
                self._dbusservice[f"/Voltages/Cell{i+1}"] = voltage_v
                self._dbusservice[f"/Balances/Cell{i+1}"] = voltage_v

        # Sum and diff
        voltageSum = sum([v / 1000.0 for v in flatVList if v is not None])
        self._dbusservice["/Voltages/Sum"] = voltageSum
        if flatVList:
            self._dbusservice["/Voltages/Diff"] = max(flatVList)/1000.0 - min(flatVList)/1000.0

        # Min/Max Cell Voltage and ID
        if flatVList:
            index_max = flatVList.index(max(flatVList))
            m_max = math.floor(index_max / getattr(self._bat, "cellsPerModule", 4))
            c_max = index_max % getattr(self._bat, "cellsPerModule", 4)
            self._dbusservice["/System/MaxVoltageCellId"] = f"M{m_max+1}C{c_max+1}"
            self._dbusservice["/System/MaxCellVoltage"] = max(flatVList) / 1000.0

            index_min = flatVList.index(min(flatVList))
            m_min = math.floor(index_min / getattr(self._bat, "cellsPerModule", 4))
            c_min = index_min % getattr(self._bat, "cellsPerModule", 4)
            self._dbusservice["/System/MinVoltageCellId"] = f"M{m_min+1}C{c_min+1}"
            self._dbusservice["/System/MinCellVoltage"] = min(flatVList) / 1000.0
        else:
            self._dbusservice["/System/MaxVoltageCellId"] = "M1C1"
            self._dbusservice["/System/MaxCellVoltage"] = 0.0
            self._dbusservice["/System/MinVoltageCellId"] = "M1C1"
            self._dbusservice["/System/MinCellVoltage"] = 0.0

        # Min/Max Cell Temperature and ID
        # Try cellTemperatures first, then moduleTemperatures, fallback to M1C1
        if hasattr(self._bat, "cellTemperatures") and self._bat.cellTemperatures:
            chainT = itertools.chain(*self._bat.cellTemperatures)
            flatTList = list(chainT)
            min_temp = min(flatTList)
            max_temp = max(flatTList)
            min_temp_index = flatTList.index(min_temp)
            max_temp_index = flatTList.index(max_temp)
            m_min = math.floor(min_temp_index / getattr(self._bat, "cellsPerModule", 4))
            c_min = min_temp_index % getattr(self._bat, "cellsPerModule", 4)
            m_max = math.floor(max_temp_index / getattr(self._bat, "cellsPerModule", 4))
            c_max = max_temp_index % getattr(self._bat, "cellsPerModule", 4)
            self._dbusservice["/System/MinCellTemperature"] = min_temp
            self._dbusservice["/System/MaxCellTemperature"] = max_temp
            self._dbusservice["/System/MinCellTemperatureId"] = f"M{m_min+1}C{c_min+1}"
            self._dbusservice["/System/MaxCellTemperatureId"] = f"M{m_max+1}C{c_max+1}"
        elif hasattr(self._bat, "moduleTemperatures") and self._bat.moduleTemperatures:
            min_temp = min(self._bat.moduleTemperatures)
            max_temp = max(self._bat.moduleTemperatures)
            m_min = self._bat.moduleTemperatures.index(min_temp)
            m_max = self._bat.moduleTemperatures.index(max_temp)
            self._dbusservice["/System/MinCellTemperature"] = min_temp
            self._dbusservice["/System/MaxCellTemperature"] = max_temp
            self._dbusservice["/System/MinCellTemperatureId"] = f"M{m_min+1}C1"
            self._dbusservice["/System/MaxCellTemperatureId"] = f"M{m_max+1}C1"
        else:
            self._dbusservice["/System/MinCellTemperature"] = getattr(self._bat, "minCellTemperature", 0.0)
            self._dbusservice["/System/MaxCellTemperature"] = getattr(self._bat, "maxCellTemperature", 0.0)
            self._dbusservice["/System/MinCellTemperatureId"] = "M1C1"
            self._dbusservice["/System/MaxCellTemperatureId"] = "M1C1"

        # PCB temp
        self._dbusservice["/System/MaxPcbTemperature"] = getattr(self._bat, "maxPcbTemperature", 0.0)

        # Info
        self._dbusservice["/Info/MaxChargeCurrent"] = getattr(self._bat, "maxChargeCurrent", 0)
        self._dbusservice["/Info/MaxDischargeCurrent"] = getattr(self._bat, "maxDischargeCurrent", 0)
        self._dbusservice["/Info/MaxChargeVoltage"] = getattr(self._bat, "maxChargeVoltage", 0)

        # Modules
        self._dbusservice["/System/NrOfModulesOnline"] = getattr(self._bat, "numberOfModulesCommunicating", 1)
        self._dbusservice["/System/NrOfModulesOffline"] = max(0, getattr(self._bat, "numberOfModules", 1) - getattr(self._bat, "numberOfModulesCommunicating", 1))
        self._dbusservice["/System/NrOfBatteriesBalancing"] = getattr(self._bat, "numberOfModulesBalancing", 0)

        # History/Statistics
        self._dbusservice["/History/MinCellVoltage"] = self._dbusservice["/System/MinCellVoltage"]
        self._dbusservice["/History/MaxCellVoltage"] = self._dbusservice["/System/MaxCellVoltage"]

        return True

def main():
    parser = ArgumentParser(description="dbus_ubms", add_help=True)
    parser.add_argument(
        "-d", "--debug", help="enable debug logging", action="store_true"
    )
    parser.add_argument("-i", "--interface", help="CAN interface")
    parser.add_argument("-c", "--capacity", help="capacity in Ah")
    parser.add_argument("-v", "--voltage", help="maximum charge voltage V")
    parser.add_argument("-p", "--print", help="print only")

    args = parser.parse_args()

    logging.basicConfig(
        format="%(levelname)-8s %(message)s",
        level=(logging.DEBUG if args.debug else logging.INFO),
    )

    if not args.interface:
        logging.info("No CAN interface specified, using default can0")
        args.interface = "can0"

    if not args.capacity:
        logging.warning("Battery capacity not specified, using default (130Ah)")
        args.capacity = 130

    if not args.voltage:
        logging.error("Maximum charge voltage not specified. Exiting.")
        return

    os.system("ip link set can0 type can bitrate 250000")
    os.system("ifconfig can0 up")

    logging.info("Starting dbus_ubms %s on %s " % (VERSION, args.interface))

    from dbus.mainloop.glib import DBusGMainLoop

    DBusGMainLoop(set_as_default=True)

    DbusBatteryService(
        servicename="com.victronenergy.battery",
        connection=args.interface,
        deviceinstance=0,
        capacity=int(args.capacity),
        voltage=float(args.voltage),
    )

    logging.debug(
        "Connected to dbus, and switching over to GLib.MainLoop() (= event based)"
    )
    mainloop = GLib.MainLoop()
    mainloop.run()

if __name__ == "__main__":
    main()
