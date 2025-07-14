#!/usr/bin/env python3

import logging
import can
import struct
import argparse
import time

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
        # cellVoltages: list of lists [module][cell] in mV
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
        self.updated = -1
        self.cyclicModeTask = None

        self._ci = can.interface.Bus(
            channel=connection,
            bustype="socketcan"
        )

        self.notifier = can.Notifier(self._ci, [self])

    def on_message_received(self, msg):
        print(f"CAN RX: {msg.arbitration_id:03X} {msg.data.hex()} (dlc={msg.dlc})")
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
        elif msg.arbitration_id == 0xC1:
            self.current = struct.unpack("Bb", msg.data[0:2])[1]
        elif msg.arbitration_id == 0xC4:
            # Pack-level temperatures (degrees C)
            self.maxCellTemperature = msg.data[0] - 40
            self.minCellTemperature = msg.data[1] - 40
            self.maxPcbTemperature = msg.data[3] - 40
            self.maxCellVoltage = struct.unpack("<h", msg.data[4:6])[0] * 0.001
            self.minCellVoltage = struct.unpack("<h", msg.data[6:8])[0] * 0.001
        elif 0x350 <= msg.arbitration_id < 0x350 + self.numberOfModules * 2:
            module = (msg.arbitration_id - 0x350) >> 1
            print(f"0x{msg.arbitration_id:X} module={module} data={msg.data.hex()} len={len(msg.data)}")
            if module < self.numberOfModules:
                if (msg.arbitration_id & 1) == 0 and len(msg.data) >= 7:
                    # Even: cells 1-3
                    c1, c2, c3 = struct.unpack("<3H", msg.data[1:7])
                    c4 = self.cellVoltages[module][3] if self.cellVoltages[module] else 0
                    self.cellVoltages[module] = [c1, c2, c3, c4]
                    self.moduleVoltage[module] = c1 + c2 + c3 + c4
                elif (msg.arbitration_id & 1) == 1 and len(msg.data) >= 4:
                    # Odd: cell 4 only
                    c4 = (msg.data[2] << 8) | msg.data[1]
                    c1, c2, c3 = self.cellVoltages[module][:3]
                    self.cellVoltages[module] = [c1, c2, c3, c4]
                    self.moduleVoltage[module] = c1 + c2 + c3 + c4

                # Debug print each time a module is updated
                print(f"Updating module {module+1}: cells={self.cellVoltages[module]}, moduleVoltage={self.moduleVoltage[module]} mV")

        elif 0x6A <= msg.arbitration_id < 0x6A + (self.numberOfModules // 7 + 1):
            iStart = (msg.arbitration_id - 0x6A) * 7
            fmt = "B" * (msg.dlc - 1)
            mSoc = struct.unpack(fmt, msg.data[1 : msg.dlc])
            for idx, m in enumerate(mSoc):
                if (iStart + idx) < len(self.moduleSoc):
                    self.moduleSoc[iStart + idx] = (m * 100) >> 8

        # --- Debug: Print all available BMS data after every CAN message ---
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
            pack_voltage = self.get_pack_voltage()
            print(f"Pack voltage (sum of modules 0-{self.modulesInSeries-1}): {pack_voltage:.3f} V")
        except Exception as e:
            print(f"Pack voltage calculation error: {e}")
        print("-------------------------------")

    def get_pack_voltage(self):
        # sum only the modules in series, not all in array (for parallel configs)
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
