#!/usr/bin/env python3

"""
Data acquisition and decoding of Valence U-BMS messages on CAN bus.
To overcome the low resolution of the pack voltage (>=1V), the cell voltages are summed up.
In order for this to work the first x modules of a xSyP pack should have assigned module IDs 1 to x.
The BMS should be operated in slave mode, VMU packages are being sent.
"""

import logging
import can
import struct

class UbmsBattery(can.Listener):
    opModes = {0: "Standby", 1: "Charge", 2: "Drive"}
    guiModeKey = {252: 0, 3: 2}
    opState = {0: 14, 1: 9, 2: 9}

    def __init__(self, voltage, capacity, connection, numberOfModules=8, numberOfStrings=2):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        self.numberOfModules = int(numberOfModules)
        self.numberOfStrings = int(numberOfStrings)
        self.modulesInSeries = int(self.numberOfModules / self.numberOfStrings)
        self.cellsPerModule = 4
        self.chargeComplete = 0
        self.soc = 0
        self.mode = 0
        self.state = ""
        self.voltage = 0.0
        self.current = 0.0
        self.temperature = 0
        self.balanced = True

        self.voltageAndCellTAlarms = 0
        self.internalErrors = 0
        self.currentAndPcbTAlarms = 0
        self.shutdownReason = 0

        self.maxPcbTemperature = 0
        self.maxCellTemperature = 0
        self.minCellTemperature = 0
        self.cellVoltages = [(0, 0, 0, 0) for _ in range(self.numberOfModules)]
        self.moduleVoltage = [0 for _ in range(self.numberOfModules)]
        self.moduleCurrent = [0 for _ in range(self.numberOfModules)]
        self.moduleSoc = [0 for _ in range(self.numberOfModules)]
        self.moduleTemp = [0 for _ in range(self.numberOfModules)]
        self.maxCellVoltage = 3.2
        self.minCellVoltage = 3.2
        self.maxChargeCurrent = 5.0
        self.maxDischargeCurrent = 5.0
        self.partnr = 0
        self.firmwareVersion = 0
        self.bms_type = 0
        self.hw_rev = 0
        self.numberOfModulesBalancing = 0
        self.numberOfModulesCommunicating = 0
        self.updated = -1
        self.cyclicModeTask = None

        try:
            self._ci = can.interface.Bus(
                channel=connection,
                bustype="socketcan",
                can_filters=[
                    {"can_id": 0x0CF, "can_mask": 0xFF0},
                    {"can_id": 0x180, "can_mask": 0xFFF},
                ],
            )
        except Exception as e:
            logging.error("Failed to initialize CAN interface: %s", str(e))
            raise

        if self._connect_and_verify(connection):
            self._set_operational_filters()
            msg = can.Message(arbitration_id=0x440, data=[0, 2, 0, 0], is_extended_id=False)
            try:
                self.cyclicModeTask = self._ci.send_periodic(msg, 1)
            except Exception as e:
                logging.error("Failed to start cyclic message: %s", str(e))
            try:
                self.notifier = can.Notifier(self._ci, [self])
            except Exception as e:
                logging.error("Failed to create CAN notifier: %s", str(e))
        else:
            logging.error("Failed to connect to a supported Valence U-BMS")

    def _connect_and_verify(self, connection):
        found = 0
        msg = None
        max_tries = 20
        tries = 0

        while found != 7 and tries < max_tries:
            tries += 1
            try:
                msg = self._ci.recv(timeout=10)
            except can.CanError as e:
                logging.error("Canbus error: %s", str(e))
                continue

            if msg is None:
                logging.error(
                    "No messages on canbus %s received. Check connection and speed setting.",
                    connection,
                )
                break

            elif msg.arbitration_id == 0xC0 and found & 2 == 0:
                logging.info(
                    "Found Valence U-BMS on %s in mode %x with %i modules communicating.",
                    connection,
                    msg.data[1],
                    msg.data[5],
                )
                if msg.data[2] & 1 != 0:
                    logging.info("The number of modules communicating is less than configured.")
                if msg.data[3] & 2 != 0:
                    logging.info("The number of modules communicating is higher than configured.")
                found = found | 2

            elif msg.arbitration_id == 0xC1 and found & 1 == 0:
                raw_voltage = msg.data[0]
                try:
                    raw_voltage_val = float(raw_voltage)
                except Exception:
                    raw_voltage_val = 0.0
                if (
                    abs(2 * raw_voltage_val - self.maxChargeVoltage)
                    > 0.15 * self.maxChargeVoltage
                ):
                    logging.error(
                        "Pack voltage of %dV differs significantly from configured max charge voltage %dV.",
                        raw_voltage_val,
                        self.maxChargeVoltage,
                    )
                found = found | 1

            elif msg.arbitration_id == 0x180 and found & 4 == 0:
                self.firmwareVersion = msg.data[0]
                self.bms_type = msg.data[3]
                self.hw_rev = msg.data[4]
                logging.info(
                    "U-BMS type %d with firmware version %d",
                    self.bms_type,
                    self.firmwareVersion,
                )
                found = found | 4

        if found == 7:
            msg = can.Message(arbitration_id=0x440, data=[0, 2, 0, 0], is_extended_id=False)
            try:
                self.cyclicModeTask = self._ci.send_periodic(msg, 1)
                self.notifier = can.Notifier(self._ci, [self])
            except Exception as e:
                logging.error("Failed to start cyclic message or notifier: %s", str(e))

        return found == 7

    def _set_operational_filters(self):
        filters = [
            {"can_id": 0x0CF, "can_mask": 0xFF0},
            {"can_id": 0x350, "can_mask": 0xFF0},
            {"can_id": 0x360, "can_mask": 0xFF0},
            {"can_id": 0x46A, "can_mask": 0xFF0},
            {"can_id": 0x06A, "can_mask": 0xFF0},
            {"can_id": 0x76A, "can_mask": 0xFF0},
        ]
        try:
            self._ci.set_filters(filters)
        except Exception as e:
            logging.warning("Could not set CAN filters: %s", str(e))

    def on_message(self, msg):
        self.on_message_received(msg)

    def on_message_received(self, msg):
        self.updated = msg.timestamp

        if msg.arbitration_id == 0xC0:
            self.soc = msg.data[0]
            self.mode = msg.data[1]
            self.state = self.opState.get(self.mode & 0x3, 14)
            self.voltageAndCellTAlarms = msg.data[2]
            self.internalErrors = msg.data[3]
            self.currentAndPcbTAlarms = msg.data[4]
            self.numberOfModulesCommunicating = msg.data[5]
            if (msg.data[2] & 1 == 0) and (msg.data[3] & 2 == 0):
                self.numberOfModules = self.numberOfModulesCommunicating
            self.numberOfModulesBalancing = msg.data[6]
            if (self.shutdownReason == 0 and msg.data[7] != 0) or self.shutdownReason != msg.data[7]:
                logging.warning("Shutdown reason 0x%x", msg.data[7])
                logging.debug("SOC %d%% mode %d state %s alarms 0x%x 0x%x 0x%x",
                    self.soc, self.mode, self.state,
                    self.voltageAndCellTAlarms, self.internalErrors, self.currentAndPcbTAlarms)
            self.shutdownReason = msg.data[7]

        elif msg.arbitration_id == 0xC1:
            self.current = struct.unpack("Bb", msg.data[0:2])[1]
            if (self.mode & 0x2) != 0:
                try:
                    self.maxDischargeCurrent = int(struct.unpack("<h", msg.data[3:5])[0] / 10)
                except Exception:
                    self.maxDischargeCurrent = 0
                try:
                    self.maxChargeCurrent = int(struct.unpack("<h", bytearray([msg.data[5], msg.data[7]]))[0] / 10)
                except Exception:
                    self.maxChargeCurrent = 0
                logging.debug("Icmax %dA Idmax %dA", self.maxChargeCurrent, self.maxDischargeCurrent)
            logging.debug("I: %dA U: %dV", self.current, msg.data[0])

        elif msg.arbitration_id == 0xC2:
            if (self.mode & 0x1) != 0:
                self.chargeComplete = (msg.data[3] & 0x4) >> 2
                self.maxChargeVoltage2 = struct.unpack("<h", msg.data[1:3])[0]
                if (self.mode & 0x18) == 0x18:
                    self.maxChargeCurrent = msg.data[0]
                else:
                    self.maxChargeCurrent = self.capacity * 0.1

        elif msg.arbitration_id == 0xC4:
            self.maxCellTemperature = msg.data[0] - 40
            self.minCellTemperature = msg.data[1] - 40
            self.maxPcbTemperature = msg.data[3] - 40
            self.maxCellVoltage = struct.unpack("<h", msg.data[4:6])[0] * 0.001
            self.minCellVoltage = struct.unpack("<h", msg.data[6:8])[0] * 0.001
            logging.debug("Umin %1.3fV Umax %1.3fV", self.minCellVoltage, self.maxCellVoltage)

        elif msg.arbitration_id in [
            0x350, 0x352, 0x354, 0x356, 0x358, 0x35A, 0x35C, 0x35E,
            0x360, 0x362, 0x364, 0x366, 0x368, 0x36A, 0x36C, 0x36E
        ]:
            module = (msg.arbitration_id - 0x350) >> 1
            if 0 <= module < self.numberOfModules:
                try:
                    self.cellVoltages[module] = struct.unpack(">hhh", msg.data[2 : msg.dlc])
                except Exception as e:
                    logging.warning("Error unpacking cell voltages for module %d: %s", module, str(e))
            else:
                logging.warning("Received CAN data for module %d, which is outside the configured range (0-%d)", module, self.numberOfModules-1)

        elif msg.arbitration_id in [
            0x351, 0x353, 0x355, 0x357, 0x359, 0x35B, 0x35D, 0x35F,
            0x361, 0x363, 0x365, 0x367, 0x369, 0x36B, 0x36D, 0x36F
        ]:
            module = (msg.arbitration_id - 0x351) >> 1
            if 0 <= module < self.numberOfModules:
                try:
                    self.cellVoltages[module] = self.cellVoltages[module] + tuple(struct.unpack(">h", msg.data[2 : msg.dlc]))
                    self.moduleVoltage[module] = sum(self.cellVoltages[module])
                    logging.debug("Umodule %d: %fmV", module, self.moduleVoltage[module])
                except Exception as e:
                    logging.warning("Error updating cell/module voltage for module %d: %s", module, str(e))
                if module == self.modulesInSeries - 1:
                    try:
                        relevant_modules = self.moduleVoltage[0 : self.modulesInSeries]
                        if all(isinstance(v, (int, float)) and v > 0 for v in relevant_modules):
                            self.voltage = sum(relevant_modules) / 1000.0
                            logging.debug("Pack voltage updated: %.3f V", self.voltage)
                        else:
                            logging.warning("Some module voltages missing or zero, cannot compute pack voltage.")
                    except Exception as e:
                        logging.error("Failed to compute pack voltage: %s", str(e))
            else:
                logging.warning("Received CAN data for module %d, which is outside the configured range (0-%d)", module, self.numberOfModules-1)

        elif msg.arbitration_id in [0x46A, 0x46B, 0x46C, 0x46D]:
            iStart = (msg.arbitration_id - 0x46A) * 3
            fmt = ">" + "h" * int((msg.dlc - 2) / 2)
            try:
                mCurrent = struct.unpack(fmt, msg.data[2 : msg.dlc])
                for idx, val in enumerate(mCurrent):
                    if (iStart + idx) < len(self.moduleCurrent):
                        self.moduleCurrent[iStart + idx] = val
            except Exception as e:
                logging.warning("Error unpacking module current: %s", str(e))

        elif msg.arbitration_id in [0x6A, 0x6B]:
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            try:
                mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
                for idx, val in enumerate(mSoc):
                    if (iStart + idx) < len(self.moduleSoc):
                        self.moduleSoc[iStart + idx] = (val * 100) >> 8
            except Exception as e:
                logging.warning("Error unpacking module SOC: %s", str(e))

        elif msg.arbitration_id in [0x76A, 0x76B, 0x76C, 0x76D]:
            iStart = (msg.arbitration_id - 0x76A) * 3
            try:
                if (iStart) < len(self.moduleTemp):
                    self.moduleTemp[iStart] = ((msg.data[2] * 256) + msg.data[3]) * 0.01
                if msg.dlc > 5 and (iStart + 1) < len(self.moduleTemp):
                    self.moduleTemp[iStart + 1] = ((msg.data[4] * 256) + msg.data[5]) * 0.01
                if msg.dlc > 7 and (iStart + 2) < len(self.moduleTemp):
                    self.moduleTemp[iStart + 2] = ((msg.data[6] * 256) + msg.data[7]) * 0.01
            except Exception as e:
                logging.warning("Error unpacking module temperature: %s", str(e))

    def set_mode(self, mode):
        if not mode in [0, 1, 2]:
            logging.warning("Invalid mode requested %s " % str(mode))
            return False

        if not hasattr(self, "cyclicModeTask") or self.cyclicModeTask is None:
            msg = can.Message(arbitration_id=0x440, data=[0, mode, 0, 0], is_extended_id=False)
            try:
                self.cyclicModeTask = self._ci.send_periodic(msg, 1)
            except Exception as e:
                logging.error("Failed to start cyclic mode task: %s", str(e))
                return False
        else:
            try:
                if hasattr(self.cyclicModeTask, "modify"):
                    msg = can.Message(arbitration_id=0x440, data=[0, mode, 0, 0], is_extended_id=False)
                    self.cyclicModeTask.modify(msg)
                else:
                    self.cyclicModeTask.stop()
                    msg = can.Message(arbitration_id=0x440, data=[0, mode, 0, 0], is_extended_id=False)
                    self.cyclicModeTask = self._ci.send_periodic(msg, 1)
            except Exception as e:
                logging.error("Failed to set cyclic mode: %s", str(e))
                return False

        logging.info("Changed mode to %s" % self.opModes[mode])
        return True

