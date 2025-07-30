#!/usr/bin/env python3

import logging
import time
import argparse
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop
import dbus
from ubmsbattery import UbmsBattery

class DbusService:
    def __init__(self, battery):
        self._bat = battery

        # Setup DBus main loop
        DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        # This is a placeholder for the real DBus service initialization.
        # Replace this with your actual DBus service registration
        # For demo, we use a dictionary to simulate DBus paths.
        self._dbusservice = {}
        self._init_paths()

    def _init_paths(self):
        # Initialize all necessary DBus paths with '0' or starting value
        self._dbusservice["/Dc/0/Voltage"] = 0.0
        self._dbusservice["/Dc/0/Current"] = 0.0
        self._dbusservice["/Soc"] = 0.0
        self._dbusservice["/System/NrOfModulesOnline"] = 0
        self._dbusservice["/System/NrOfModules"] = self._bat.numberOfModules

    def update(self):
        # Always use the calculated pack voltage for DBus!
        pack_voltage = self._bat.get_pack_voltage()
        # Also update .voltage for compatibility
        self._bat.voltage = pack_voltage

        # DEBUG: Print the voltage being sent to DBus
        logging.debug(
            f"DBUS UPDATE: bat.voltage={self._bat.voltage:.3f}V, get_pack_voltage={pack_voltage:.3f}V, current={self._bat.current}A, soc={self._bat.soc}%"
        )

        self._dbusservice["/Dc/0/Voltage"] = pack_voltage
        self._dbusservice["/Dc/0/Current"] = self._bat.current
        self._dbusservice["/Soc"] = self._bat.soc
        self._dbusservice["/System/NrOfModulesOnline"] = self._bat.numberOfModulesCommunicating
        self._dbusservice["/System/NrOfModules"] = self._bat.numberOfModules

        # If using a real DBus service, here is where you would set the values on the DBus paths

    def run(self):
        # Main GLib loop with periodic update
        loop = GLib.MainLoop()
        def periodic_update():
            self.update()
            return True  # Repeat this callback
        GLib.timeout_add_seconds(1, periodic_update)
        loop.run()

def main():
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
