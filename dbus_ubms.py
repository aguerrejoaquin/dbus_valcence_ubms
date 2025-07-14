#!/usr/bin/env python3

"""
D-Bus battery service for Victron, publishing all key stats directly from UbmsBattery,
with robust debug output and publishing to both /Dc/0/Voltage and /Dc/Battery/Voltage.
"""

from gi.repository import GLib
import platform
import logging
import sys
import os
import dbus
from argparse import ArgumentParser

from ubmsbattery import UbmsBattery

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService
from ve_utils import exit_on_error

VERSION = "1.3.0"

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
        self._bat = UbmsBattery(capacity=capacity, voltage=voltage, connection=connection)
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
        self._dbusservice.add_path("/Dc/Battery/Voltage", 0.0)  # For Victron compatibility
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Dc/Battery/Current", 0.0)
        self._dbusservice.add_path("/Dc/0/Temperature", 0.0)
        self._dbusservice.add_path("/Dc/Battery/Temperature", 0.0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)
        self._dbusservice.add_path("/Dc/Battery/Power", 0.0)
        self._dbusservice.add_path("/Soc", 0)
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
        self._dbusservice.add_path("/System/NrOfBatteries", getattr(self._bat, "numberOfModules", 1))
        self._dbusservice.add_path("/System/NrOfModulesOnline", getattr(self._bat, "numberOfModules", 1))
        self._dbusservice.add_path("/System/NrOfModulesOffline", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingDischarge", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingCharge", 0)
        self._dbusservice.add_path("/System/NrOfBatteriesBalancing", 0)
        self._dbusservice.add_path("/System/BatteriesParallel", getattr(self._bat, "numberOfStrings", 1))
        self._dbusservice.add_path("/System/BatteriesSeries", getattr(self._bat, "modulesInSeries", 1))
        self._dbusservice.add_path("/System/NrOfCellsPerBattery", getattr(self._bat, "cellsPerModule", 4))
        self._dbusservice.add_path("/System/MinCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MaxCellVoltage", 0.0)
        self._dbusservice.add_path("/System/MinCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxCellTemperature", 0.0)
        self._dbusservice.add_path("/System/MaxPcbTemperature", 0.0)
        self._dbusservice.register()
        GLib.timeout_add(1000, exit_on_error, self._update)

    def _update(self):
        voltage = getattr(self._bat, "voltage", 0.0)
        current = getattr(self._bat, "current", 0.0)
        temperature = getattr(self._bat, "maxCellTemperature", 0.0)
        soc = getattr(self._bat, "soc", 0)
        power = float(voltage) * float(current)

        # Debug output
        print(f"[DEBUG] UbmsBattery.voltage = {voltage} (type={type(voltage)})")
        print(f"[DEBUG] UbmsBattery.current = {current}")
        print(f"[DEBUG] UbmsBattery.maxCellTemperature = {temperature}")
        print(f"[DEBUG] UbmsBattery.soc = {soc}")
        print(f"[DEBUG] Published /Dc/0/Voltage = {float(voltage)}")
        print(f"[DEBUG] Published /Dc/Battery/Voltage = {float(voltage)}")

        self._dbusservice["/Dc/0/Voltage"] = float(voltage)
        self._dbusservice["/Dc/Battery/Voltage"] = float(voltage)
        self._dbusservice["/Dc/0/Current"] = float(current)
        self._dbusservice["/Dc/Battery/Current"] = float(current)
        self._dbusservice["/Dc/0/Temperature"] = float(temperature)
        self._dbusservice["/Dc/Battery/Temperature"] = float(temperature)
        self._dbusservice["/Dc/0/Power"] = power
        self._dbusservice["/Dc/Battery/Power"] = power
        self._dbusservice["/Soc"] = float(soc)
        self._dbusservice["/Connected"] = 1 if getattr(self._bat, "updated", -1) != -1 else 0

        # Add more paths and values as needed for full Victron compatibility

        return True

def main():
    parser = ArgumentParser(description="dbus_ubms", add_help=True)
    parser.add_argument("-i", "--interface", help="CAN interface", default="can0")
    parser.add_argument("-c", "--capacity", help="capacity in Ah", default=130)
    parser.add_argument("-v", "--voltage", help="maximum charge voltage V", required=True)
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
        deviceinstance=0,
        capacity=int(args.capacity),
        voltage=float(args.voltage),
    )
    print("Connected to dbus, switching to GLib.MainLoop()")
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
