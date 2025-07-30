#!/usr/bin/env python3

import logging
import time
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop
import dbus
from ubmsbattery import UbmsBattery

class DbusService:
    def __init__(self, battery):
        self._bat = battery

        DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        # Example DBus service setup
        self._dbusservice = self._bus.request_name("com.victronenergy.battery.ubms")

        # Use a dictionary to simulate the relevant DBus paths for demonstration
        self.paths = {
            "/Dc/0/Voltage": self._bat.voltage,
            "/Dc/0/Current": self._bat.current,
            "/Soc": self._bat.soc,
            "/System/NrOfModulesOnline": self._bat.numberOfModulesCommunicating,
            "/System/NrOfModules": self._bat.numberOfModules,
        }

    def update(self):
        # Always use the calculated pack voltage from battery.get_pack_voltage()
        self._bat.voltage = self._bat.get_pack_voltage()
        self.paths["/Dc/0/Voltage"] = self._bat.voltage
        self.paths["/Dc/0/Current"] = self._bat.current
        self.paths["/Soc"] = self._bat.soc
        self.paths["/System/NrOfModulesOnline"] = self._bat.numberOfModulesCommunicating
        self.paths["/System/NrOfModules"] = self._bat.numberOfModules

        # Here you would actually update the DBus paths for Victron, e.g.:
        # self._dbusservice["/Dc/0/Voltage"] = self._bat.voltage
        # etc.

        logging.debug(f"DBus update: voltage={self._bat.voltage:.3f}V, current={self._bat.current}A, soc={self._bat.soc}%")

    def run(self):
        # Main loop
        loop = GLib.MainLoop()
        def periodic_update():
            self.update()
            return True  # repeat
        GLib.timeout_add_seconds(1, periodic_update)
        loop.run()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=650)
    parser.add_argument("--voltage", type=float, default=29.0)
    parser.add_argument("--connection", type=str, default="can0")
    parser.add_argument("--modules", type=int, default=16)
    parser.add_argument("--strings", type=int, default=4)
    args = parser.parse_args()

    logging.basicConfig(format="%(levelname)-8s %(message)s", level=logging.DEBUG)
    battery = UbmsBattery(
        capacity=args.capacity,
        voltage=args.voltage,
        connection=args.connection,
        numberOfModules=args.modules,
        numberOfStrings=args.strings
    )

    dbus_service = DbusService(battery)
    dbus_service.run()

if __name__ == "__main__":
    main()
