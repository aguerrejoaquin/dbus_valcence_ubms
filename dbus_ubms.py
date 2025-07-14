#!/usr/bin/env python3

import logging
import can
import time
import struct
import argparse

class UbmsBattery(can.Listener):
    def __init__(self, voltage, capacity, connection, numberOfModules=8, numberOfStrings=2):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        self.numberOfModules = int(numberOfModules)
        self.numberOfStrings = int(numberOfStrings)
        self.modulesInSeries = int(self.numberOfModules / self.numberOfStrings)
        self.moduleSoc = [0 for _ in range(self.numberOfModules)]
        self.moduleVoltage = [0 for _ in range(self.numberOfModules)]
        self.cellVoltages = [(0, 0, 0, 0) for _ in range(self.numberOfModules)]
        self.moduleTemp = [0 for _ in range(self.numberOfModules)]
        self.moduleCurrent = [0 for _ in range(self.numberOfModules)]
        self.voltage = 0.0
        self.current = 0.0
        self.soc = 0

        logging.info("Created a socket")
        try:
            self._ci = can.interface.Bus(
                channel=connection,
                bustype="socketcan"
                # No can_filters for debugging, accept all frames
            )
        except Exception as e:
            logging.error("Failed to initialize CAN interface: %s", str(e))
            raise

        # Try handshake
        if self._connect_and_verify(connection):
            logging.info("Handshake completed, switching to normal operation.")
            self.notifier = can.Notifier(self._ci, [self])
        else:
            logging.error("Failed to connect to a supported Valence U-BMS (continuing anyway for debug)")
            # Continue anyway for debugging
            self.notifier = can.Notifier(self._ci, [self])

    def _connect_and_verify(self, connection):
        found = 0
        msg = None
        max_tries = 500  # Increased for slow BMS
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
                found = found | 4

        # PATCH: If handshake failed, force found for debug
        if found != 7:
            logging.warning("Handshake not complete, but continuing for debug.")
            found = 7

        return found == 7

    def on_message(self, msg):
        self.on_message_received(msg)

    def on_message_received(self, msg):
        # Debug: Print every CAN frame
        logging.debug("CAN frame: 0x%X Data: %s", msg.arbitration_id, msg.data.hex())

        # Voltage and SOC
        if msg.arbitration_id == 0xC0:
            self.soc = msg.data[0]
            logging.debug("SOC updated: %d", self.soc)

        elif msg.arbitration_id == 0xC1:
            self.current = struct.unpack("Bb", msg.data[0:2])[1]
            logging.debug("Current updated: %d", self.current)

        # Module voltages: 0x350, 0x352, ... etc
        if 0x350 <= msg.arbitration_id <= 0x36F and (msg.arbitration_id & 1) == 0:
            module = (msg.arbitration_id - 0x350) >> 1
            if 0 <= module < self.numberOfModules:
                try:
                    self.cellVoltages[module] = struct.unpack(">hhh", msg.data[2 : msg.dlc])
                    self.moduleVoltage[module] = sum(self.cellVoltages[module])
                    logging.debug("Module %d voltages: %s, module voltage: %d", module, self.cellVoltages[module], self.moduleVoltage[module])
                except Exception as e:
                    logging.warning("Error unpacking cell voltages for module %d: %s", module, str(e))

        # Module SOC: 0x6A, 0x6B, 0x6C (for up to 16 modules)
        if msg.arbitration_id in range(0x6A, 0x6A + ((self.numberOfModules + 6) // 7)):
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            try:
                mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
                for idx, val in enumerate(mSoc):
                    module_index = iStart + idx
                    if module_index < self.numberOfModules:
                        self.moduleSoc[module_index] = (val * 100) >> 8
                        logging.debug("Module %d SOC: %d", module_index, self.moduleSoc[module_index])
            except Exception as e:
                logging.warning("Error unpacking module SOC: %s", str(e))

        # Module temperatures: (example IDs, adjust as needed)
        if 0x76A <= msg.arbitration_id <= 0x76D:
            iStart = (msg.arbitration_id - 0x76A) * 3
            try:
                if (iStart) < len(self.moduleTemp):
                    self.moduleTemp[iStart] = ((msg.data[2] * 256) + msg.data[3]) * 0.01
                if msg.dlc > 5 and (iStart + 1) < len(self.moduleTemp):
                    self.moduleTemp[iStart + 1] = ((msg.data[4] * 256) + msg.data[5]) * 0.01
                if msg.dlc > 7 and (iStart + 2) < len(self.moduleTemp):
                    self.moduleTemp[iStart + 2] = ((msg.data[6] * 256) + msg.data[7]) * 0.01
                logging.debug("Module temps (starting at %d): %s", iStart, self.moduleTemp[iStart:iStart+3])
            except Exception as e:
                logging.warning("Error unpacking module temperature: %s", str(e))

def main():
    parser = argparse.ArgumentParser(description="dbus_ubms.py debug")
    parser.add_argument("--capacity", "-c", type=int, default=650, help="Battery capacity, Ah")
    parser.add_argument("--voltage", "-v", type=float, default=29.0, help="Battery max charge voltage")
    parser.add_argument("--interface", "-i", type=str, default="can0", help="CAN device")
    parser.add_argument("--modules", type=int, default=8, help="Number of modules")
    parser.add_argument("--strings", type=int, default=2, help="Number of parallel strings")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    loglevel = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(format="%(levelname)-8s %(message)s", level=loglevel)

    bat = UbmsBattery(
        capacity=args.capacity,
        voltage=args.voltage,
        connection=args.interface,
        numberOfModules=args.modules,
        numberOfStrings=args.strings,
    )

    # Simple debug loop to display battery state
    try:
        while True:
            print("\n---- U-BMS State ----")
            print(f"SOC: {bat.soc}")
            print(f"Current: {bat.current}")
            print(f"Module Voltages: {bat.moduleVoltage}")
            print(f"Module SOCs: {bat.moduleSoc}")
            print(f"Module Temps: {bat.moduleTemp}")
            time.sleep(5)
    except KeyboardInterrupt:
        print("Exiting.")

if __name__ == "__main__":
    main()
