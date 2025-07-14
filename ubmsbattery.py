#!/usr/bin/env python3

"""
Data acquisition and decoding of Valence U-BMS messages on CAN bus for debugging.
- Accepts any number of modules and strings (set via command line or defaults to 16/4).
- Receives all CAN frames (no filters), prints every CAN message.
- Decodes cell voltages, module voltages, module SOCs.
- Prints pack voltage as the sum of modules in one string (e.g., for 16 modules and 4 strings, sum modules 0,1,2,3).
- At the end, prints all module voltages, SOCs, and cell voltages.
"""

import logging
import can
import struct
import argparse
import time

class UbmsBattery(can.Listener):
    opModes = {0: "Standby", 1: "Charge", 2: "Drive"}
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
            bustype="socketcan"
            # No filters for debug, receive all frames
        )

        # Don't require handshake for debugging
        self.notifier = can.Notifier(self._ci, [self])

    def on_message_received(self, msg):
        print(f"CAN RX: {msg.arbitration_id:03X} {msg.data.hex()} (dlc={msg.dlc})")
        self.updated = getattr(msg, "timestamp", 0)
        # --- Try to extract data if IDs match known Valence protocol ---
        if msg.arbitration_id == 0xC0:
            self.soc = msg.data[0]
            self.mode = msg.data[1]
            self.state = self.opState.get(self.mode & 0x3, "unknown")
            self.voltageAndCellTAlarms = msg.data[2]
            self.internalErrors = msg.data[3]
            self.currentAndPcbTAlarms = msg.data[4]
            self.numberOfModulesCommunicating = msg.data[5]
            self.numberOfModulesBalancing = msg.data[6]
            self.shutdownReason = msg.data[7]
        elif msg.arbitration_id == 0xC1:
            self.current = struct.unpack("Bb", msg.data[0:2])[1]
        elif msg.arbitration_id == 0xC4:
            self.maxCellTemperature = msg.data[0] - 40
            self.minCellTemperature = msg.data[1] - 40
            self.maxPcbTemperature = msg.data[3] - 40
            self.maxCellVoltage = struct.unpack("<h", msg.data[4:6])[0] * 0.001
            self.minCellVoltage = struct.unpack("<h", msg.data[6:8])[0] * 0.001
        elif 0x350 <= msg.arbitration_id < 0x350 + self.numberOfModules * 2:
            module = (msg.arbitration_id - 0x350) >> 1
            # Fix: skip first byte (module id), then read 4x2 bytes for cell voltages
            if module < self.numberOfModules and (msg.arbitration_id & 1) == 0 and len(msg.data) >= 9:
                try:
                    self.cellVoltages[module] = struct.unpack(">4H", msg.data[1:9])
                except Exception:
                    self.cellVoltages[module] = (0, 0, 0, 0)
                self.moduleVoltage[module] = sum(self.cellVoltages[module])
        elif 0x6A <= msg.arbitration_id < 0x6A + (self.numberOfModules // 7 + 1):
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
            for idx, m in enumerate(mSoc):
                if (iStart + idx) < len(self.moduleSoc):
                    self.moduleSoc[iStart + idx] = (m * 100) >> 8

    def get_pack_voltage(self):
        # Sum only the modules in one string (modules 0,1,2,3 for 16 modules/4 strings)
        pack_voltage = sum(self.moduleVoltage[:self.modulesInSeries]) / 1000.0
        return pack_voltage

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=650)
    parser.add_argument("--voltage", type=float, default=29.0)
    parser.add_argument("--connection", type=str, default="can0")
    parser.add_argument("--modules", type=int, default=16)
    parser.add_argument("--strings", type=int, default=4)
    parser.add_argument("--duration", type=int, default=10, help="How long to listen (seconds)")
    args = parser.parse_args()

    logging.basicConfig(format="%(levelname)-8s %(message)s", level=(logging.DEBUG))
    bat = UbmsBattery(
        capacity=args.capacity,
        voltage=args.voltage,
        connection=args.connection,
        numberOfModules=args.modules,
        numberOfStrings=args.strings
    )
    listeners = [bat]
    notifier = can.Notifier(bat._ci, listeners)

    print("Listening for CAN messages...")
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        pass

    print("\n------ DEBUG SUMMARY ------")
    logging.info("Number of modules: %d", bat.numberOfModules)
    logging.info("Number of strings: %d", bat.numberOfStrings)
    logging.info("Pack voltage (sum of modules 0-%d): %.3f V", bat.modulesInSeries-1, bat.get_pack_voltage())
    logging.info("Pack current: %dA", bat.current)
    logging.info("Pack SOC: %d%%", bat.soc)
    logging.info("Max cell voltage: %1.3fV", bat.maxCellVoltage)
    logging.info("Min cell voltage: %1.3fV", bat.minCellVoltage)
    logging.info("Cell voltages and module SOCs:")
    for i in range(bat.numberOfModules):
        logging.info(
            "Module %d: Cell Voltages: %s, Module Voltage: %s mV, Module SOC: %s%%",
            i, bat.cellVoltages[i], bat.moduleVoltage[i], bat.moduleSoc[i]
        )

    notifier.stop()
    bat._ci.shutdown()

if __name__ == "__main__":
    main()
