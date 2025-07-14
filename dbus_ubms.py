#!/usr/bin/env python3
import sys
import time
import logging
import argparse
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# Replace this with your real UbmsBattery implementation!
class UbmsBattery:
    def __init__(self, interface):
        self.voltage = 0.0
        self.current = 0.0
        self.soc = 0.0
        self.maxCellVoltage = 0.0
        self.minCellVoltage = 0.0
        self.cellVoltages = []
        self.maxCellTemperature = 0.0
        self.minCellTemperature = 0.0
        self.state = "unknown"
        self.chargeComplete = False
        self.numberOfModules = 0
        self.numberOfModulesCommunicating = 0
        self.numberOfModulesBalancing = 0
        # Add more initialization as needed

    def update_from_can(self):
        # Here you would implement CAN parsing and set the above attributes
        # For debug/demo, we'll just cycle the voltage
        import random
        self.voltage = 52.0 + random.uniform(-1.0, 1.0)
        self.current = 10.0 + random.uniform(-0.5, 0.5)
        self.soc = 80.0 + random.uniform(-2.0, 2.0)
        self.maxCellVoltage = 3.5
        self.minCellVoltage = 3.3
        self.cellVoltages = [3.3 + random.uniform(0, 0.2) for _ in range(16)]
        self.maxCellTemperature = 35.0
        self.minCellTemperature = 25.0
        self.state = "charging"
        self.chargeComplete = False
        self.numberOfModules = 1
        self.numberOfModulesCommunicating = 1
        self.numberOfModulesBalancing = 0

class DbusUbmsService(dbus.service.Object):
    def __init__(self, battery, deviceinstance, servicename):
        self._bat = battery
        self._deviceinstance = deviceinstance
        self._servicename = servicename

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._bus_name = dbus.service.BusName(self._servicename, bus=self._bus)

        dbus.service.Object.__init__(self, self._bus_name, '/')

        # Register D-Bus values as properties
        self._values = {
            "/Dc/0/Voltage": 0.0,
            "/Dc/0/Current": 0.0,
            "/Soc": 0.0
        }

        # Start periodic update (every 1 second)
        GLib.timeout_add(1000, self._update)

    def _update(self):
        try:
            self._bat.update_from_can()
            logging.info("=== dbus_ubms Update Start ===")
            logging.info("UbmsBattery.voltage: %s", self._bat.voltage)
            logging.info("UbmsBattery.current: %s", self._bat.current)
            logging.info("UbmsBattery.soc: %s", self._bat.soc)
            logging.info("UbmsBattery.maxCellVoltage: %s", self._bat.maxCellVoltage)
            logging.info("UbmsBattery.minCellVoltage: %s", self._bat.minCellVoltage)
            logging.info("UbmsBattery.cellVoltages: %s", self._bat.cellVoltages)
            logging.info("UbmsBattery.maxCellTemperature: %s", self._bat.maxCellTemperature)
            logging.info("UbmsBattery.minCellTemperature: %s", self._bat.minCellTemperature)
            logging.info("UbmsBattery.state: %s", self._bat.state)
            logging.info("UbmsBattery.chargeComplete: %s", self._bat.chargeComplete)
            logging.info("UbmsBattery.numberOfModules: %s", self._bat.numberOfModules)
            logging.info("UbmsBattery.numberOfModulesCommunicating: %s", self._bat.numberOfModulesCommunicating)
            logging.info("UbmsBattery.numberOfModulesBalancing: %s", self._bat.numberOfModulesBalancing)
        except Exception as e:
            logging.exception("Error while dumping UbmsBattery debug values: %s", e)

        # Assign values to D-Bus (simulate actual publishing)
        try:
            self._values["/Dc/0/Voltage"] = float(self._bat.voltage)
            self._values["/Dc/0/Current"] = float(self._bat.current)
            self._values["/Soc"] = float(self._bat.soc)
        except Exception as e:
            logging.exception("Error while updating D-Bus values: %s", e)

        return True  # True to keep timer running

    # D-Bus property getter
    @dbus.service.method(dbus_interface='com.victronenergy.BusItem',
                         in_signature='', out_signature='v')
    def GetValue(self):
        # Simple demo: always return voltage (for /Dc/0/Voltage path)
        return dbus.Double(self._bat.voltage)

    # D-Bus Introspection (optional, for tools)
    @dbus.service.method('org.freedesktop.DBus.Introspectable',
                         in_signature='', out_signature='s')
    def Introspect(self):
        return ""

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
    service = DbusUbmsService(battery, args.deviceinstance, servicename)

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
