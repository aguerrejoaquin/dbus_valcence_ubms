#!/usr/bin/env python3

"""
Put a battery service on the dbus, according to Victron standards, with constantly updating paths.
Adapted for 16 modules and 4 strings on can0 for Venus OS compatibility.
"""
from gi.repository import GLib
import dbus.mainloop.glib
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

from gi.repository import GLib
import platform
import logging
import sys
import os
import dbus
from time import time
from argparse import ArgumentParser

from ubmsbattery import UbmsBattery

# Adjust this path if needed!
sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext/velib_python"))
from vedbus import VeDbusService

VERSION = "1.1.0"

class DbusBatteryService:
    def __init__(self, servicename, deviceinstance, voltage, capacity, connection="can0"):
        self._bat = UbmsBattery(capacity=capacity, voltage=voltage, connection=connection)
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
        self._dbusservice.add_path("/System/NrOfModules", 16)
        self._dbusservice.add_path("/System/NrOfStrings", 4)
        self._dbusservice.add_path("/System/NrOfModulesPerString", 4)

        # Real-time values
        self._dbusservice.add_path("/Dc/0/Voltage", 0.0)
        self._dbusservice.add_path("/Dc/0/Current", 0.0)
        self._dbusservice.add_path("/Soc", 0)
        self._dbusservice.add_path("/Dc/0/Power", 0.0)

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

        return True  # Continue running

def main():
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
        connection=args.interface
    )
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
