#!/usr/bin/env python3
import sys
import time
import logging
import argparse
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

class UbmsBattery:
    def __init__(self, interface):
        self.voltage = 52.0
        self.current = 10.0
        self.soc = 80.0
        # Add more attributes as needed

    def update_from_can(self):
        import random
        self.voltage = 52.0 + random.uniform(-1.0, 1.0)
        self.current = 10.0 + random.uniform(-0.5, 0.5)
        self.soc = 80.0 + random.uniform(-2.0, 2.0)

class BatteryValue(dbus.service.Object):
    def __init__(self, bus, path, getter):
        super().__init__(bus, path)
        self.getter = getter

    @dbus.service.method('com.victronenergy.BusItem', in_signature='', out_signature='v')
    def GetValue(self):
        return dbus.Double(self.getter())

    @dbus.service.method('org.freedesktop.DBus.Introspectable', in_signature='', out_signature='s')
    def Introspect(self):
        return ""

class DbusUbmsService:
    def __init__(self, battery, servicename):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._bus_name = dbus.service.BusName(servicename, bus=self._bus)
        self._bat = battery

        # Register actual D-Bus objects for each value
        self.voltage_obj = BatteryValue(self._bus, "/Dc/0/Voltage", lambda: self._bat.voltage)
        self.current_obj = BatteryValue(self._bus, "/Dc/0/Current", lambda: self._bat.current)
        self.soc_obj = BatteryValue(self._bus, "/Soc", lambda: self._bat.soc)

        GLib.timeout_add(1000, self._update)

    def _update(self):
        self._bat.update_from_can()
        logging.info("Voltage: %s", self._bat.voltage)
        logging.info("Current: %s", self._bat.current)
        logging.info("SoC: %s", self._bat.soc)
        return True

def parse_args():
    parser = argparse.ArgumentParser(description="DBus UBMS Battery Service with Debug Logging")
    parser.add_argument('--interface', '-i', type=str, default='can0', help='CAN interface')
    parser.add_argument('--capacity', '-c', type=float, required=True, help='Battery capacity')
    parser.add_argument('--voltage', '-v', type=float, required=True, help='Nominal voltage')
    parser.add_argument('--deviceinstance', type=int, default=0, help='Device instance')
    parser.add_argument('--debug', '-d', action='store_true', help='Enable debug logging')
    parser.add_argument('--logfile', type=str, default=None, help='Log file')
    return parser.parse_args()

def main():
    args = parse_args()
    loglevel = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        filename=args.logfile,
        level=loglevel,
        format='%(asctime)s %(levelname)s: %(message)s'
    )
    logging.info("Starting dbus_ubms.py with args: %s", args)

    battery = UbmsBattery(args.interface)
    servicename = f'com.victronenergy.battery.socketcan_{args.interface}_di{args.deviceinstance}'
    logging.info("Registering D-Bus service name: %s", servicename)
    service = DbusUbmsService(battery, servicename)

    try:
        loop = GLib.MainLoop()
        loop.run()
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, stopping service.")
        sys.exit(0)
    except Exception as e:
        logging.exception("Unhandled exception: %s", e)
        sys.exit(1)

if __name__ == '__main__':
    main()
