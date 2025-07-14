#!/usr/bin/env python3
import sys
import logging
import argparse
import os
import signal
from gi.repository import GLib

# Make sure the ve lib path is correct for your Venus OS install
sys.path.insert(1, '/opt/victronenergy/velib/python')
from dbus.mainloop.glib import DBusGMainLoop
from dbusservice import VeDbusService

class UbmsBattery:
    def __init__(self, interface):
        self.interface = interface
        self.voltage = 52.0
        self.current = 10.0
        self.soc = 80.0
        self.cellVoltages = [3.31, 3.33, 3.45, 3.40, 3.55, 3.41, 3.38, 3.49]
        self.cellTemperatures = [25.1, 25.9, 26.3, 27.0, 26.5, 25.6, 25.8, 26.1]
        self.moduleSocs = [80.0]  # Extend if you have more modules
        self.moduleTemperatures = [26.0]  # Extend if you have more modules
        self.numberOfModules = len(self.moduleSocs)
        self.numberOfModulesCommunicating = len(self.moduleSocs)
        self.numberOfModulesBalancing = 0
        self.maxCellVoltage = max(self.cellVoltages)
        self.minCellVoltage = min(self.cellVoltages)
        self.maxCellTemperature = max(self.cellTemperatures)
        self.minCellTemperature = min(self.cellTemperatures)
        self.state = "charging"
        self.chargeComplete = False

    def update_from_can(self):
        import random
        self.voltage = 52.0 + random.uniform(-1.0, 1.0)
        self.current = 10.0 + random.uniform(-0.5, 0.5)
        self.soc = 80.0 + random.uniform(-2.0, 2.0)
        self.cellVoltages = [3.3 + random.uniform(0, 0.2) for _ in range(len(self.cellVoltages))]
        self.cellTemperatures = [25.0 + random.uniform(0, 3.0) for _ in range(len(self.cellTemperatures))]
        self.moduleSocs = [self.soc + random.uniform(-5, 5) for _ in range(self.numberOfModules)]
        self.moduleTemperatures = [26.0 + random.uniform(-2, 2) for _ in range(self.numberOfModules)]
        self.maxCellVoltage = max(self.cellVoltages)
        self.minCellVoltage = min(self.cellVoltages)
        self.maxCellTemperature = max(self.cellTemperatures)
        self.minCellTemperature = min(self.cellTemperatures)

