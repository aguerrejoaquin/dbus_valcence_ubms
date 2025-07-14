#!/usr/bin/env python3

import logging
import can
import time
import struct
import argparse
import os
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# Venus OS D-Bus path format
SERVICE_NAME = 'com.victronenergy.battery.can'
OBJECT_PATH = '/com/victronenergy/battery/can'

class UbmsBattery(can.Listener):
    def __init__(self, voltage, capacity, connection, numberOfModules=16, numberOfStrings=4):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        self.numberOfModules = int(numberOfModules)
        self.numberOfStrings = int(numberOfStrings)
        self.modulesInSeries = int(self.numberOfModules / self.numberOfStrings)
        self.moduleSoc = [0 for _ in range(self.numberOfModules)]
        self.moduleVoltage = [0 for _ in range(self.numberOfModules)]
        self.cellVoltages = [[] for _ in range(self.numberOfModules)]
        self.moduleTemp = [0 for _ in range(self.numberOfModules)]
        self.voltage = 0.0
        self.current = 0.0
        self.soc = 0

        logging.info("Created a socket")
        try:
            self._ci = can.interface.Bus(
                channel=connection,
                bustype="socketcan"
            )
        except Exception as e:
            logging.error("Failed to initialize CAN interface: %s", str(e))
            raise

        if self._connect_and_verify(connection):
            logging.info("Handshake completed, switching to normal operation.")
            self.notifier = can.Notifier(self._ci, [self])
        else:
            logging.error("Failed to connect to a supported Valence U-BMS (continuing anyway for debug)")
            self.notifier = can.Notifier(self._ci, [self])

    def _connect_and_verify(self, connection):
        found = 0
        msg = None
        max_tries = 500
        tries = 0
        while found != 7 and tries < max_tries:
            tries += 1
            try:
                msg = self._ci.recv(timeout=2)
            except can.CanError as e:
                logging.error("Canbus error: %s", str(e))
                continue

            if msg is None:
                logging.error("No messages on canbus %s received. Check connection and speed setting.", connection)
                break

            logging.debug("Handshake: Received CAN frame 0x%X: %s", msg.arbitration_id, msg.data.hex())

            if msg.arbitration_id == 0xC0 and found & 2 == 0:
                logging.info("Found Valence U-BMS on %s in mode %x with %i modules communicating.",
                             connection, msg.data[1], msg.data[5])
                found = found | 2

            elif msg.arbitration_id == 0xC1 and found & 1 == 0:
                raw_voltage = msg.data[0]
                try:
                    raw_voltage_val = float(raw_voltage)
                except Exception:
                    raw_voltage_val = 0.0
                if abs(2 * raw_voltage_val - self.maxChargeVoltage) > 0.15 * self.maxChargeVoltage:
                    logging.error("Pack voltage of %dV differs significantly from configured max charge voltage %dV.",
                                  raw_voltage_val, self.maxChargeVoltage)
                found = found | 1

            elif msg.arbitration_id == 0x180 and found & 4 == 0:
                self.firmwareVersion = msg.data[0]
                self.bms_type = msg.data[3]
                self.hw_rev = msg.data[4]
                logging.info("U-BMS type %d with firmware version %d",
                             self.bms_type, self.firmwareVersion)

        if found != 7:
            logging.warning("Handshake not complete, but continuing for debug.")
            found = 7

        return found == 7

    def on_message(self, msg):
        self.on_message_received(msg)

    def on_message_received(self, msg):
        if msg.arbitration_id == 0xC0:
            self.soc = msg.data[0]

        elif msg.arbitration_id == 0xC1:
            try:
                self.current = struct.unpack("Bb", msg.data[0:2])[1]
            except Exception:
                pass

        if 0x350 <= msg.arbitration_id <= 0x36F:
            module = (msg.arbitration_id - 0x350) >> 1
            if 0 <= module < self.numberOfModules:
                if (msg.arbitration_id & 1) == 0:
                    try:
                        cell_vals = list(struct.unpack(">hhh", msg.data[2:8]))
                        self.cellVoltages[module] = cell_vals
                    except Exception:
                        pass
                else:
                    try:
                        if not self.cellVoltages[module] or len(self.cellVoltages[module]) != 3:
                            self.cellVoltages[module] = [0, 0, 0]
                        cell_val = struct.unpack(">h", msg.data[2:4])[0]
                        self.cellVoltages[module].append(cell_val)
                        self.moduleVoltage[module] = sum(self.cellVoltages[module])
                    except Exception:
                        pass

        if msg.arbitration_id in range(0x6A, 0x6A + ((self.numberOfModules + 6) // 7)):
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            try:
                mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
                for idx, val in enumerate(mSoc):
                    module_index = iStart + idx
                    if module_index < self.numberOfModules:
                        self.moduleSoc[module_index] = (val * 100) >> 8
            except Exception:
                pass

        if 0x76A <= msg.arbitration_id <= 0x76F:
            iStart = (msg.arbitration_id - 0x76A) * 3
            try:
                if (iStart) < len(self.moduleTemp) and msg.dlc > 3:
                    self.moduleTemp[iStart] = ((msg.data[2] * 256) + msg.data[3]) * 0.01
                if msg.dlc > 5 and (iStart + 1) < len(self.moduleTemp):
                    self.moduleTemp[iStart + 1] = ((msg.data[4] * 256) + msg.data[5]) * 0.01
                if msg.dlc > 7 and (iStart + 2) < len(self.moduleTemp):
                    self.moduleTemp[iStart + 2] = ((msg.data[6] * 256) + msg.data[7]) * 0.01
            except Exception:
                pass

class DbusService(dbus.service.Object):
    def __init__(self, bus, battery, service_name, object_path):
        super().__init__(bus, object_path)
        self.battery = battery
        self.service_name = service_name
        self.bus = bus
        self.last_update = 0
        self.properties = {
            'Soc': 0,
            'Current': 0,
            'Voltage': 0,
            'ModuleTemps': [0]*battery.numberOfModules,
            'ModuleVoltages': [0]*battery.numberOfModules,
            'ModuleSocs': [0]*battery.numberOfModules
        }
        # Register the service on the bus
        bus.request_name(service_name)

    @dbus.service.method(dbus.PROPERTIES_IFACE,
                        in_signature='ss', out_signature='v')
    def Get(self, interface_name, property_name):
        return self.properties.get(property_name, 0)

    @dbus.service.method(dbus.PROPERTIES_IFACE,
                        in_signature='ssv', out_signature='')
    def Set(self, interface_name, property_name, value):
        self.properties[property_name] = value

    @dbus.service.method(dbus.PROPERTIES_IFACE,
                        in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface_name):
        return self.properties

    def update(self):
        # Update D-Bus properties from battery object
        self.properties['Soc'] = self.battery.soc
        self.properties['Current'] = self.battery.current
        self.properties['Voltage'] = sum(self.battery.moduleVoltage)
        self.properties['ModuleTemps'] = self.battery.moduleTemp[:]
        self.properties['ModuleVoltages'] = self.battery.moduleVoltage[:]
        self.properties['ModuleSocs'] = self.battery.moduleSoc[:]

def main():
    parser = argparse.ArgumentParser(description="dbus_ubms.py (Venus OS service)")
    parser.add_argument("--capacity", "-c", type=int, default=650, help="Battery capacity, Ah")
    parser.add_argument("--voltage", "-v", type=float, default=29.0, help="Battery max charge voltage")
    parser.add_argument("--interface", "-i", type=str, default="can0", help="CAN device")
    parser.add_argument("--modules", type=int, default=16, help="Number of modules")
    parser.add_argument("--strings", type=int, default=4, help="Number of parallel strings")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    loglevel = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(format="%(levelname)-8s %(message)s", level=loglevel)

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    bat = UbmsBattery(
        capacity=args.capacity,
        voltage=args.voltage,
        connection=args.interface,
        numberOfModules=args.modules,
        numberOfStrings=args.strings,
    )

    # Service and object path
    service = DbusService(bus, bat, SERVICE_NAME, OBJECT_PATH)

    def periodic_update():
        service.update()
        return True  # Continue repeating

    # Call update every 2 seconds
    GLib.timeout_add_seconds(2, periodic_update)
    logging.info("Service running. Exporting D-Bus battery info every 2 seconds.")

    # Start main loop
    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        print("Exiting.")

if __name__ == "__main__":
    main()
