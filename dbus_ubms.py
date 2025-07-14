#!/usr/bin/env python3
import sys
import time
import logging
import argparse
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# Dummy UbmsBattery for illustration; replace with your real class
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

    # Implement your CAN parsing/updating logic here

class DbusUbmsService:
    def __init__(self, battery, deviceinstance):
        self._bat = battery
        self._deviceinstance = deviceinstance
        self._running = True

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._servicename = f'com.victronenergy.battery.socketcan_can0_di{deviceinstance}'
        self._dbusservice = dbus.service.BusName(self._servicename, bus=self._bus)

        # You would register all D-Bus paths here in a real implementation
        self._values = {
            "/Dc/0/Voltage": 0.0,
            "/Dc/0/Current": 0.0,
            "/Soc": 0.0
        }

        # Timer for regular updates
        GLib.timeout_add(1000, self._update)

    def _update(self):
        try:
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

        # Assign values to D-Bus (replace with actual D-Bus path setting in your framework)
        try:
            # Example: set D-Bus paths
            self._values["/Dc/0/Voltage"] = float(self._bat.voltage)
            self._values["/Dc/0/Current"] = float(self._bat.current)
            self._values["/Soc"] = float(self._bat.soc)

            # If you have a D-Bus library that needs explicit notification, do so here
            # For example: self._dbusservice["/Dc/0/Voltage"] = float(self._bat.voltage)

        except Exception as e:
            logging.exception("Error while updating D-Bus values: %s", e)

        return self._running  # True to keep timer running

    def stop(self):
        self._running = False

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

    # Replace with your real UbmsBattery initialization and CAN logic
    battery = UbmsBattery(args.interface)

    # Service initialization
    service = DbusUbmsService(battery, args.deviceinstance)

    # Main event loop
    try:
        loop = GLib.MainLoop()
        loop.run()
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, stopping service.")
        service.stop()
        sys.exit(0)
    except Exception as e:
        logging.exception("Unhandled exception: %s", e)
        sys.exit(1)

if __name__ == '__main__':
    main()