class DbusUbmsService:
    def __init__(self, battery, deviceinstance, productname, connection, serial):
        self._bat = battery

        # Standard Victron BMS D-Bus service name for CAN
        service_name = f'com.victronenergy.battery.socketcan_{self._bat.interface}_di{deviceinstance}'
        logging.info(f"Registering D-Bus service name: {service_name}")

        self._dbusservice = VeDbusService(
            service_name,
            deviceinstance=deviceinstance
        )

        # *** REQUIRED FOR DETECTION ***
        self._dbusservice.add_path('/DeviceInstance', deviceinstance, writeable=False)
        self._dbusservice.add_path('/Connected', 1, writeable=False)
        self._dbusservice.add_path('/ProductId', 0xB004, writeable=False)
        self._dbusservice.add_path('/ProductName', productname)
        self._dbusservice.add_path('/FirmwareVersion', '1.0')
        self._dbusservice.add_path('/HardwareVersion', '1.0')
        self._dbusservice.add_path('/Serial', serial)
        self._dbusservice.add_path('/Mgmt/ProcessName', os.path.basename(__file__))
        self._dbusservice.add_path('/Mgmt/ProcessVersion', 'v1.0')
        self._dbusservice.add_path('/Mgmt/Connection', connection)

        # D-Bus battery paths (minimum required)
        self._dbusservice.add_path('/Dc/0/Voltage', 0, writeable=False)
        self._dbusservice.add_path('/Dc/0/Current', 0, writeable=False)
        self._dbusservice.add_path('/Soc', 0, writeable=False)

        # System paths
        self._dbusservice.add_path('/System/NrOfModules', self._bat.numberOfModules, writeable=False)
        self._dbusservice.add_path('/System/NrOfModulesOnline', self._bat.numberOfModulesCommunicating, writeable=False)
        self._dbusservice.add_path('/System/NrOfModulesBalancing', self._bat.numberOfModulesBalancing, writeable=False)
        self._dbusservice.add_path('/System/MinCellTemperature', 0, writeable=False)
        self._dbusservice.add_path('/System/MaxCellTemperature', 0, writeable=False)
        self._dbusservice.add_path('/System/MinCellVoltage', 0, writeable=False)
        self._dbusservice.add_path('/System/MaxCellVoltage', 0, writeable=False)
        self._dbusservice.add_path('/System/NrOfCells', len(self._bat.cellVoltages), writeable=False)

        # Per-cell voltage and temperature
        for idx in range(len(self._bat.cellVoltages)):
            self._dbusservice.add_path(f'/System/Cell/{idx+1}/Voltage', 0, writeable=False)
            self._dbusservice.add_path(f'/System/Cell/{idx+1}/Temperature', 0, writeable=False)

        # Per-module SoC and temperature
        for idx in range(self._bat.numberOfModules):
            self._dbusservice.add_path(f'/System/Module/{idx+1}/Soc', 0, writeable=False)
            self._dbusservice.add_path(f'/System/Module/{idx+1}/Temperature', 0, writeable=False)

        GLib.timeout_add(1000, self._update)

    def _update(self):
        self._bat.update_from_can()
        # Main values
        self._dbusservice['/Dc/0/Voltage'] = float(self._bat.voltage)
        self._dbusservice['/Dc/0/Current'] = float(self._bat.current)
        self._dbusservice['/Soc'] = float(self._bat.soc)
        # System info
        self._dbusservice['/System/NrOfModules'] = int(self._bat.numberOfModules)
        self._dbusservice['/System/NrOfModulesOnline'] = int(self._bat.numberOfModulesCommunicating)
        self._dbusservice['/System/NrOfModulesBalancing'] = int(self._bat.numberOfModulesBalancing)
        self._dbusservice['/System/MinCellTemperature'] = float(self._bat.minCellTemperature)
        self._dbusservice['/System/MaxCellTemperature'] = float(self._bat.maxCellTemperature)
        self._dbusservice['/System/MinCellVoltage'] = float(self._bat.minCellVoltage)
        self._dbusservice['/System/MaxCellVoltage'] = float(self._bat.maxCellVoltage)
        self._dbusservice['/System/NrOfCells'] = len(self._bat.cellVoltages)
        # Per-cell voltage and temperature
        for idx, v in enumerate(self._bat.cellVoltages):
            self._dbusservice[f'/System/Cell/{idx+1}/Voltage'] = float(v)
        for idx, t in enumerate(self._bat.cellTemperatures):
            self._dbusservice[f'/System/Cell/{idx+1}/Temperature'] = float(t)
        # Per-module SoC and temperature
        for idx, soc in enumerate(self._bat.moduleSocs):
            self._dbusservice[f'/System/Module/{idx+1}/Soc'] = float(soc)
        for idx, t in enumerate(self._bat.moduleTemperatures):
            self._dbusservice[f'/System/Module/{idx+1}/Temperature'] = float(t)
        return True

def parse_args():
    parser = argparse.ArgumentParser(description="DBus UBMS Battery Service for Victron Venus OS")
    parser.add_argument('--interface', '-i', type=str, default='can0', help='CAN interface')
    parser.add_argument('--deviceinstance', type=int, default=0, help='Device instance')
    parser.add_argument('--debug', '-d', action='store_true', help='Enable debug logging')
    parser.add_argument('--logfile', type=str, default=None, help='Log file')
    parser.add_argument('--productname', type=str, default='UBMS Battery', help='Product Name')
    parser.add_argument('--connection', type=str, default='CAN-bus', help='Connection string')
    parser.add_argument('--serial', type=str, default='UBMS001', help='Serial number')
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
    DBusGMainLoop(set_as_default=True)
    service = DbusUbmsService(
        battery,
        deviceinstance=args.deviceinstance,
        productname=args.productname,
        connection=args.connection,
        serial=args.serial
    )

    def handle_sigterm(*args):
        logging.info("SIGTERM received, exiting.")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)
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
