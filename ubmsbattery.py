#!/usr/bin/env python3

import logging
import can
import struct
import argparse
import time

# === CAN comms lost timeout (seconds) ===
COMMS_TIMEOUT = 5

class UbmsBattery(can.Listener):
    opModes = {0: "Standby", 1: "Charge", 2: "Drive"}
    opState = {0: 14, 1: 9, 2: 9}

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
        self.cellVoltages = [[0, 0, 0, 0] for _ in range(self.numberOfModules)]
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
        self.updated = time.time()
        self.cyclicModeTask = None

        self._ci = can.interface.Bus(
            channel=connection,
            bustype="socketcan"
        )

        self.notifier = can.Notifier(self._ci, [self])

    def on_message_received(self, msg):
        print(f"CAN RX: {msg.arbitration_id:03X} {msg.data.hex()} (dlc={msg.dlc})")
        self.updated = time.time()

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
            if (self.mode & 0x2) != 0:
                self.maxDischargeCurrent = int((struct.unpack("<h", msg.data[3:5])[0]) / 10)
                if len(msg.data) >= 8:
                    self.maxChargeCurrent = int((struct.unpack("<h", bytearray([msg.data[5], msg.data[7]]))[0]) / 10)
                print(f"[PATCH] Updated maxChargeCurrent={self.maxChargeCurrent}, maxDischargeCurrent={self.maxDischargeCurrent} (drive mode)")
        elif msg.arbitration_id == 0xC2:
            if (self.mode & 0x1) != 0:
                self.chargeComplete = (msg.data[3] & 0x4) >> 2
                if (self.mode & 0x18) == 0x18:
                    self.maxChargeCurrent = msg.data[0]
                    print(f"[PATCH] Updated maxChargeCurrent={self.maxChargeCurrent} (equalizing)")
                else:
                    self.maxChargeCurrent = self.capacity * 0.1
                    print(f"[PATCH] Updated maxChargeCurrent={self.maxChargeCurrent} (charge mode, default 0.1C)")
        elif 0x350 <= msg.arbitration_id <= 0x35F:
            module = (msg.arbitration_id - 0x350) // 2
            if (msg.arbitration_id & 1) == 0 and len(msg.data) >= 8:
                # Even IDs: cells 1-3
                c1 = int.from_bytes(msg.data[2:4], byteorder='big')
                c2 = int.from_bytes(msg.data[4:6], byteorder='big')
                c3 = int.from_bytes(msg.data[6:8], byteorder='big')
                old_cells = self.cellVoltages[module]
                self.cellVoltages[module] = [c1, c2, c3, old_cells[3]]
            elif (msg.arbitration_id & 1) == 1 and len(msg.data) >= 4:
                # Odd IDs: cell 4
                c4 = int.from_bytes(msg.data[2:4], byteorder='big')
                old_cells = self.cellVoltages[module]
                self.cellVoltages[module] = [old_cells[0], old_cells[1], old_cells[2], c4]
            # Only update moduleVoltage if all cells are non-zero
            if all(self.cellVoltages[module]):
                self.moduleVoltage[module] = sum(self.cellVoltages[module])
            print(f"Updating module {module+1}: cells={self.cellVoltages[module]}, moduleVoltage={self.moduleVoltage[module]} mV")
        elif msg.arbitration_id == 0xC4:
            self.maxCellTemperature = msg.data[0] - 40
            self.minCellTemperature = msg.data[1] - 40
            self.maxPcbTemperature = msg.data[3] - 40
            self.maxCellVoltage = struct.unpack("<h", msg.data[4:6])[0] * 0.001
            self.minCellVoltage = struct.unpack("<h", msg.data[6:8])[0] * 0.001
        elif 0x6A <= msg.arbitration_id < 0x6A + (self.numberOfModules // 7 + 1):
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
            for idx, m in enumerate(mSoc):
                if (iStart + idx) < len(self.moduleSoc):
                    self.moduleSoc[iStart + idx] = (m * 100) >> 8

        print("----- Battery State Debug -----")
        print(f"State: {self.state} (mode={self.mode})")
        print(f"Pack SOC: {self.soc}%")
        print(f"Current: {self.current} A")
        print(f"Max Charge Voltage: {self.maxChargeVoltage} V")
        print(f"Max Charge Current: {self.maxChargeCurrent} A")
        print(f"Max Discharge Current: {self.maxDischargeCurrent} A")
        print(f"Number of modules: {self.numberOfModules}")
        print(f"Number of strings: {self.numberOfStrings}")
        print(f"Number of modules in series: {self.modulesInSeries}")
        print(f"Number of modules communicating: {self.numberOfModulesCommunicating}")
        print(f"Number of modules balancing: {self.numberOfModulesBalancing}")
        print(f"Shutdown reason: {self.shutdownReason}")
        print(f"Voltage and Cell Temp Alarms: {self.voltageAndCellTAlarms}")
        print(f"Current and PCB Temp Alarms: {self.currentAndPcbTAlarms}")
        print(f"Internal Errors: {self.internalErrors}")
        print(f"Balanced: {self.balanced}")
        print(f"Pack max cell voltage: {self.maxCellVoltage:.3f} V")
        print(f"Pack min cell voltage: {self.minCellVoltage:.3f} V")
        print(f"Pack max cell temperature: {self.maxCellTemperature}°C")
        print(f"Pack min cell temperature: {self.minCellTemperature}°C")
        print(f"Pack max PCB temperature: {self.maxPcbTemperature}°C")
        print("Per-module voltages (mV):", [f"{v}" for v in self.moduleVoltage])
        print("Per-module SOC (%):", [f"{v}" for v in self.moduleSoc])
        print("Per-module temps (unused):", [f"{v}" for v in self.moduleTemp])
        print("Cell voltages (V):")
        for idx, cells in enumerate(self.cellVoltages):
            print(f"  Module {idx+1:02}: " + " ".join(f"{v/1000:.3f}V" for v in cells))
        try:
            for s in range(self.numberOfStrings):
                start = s * self.modulesInSeries
                end = start + self.modulesInSeries
                string_voltage = sum(self.moduleVoltage[start:end]) / 1000.0
                print(f"String {s+1}: sum of modules {start+1}-{end}: {string_voltage:.3f} V")
            pack_voltage = self.get_pack_voltage()
            print(f"Pack voltage (average of all strings): {pack_voltage:.3f} V")
        except Exception as e:
            print(f"Pack voltage calculation error: {e}")
        print("-------------------------------")

    def get_pack_voltage(self):
        sum_strings = []
        for s in range(self.numberOfStrings):
            start = s * self.modulesInSeries
            end = start + self.modulesInSeries
            string_voltage = sum(self.moduleVoltage[start:end])
            sum_strings.append(string_voltage)
        avg_voltage = sum(sum_strings) / len(sum_strings) / 1000.0  # in V
        return avg_voltage

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=650)
    parser.add_argument("--voltage", type=float, default=29.0)
    parser.add_argument("--connection", type=str, default="can0")
    parser.add_argument("--modules", type=int, default=16)
    parser.add_argument("--strings", type=int, default=4)
    parser.add_argument("--duration", type=int, default=10, help="How long to listen (seconds)")
    parser.add_argument("--comms-timeout", type=int, default=COMMS_TIMEOUT, help="CAN comms lost timeout (seconds)")
    args = parser.parse_args()

    comms_timeout = args.comms_timeout

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
        start_time = time.time()
        warned = False
        while time.time() - start_time < args.duration:
            time.sleep(0.5)
            since = time.time() - bat.updated
            if since > comms_timeout and not warned:
                print(f"\n*** WARNING: CAN communication lost! No CAN data received in {since:.1f} seconds. ***\n")
                warned = True
            if since <= comms_timeout:
                warned = False
    except KeyboardInterrupt:
        pass

    print("\n------ DEBUG SUMMARY ------")
    logging.info("Number of modules: %d", bat.numberOfModules)
    logging.info("Number of strings: %d", bat.numberOfStrings)
    logging.info("Pack voltage (average of all strings): %.3f V", bat.get_pack_voltage())
    logging.info("Pack current: %dA", bat.current)
    logging.info("Pack SOC: %d%%", bat.soc)
    logging.info("Max cell voltage: %1.3fV", bat.maxCellVoltage)
    logging.info("Min cell voltage: %1.3fV", bat.minCellVoltage)
    logging.info("Max cell temperature: %d°C", bat.maxCellTemperature)
    logging.info("Min cell temperature: %d°C", bat.minCellTemperature)
    logging.info("Max PCB temperature: %d°C", bat.maxPcbTemperature)
    logging.info("Voltage and Cell Temp Alarms: %d", bat.voltageAndCellTAlarms)
    logging.info("Current and PCB Temp Alarms: %d", bat.currentAndPcbTAlarms)
    logging.info("Internal Errors: %d", bat.internalErrors)
    logging.info("Number of modules communicating: %d", bat.numberOfModulesCommunicating)
    logging.info("Number of modules balancing: %d", bat.numberOfModulesBalancing)
    logging.info("Shutdown reason: %d", bat.shutdownReason)
    logging.info("Balanced: %s", bat.balanced)
    logging.info("Max Charge Current (CCL): %sA", bat.maxChargeCurrent)
    logging.info("Max Discharge Current (DCL): %sA", bat.maxDischargeCurrent)
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
