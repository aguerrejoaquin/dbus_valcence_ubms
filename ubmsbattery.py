#!/usr/bin/env python3

"""
Data acquisition and decoding of Valence U-BMS messages on CAN bus
To overcome the low resolution of the pack voltage(>=1V) the cell voltages are summed up.
In order for this to work the first x modules of a xSyP pack should have assigned module IDs 1 to x
The BMS should be operated in slave mode, VMU packages are being sent
"""

import logging
import can
import struct

class UbmsBattery(can.Listener):
    opModes = {0: "Standby", 1: "Charge", 2: "Drive"}
    guiModeKey = {252: 0, 3: 2}
    opState = {0: 14, 1: 9, 2: 9}  # Victron BMS states

    def __init__(self, voltage, capacity, connection, numberOfModules=16, numberOfStrings=4):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        self.numberOfModules = max(numberOfModules, 16)
        self.numberOfStrings = max(numberOfStrings, 4)
        self.modulesInSeries = int(self.numberOfModules / self.numberOfStrings)
        self.cellsPerModule = 4
        self.chargeComplete = 0
        self.soc = 0
        self.mode = 0
        self.state = ""
        self.voltage = 0
        self.current = 0
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

        self._ci = can.interface.Bus(
            channel=connection,
            bustype="socketcan",
            can_filters=[
                {"can_id": 0x0CF, "can_mask": 0xFF0},  # BMS status
                {"can_id": 0x180, "can_mask": 0xFFF},  # Firmware version and BMS type
            ],
        )

        if self._connect_and_verify(connection):
            self._set_operational_filters()
            msg = can.Message(
                arbitration_id=0x440, data=[0, 2, 0, 0], is_extended_id=False
            )  # default: drive mode
            self.cyclicModeTask = self._ci.send_periodic(msg, 1)
            notifier = can.Notifier(self._ci, [self])
        else:
            logging.error("Failed to connect to a supported Valence U-BMS")

    def _connect_and_verify(self, connection):
        found = 0
        msg = None
        while found != 7:
            try:
                msg = self._ci.recv(timeout=10)
            except can.CanError:
                logging.error("Canbus error")

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
                    logging.info(
                        "The number of modules communicating is less than configured."
                    )
                if msg.data[3] & 2 != 0:
                    logging.info(
                        "The number of modules communicating is higher than configured."
                    )
                found = found | 2

            elif msg.arbitration_id == 0xC1 and found & 1 == 0:
                if (
                    abs(2 * msg.data[0] - self.maxChargeVoltage)
                    > 0.15 * self.maxChargeVoltage
                ):
                    logging.error(
                        "Pack voltage of %dV differs significantly from configured max charge voltage %dV.",
                        msg.data[0],
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
            msg = can.Message(
                arbitration_id=0x440, data=[0, 2, 0, 0], is_extended_id=False
            )  # default: drive mode
            self.cyclicModeTask = self._ci.send_periodic(msg, 1)
            notifier = can.Notifier(self._ci, [self])
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
        self._ci.set_filters(filters)

    def on_message_received(self, msg):
        self.updated = msg.timestamp
        if msg.arbitration_id == 0xC0:
            self.soc = msg.data[0]
            self.mode = msg.data[1]
            self.state = self.opState[self.mode & 0x3]
            self.voltageAndCellTAlarms = msg.data[2]
            self.internalErrors = msg.data[3]
            self.currentAndPcbTAlarms = msg.data[4]
            self.numberOfModulesCommunicating = msg.data[5]
            if (msg.data[2] & 1 == 0) and (msg.data[3] & 2 == 0):
                self.numberOfModules = self.numberOfModulesCommunicating
            self.numberOfModulesBalancing = msg.data[6]
            if (self.shutdownReason == 0 and msg.data[7] != 0) or self.shutdownReason != msg.data[7]:
                logging.warning("Shutdown reason 0x%x", msg.data[7])
                logging.debug(
                    "SOC %d%% mode %d state %s alarms 0x%x 0x%x 0x%x",
                    self.soc, self.mode, self.state,
                    self.voltageAndCellTAlarms, self.internalErrors, self.currentAndPcbTAlarms,
                )
            self.shutdownReason = msg.data[7]

        elif msg.arbitration_id == 0xC1:
            self.current = struct.unpack("Bb", msg.data[0:2])[1]
            if (self.mode & 0x2) != 0:
                self.maxDischargeCurrent = int((struct.unpack("<h", msg.data[3:5])[0]) / 10)
                self.maxChargeCurrent = int((struct.unpack("<h", bytearray([msg.data[5], msg.data[7]]))[0]) / 10)
                logging.debug(
                    "Icmax %dA Idmax %dA", self.maxChargeCurrent, self.maxDischargeCurrent
                )
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
            logging.debug(
                "Umin %1.3fV Umax %1.3fV", self.minCellVoltage, self.maxCellVoltage
            )

        # Expand cell voltage and module voltage handling for any number of modules
        elif 0x350 <= msg.arbitration_id < 0x350 + 2 * self.numberOfModules:
            module = (msg.arbitration_id - 0x350) >> 1
            if (msg.arbitration_id & 1) == 0:
                # Even IDs: cell voltages
                # Defensive: pad with zeros if not enough data
                try:
                    self.cellVoltages[module] = struct.unpack(">4H", msg.data[:8])
                except Exception:
                    self.cellVoltages[module] = (0, 0, 0, 0)
            else:
                # Odd IDs: Not used in all firmwares, handle as needed
                pass
            self.moduleVoltage[module] = sum(self.cellVoltages[module])
            logging.debug("Umodule %d: %fmV", module, self.moduleVoltage[module])
            if module == self.numberOfModules - 1:
                self.voltage = sum(self.moduleVoltage[:self.modulesInSeries]) / 1000.0

        elif 0x46A <= msg.arbitration_id < 0x46A + self.numberOfModules:
            iStart = (msg.arbitration_id - 0x46A) * 3
            fmt = ">" + "h" * int((msg.dlc - 2) / 2)
            mCurrent = struct.unpack(fmt, msg.data[2 : msg.dlc])
            for idx, val in enumerate(mCurrent):
                if (iStart + idx) < len(self.moduleCurrent):
                    self.moduleCurrent[iStart + idx] = val

        elif 0x6A <= msg.arbitration_id < 0x6A + self.numberOfModules:
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
            for idx, m in enumerate(mSoc):
                if (iStart + idx) < len(self.moduleSoc):
                    self.moduleSoc[iStart + idx] = (m * 100) >> 8

        elif 0x76A <= msg.arbitration_id < 0x76A + self.numberOfModules:
            iStart = (msg.arbitration_id - 0x76A) * 3
            self.moduleTemp[iStart] = ((msg.data[2] * 256) + msg.data[3]) * 0.01
            if msg.dlc > 5 and (iStart + 1) < len(self.moduleTemp):
                self.moduleTemp[iStart + 1] = ((msg.data[4] * 256) + msg.data[5]) * 0.01
            if msg.dlc > 7 and (iStart + 2) < len(self.moduleTemp):
                self.moduleTemp[iStart + 2] = ((msg.data[6] * 256) + msg.data[7]) * 0.01

    def set_mode(self, mode):
        if not mode in [0, 1, 2]:
            logging.warning("Invalid mode requested %s " % str(mode))
            return False
        if not isinstance(self.cyclicModeTask, can.ModifiableCyclicTaskABC):
            msg = can.Message(arbitration_id=0x440, data=[0, mode, 0, 0], extended_id=False)
            self.cyclicModeTask.modify(msg)
        else:
            self.cyclicModeTask.stop()
            msg = can.Message(arbitration_id=0x440, data=[0, mode, 0, 0], extended_id=False)
            self.cyclicModeTask = self._ci.send_periodic(msg, 1)
        logging.info("Changed mode to %s" % self.opModes[mode])
        return True

# === All code below is to simply run it from the commandline for debugging purposes ===
def main():
    logging.basicConfig(format="%(levelname)-8s %(message)s", level=(logging.DEBUG))
    bat = UbmsBattery(capacity=650, voltage=29.0, connection="can0", numberOfModules=16, numberOfStrings=4)
    listeners = [bat]
    notifier = can.Notifier(bat._ci, listeners)

    # print out some info about the BMS
    logging.info("BMS type: %d", bat.bms_type)
    logging.info("Firmware version: %d", bat.firmwareVersion)
    logging.info("Hardware version: %d", bat.hw_rev)
    logging.info("Number of modules: %d", bat.numberOfModules)
    logging.info("Number of strings: %d", bat.numberOfStrings)
    logging.info("Max cell voltage: %1.3fV", bat.maxCellVoltage)
    logging.info("Min cell voltage: %1.3fV", bat.minCellVoltage)
    logging.info("Pack voltage: %1.3fV", bat.voltage)
    logging.info("Pack current: %dA", bat.current)
    logging.info("Pack SOC: %d%%", bat.soc)
    logging.info("Cell voltages and module SOCs:")
    for i in range(bat.numberOfModules):
        logging.info("Module %d: Cell Voltages: %s, Module Voltage: %s mV, Module SOC: %s%%",
            i, bat.cellVoltages[i], bat.moduleVoltage[i], bat.moduleSoc[i])

    notifier.stop()
    bat._ci.shutdown()

if __name__ == "__main__":
    main()
