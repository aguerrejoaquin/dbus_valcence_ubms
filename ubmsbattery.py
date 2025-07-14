#!/usr/bin/env python3

import logging
import can
import struct
import argparse
import time

class UbmsBattery(can.Listener):
    opModes = {0: "Standby", 1: "Charge", 2: "Drive"}
    opState = {0: 14, 1: 9, 2: 9}

    def __init__(self, voltage, capacity, connection, numberOfModules=8, numberOfStrings=2):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        self.numberOfModules = max(numberOfModules, 8)
        self.numberOfStrings = max(numberOfStrings, 2)
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
        )

        self.notifier = can.Notifier(self._ci, [self])

    def on_message_received(self, msg):
        print(f"[DEBUG] CAN RX: ID=0x{msg.arbitration_id:03X} Data={msg.data.hex()} len={msg.dlc}")
        self.updated = getattr(msg, "timestamp", 0)

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
            print(f"[DEBUG] Updated SOC: {self.soc}, Mode: {self.mode}, State: {self.state}")
        elif msg.arbitration_id == 0xC1:
            self.current = struct.unpack("Bb", msg.data[0:2])[1]
            print(f"[DEBUG] Updated Current: {self.current}A")
        elif msg.arbitration_id == 0xC4:
            self.maxCellTemperature = msg.data[0] - 40
            self.minCellTemperature = msg.data[1] - 40
            self.maxPcbTemperature = msg.data[3] - 40
            self.maxCellVoltage = struct.unpack("<h", msg.data[4:6])[0] * 0.001
            self.minCellVoltage = struct.unpack("<h", msg.data[6:8])[0] * 0.001
            print(f"[DEBUG] Updated Temps: MaxCell {self.maxCellTemperature}, MinCell {self.minCellTemperature}, MaxPCB {self.maxPcbTemperature}")
            print(f"[DEBUG] Updated Cell Voltages: Max {self.maxCellVoltage:.3f}V, Min {self.minCellVoltage:.3f}V")
        elif 0x350 <= msg.arbitration_id < 0x350 + self.numberOfModules * 2:
            module = (msg.arbitration_id - 0x350) >> 1
            if module < self.numberOfModules:
                if (msg.arbitration_id & 1) == 0 and len(msg.data) >= 7:
                    c1, c2, c3 = struct.unpack("<3H", msg.data[1:7])
                    c4 = self.cellVoltages[module][3] if self.cellVoltages[module] else 0
                    self.cellVoltages[module] = (c1, c2, c3, c4)
                    self.moduleVoltage[module] = c1 + c2 + c3 + c4
                    print(f"[DEBUG] Module {module} EVEN Cells: {self.cellVoltages[module]}")
                elif (msg.arbitration_id & 1) == 1 and len(msg.data) >= 4:
                    c4 = (msg.data[2] << 8) | msg.data[1]
                    c1, c2, c3 = self.cellVoltages[module][:3]
                    self.cellVoltages[module] = (c1, c2, c3, c4)
                    self.moduleVoltage[module] = c1 + c2 + c3 + c4
                    print(f"[DEBUG] Module {module} ODD Cells: {self.cellVoltages[module]}")
        elif 0x6A <= msg.arbitration_id < 0x6A + (self.numberOfModules // 7 + 1):
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
            for idx, m in enumerate(mSoc):
                if (iStart + idx) < len(self.moduleSoc):
                    self.moduleSoc[iStart + idx] = (m * 100) >> 8
            print(f"[DEBUG] Module SOCs: {self.moduleSoc}")

    def get_pack_voltage(self):
        pack_voltage = sum(self.moduleVoltage[:self.modulesInSeries]) / 1000.0
        return pack_voltage

    def get_min_max_cell_voltage(self):
        min_v = 9999
        max_v = 0
        min_id = max_id = (-1, -1)
        for m, cells in enumerate(self.cellVoltages):
            for c, v in enumerate(cells):
                if v == 0:
                    continue
                if v < min_v:
                    min_v = v
                    min_id = (m, c)
                if v > max_v:
                    max_v = v
                    max_id = (m, c)
        print(f"[DEBUG] Min cell voltage: {min_v} mV at {min_id}, Max cell voltage: {max_v} mV at {max_id}")
        return min_v, min_id, max_v, max_id

    def get_min_max_cell_temp(self):
        print(f"[DEBUG] Min cell temp: {self.minCellTemperature}, Max cell temp: {self.maxCellTemperature}")
        return self.minCellTemperature, 0, self.maxCellTemperature, 0

    def close(self):
        self.notifier.stop()
        self._ci.shutdown()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=260)
    parser.add_argument("--voltage", type=float, default=29.0)
    parser.add_argument("--connection", type=str, default="can0")
    parser.add_argument("--modules", type=int, default=8)
    parser.add_argument("--strings", type=int, default=2)
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
    print("Listening for CAN messages...")
    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        pass

    print("\n------ DEBUG SUMMARY ------")
    logging.info("Number of modules: %d", bat.numberOfModules)
    logging.info("Number of strings: %d", bat.numberOfStrings)
    logging.info("Pack voltage: %.3f V", bat.get_pack_voltage())
    logging.info("Pack current: %dA", bat.current)
    logging.info("Pack SOC: %d%%", bat.soc)
    logging.info("Max cell voltage: %1.3fV", bat.maxCellVoltage)
    logging.info("Min cell voltage: %1.3fV", bat.minCellVoltage)
    logging.info("Max cell temperature: %d°C", bat.maxCellTemperature)
    logging.info("Min cell temperature: %d°C", bat.minCellTemperature)
    for i in range(bat.numberOfModules):
        logging.info(
            "Module %d: Cell Voltages: %s, Module Voltage: %s mV, Module SOC: %s%%",
            i, bat.cellVoltages[i], bat.moduleVoltage[i], bat.moduleSoc[i]
        )

    bat.get_min_max_cell_voltage()
    bat.get_min_max_cell_temp()
    bat.close()