def debug_command_loop(bat):
    print("\nType a command to inspect the battery (commands: soc, volt, temp, all, help, exit):")
    while True:
        try:
            cmd = input("> ").strip().lower()
            if cmd == "soc":
                print("Module SOCs:", bat.moduleSoc)
            elif cmd == "volt":
                for i in range(bat.numberOfModules):
                    print(f"Module {i}: cell voltages: {bat.cellVoltages[i]}")
                print("Module voltages:", bat.moduleVoltage)
                print(f"Pack voltage: {bat.voltage:.3f} V")
            elif cmd == "temp":
                print("Module temperatures:", bat.moduleTemp)
                print(f"Max cell temp: {bat.maxCellTemperature}, Min cell temp: {bat.minCellTemperature}, Max PCB temp: {bat.maxPcbTemperature}")
            elif cmd == "all":
                print(f"BMS type: {bat.bms_type}, Firmware: {bat.firmwareVersion}, HW rev: {bat.hw_rev}")
                print(f"Number of modules: {bat.numberOfModules}")
                print("Module SOCs:", bat.moduleSoc)
                for i in range(bat.numberOfModules):
                    print(f"Module {i}: cell voltages: {bat.cellVoltages[i]}")
                print("Module voltages:", bat.moduleVoltage)
                print(f"Pack voltage: {bat.voltage:.3f} V")
                print("Module temperatures:", bat.moduleTemp)
                print(f"Max cell temp: {bat.maxCellTemperature}, Min cell temp: {bat.minCellTemperature}, Max PCB temp: {bat.maxPcbTemperature}")
            elif cmd == "help":
                print("Commands: soc, volt, temp, all, help, exit")
            elif cmd == "exit":
                print("Exiting debug loop.")
                break
            else:
                print("Unknown command. Type 'help' for options.")
        except (EOFError, KeyboardInterrupt):
            print("Exiting debug loop.")
            break

def main():
    import sys
    import argparse

    logging.basicConfig(format="%(levelname)-8s %(message)s", level=logging.DEBUG)

    parser = argparse.ArgumentParser(description="ubmsbattery.py debug")
    parser.add_argument("--capacity", type=int, default=650, help="Battery capacity, Ah")
    parser.add_argument("--voltage", type=float, default=29.0, help="Battery max charge voltage")
    parser.add_argument("--connection", type=str, default="can0", help="CAN device")
    parser.add_argument("--modules", type=int, default=8, help="Number of modules")
    parser.add_argument("--strings", type=int, default=2, help="Number of parallel strings")

    args = parser.parse_args()

    bat = UbmsBattery(
        capacity=args.capacity,
        voltage=args.voltage,
        connection=args.connection,
        numberOfModules=args.modules,
        numberOfStrings=args.strings,
    )
    notifier = can.Notifier(bat._ci, [bat])

    try:
        debug_command_loop(bat)
    finally:
        notifier.stop()
        bat._ci.shutdown()

if __name__ == "__main__":
    main()
