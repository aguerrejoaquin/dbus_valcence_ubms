#!/usr/bin/env python3

"""
Minimal D-Bus battery service for Victron, publishing voltage and all key stats
directly from UbmsBattery.
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

VERSION = "1.2.0"

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
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Dc/0/Temperature", 0.0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.register()
        # Update every second
        GLib.timeout_add(1000, exit_on_error, self._update)

    def _update(self):
        # Always use UbmsBattery.voltage (which is set in the CAN handler)
        voltage = getattr(self._bat, "voltage", None)
        current = getattr(self._bat, "current", 0.0)
        temperature = getattr(self._bat, "maxCellTemperature", 0.0)
        soc = getattr(self._bat, "soc", 0)

        # Debug: show what we're publishing
        print(f"[DEBUG] UbmsBattery.voltage = {voltage} (type={type(voltage)})")
        print(f"[DEBUG] UbmsBattery.current = {current}")
        print(f"[DEBUG] UbmsBattery.maxCellTemperature = {temperature}")
        print(f"[DEBUG] UbmsBattery.soc = {soc}")

        # Ensure type is float, otherwise Victron will show zero!
        try:
            voltage_f = float(voltage) if voltage is not None else 0.0
        except Exception as e:
            logging.error(f"Could not convert voltage to float: {e}")
            voltage_f = 0.0

        self._dbusservice["/Dc/0/Voltage"] = voltage_f
        self._dbusservice["/Dc/0/Current"] = float(current)
        self._dbusservice["/Dc/0/Temperature"] = float(temperature)
        self._dbusservice["/Dc/0/Power"] = voltage_f * float(current)
        self._dbusservice["/Soc"] = float(soc)
        self._dbusservice["/Connected"] = 1 if getattr(self._bat, "updated", -1) != -1 else 0

        print(f"[DEBUG] Published /Dc/0/Voltage = {self._dbusservice['/Dc/0/Voltage']}")

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
