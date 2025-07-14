#!/usr/bin/env python3

"""
Data acquisition and decoding of Valence U-BMS messages on CAN bus
Adapted for 16 modules and 4 strings on can0 for Venus OS compatibility.
"""

import logging
import can
import struct

class UbmsBattery(can.Listener):
    opModes = {0: "Standby", 1: "Charge", 2: "Drive"}
    guiModeKey = {252: 0, 3: 2}
    opState = {0: 14, 1: 9, 2: 9}

    def __init__(self, voltage, capacity, connection):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        self.numberOfModules = 16
        self.numberOfStrings = 4
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
        self.cellVoltages = [(0, 0, 0, 0) for i in range(self.numberOfModules)]
        self.moduleVoltage = [0 for i in range(self.numberOfModules)]
        self.moduleCurrent = [0 for i in range(self.numberOfModules)]
        self.moduleSoc = [0 for i in range(self.numberOfModules)]
        self.moduleTemp = [0 for i in range(self.numberOfModules)]
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
                {"can_id": 0x0CF, "can_mask": 0xFF0},
                {"can_id": 0x180, "can_mask": 0xFFF},
            ],
        )

        if self._connect_and_verify(connection):
            pass

    def _connect_and_verify(self, connection):
        # Implement your handshake logic or just return True for now
        return True

    def on_message(self, msg):
        # Example CAN message handling logic.
        if 0x350 <= msg.arbitration_id <= (0x350 + self.numberOfModules * 2 - 1):
            module = (msg.arbitration_id - 0x350) // 2
            if (msg.arbitration_id & 1) == 0:
                # Even arbitration_id: cell voltages
                self.cellVoltages[module] = struct.unpack(">4H", msg.data)
                self.moduleVoltage[module] = sum(self.cellVoltages[module])
            else:
                # Odd arbitration_id: temperature and SOC
                self.moduleTemp[module], self.moduleSoc[module] = struct.unpack(">2H", msg.data[:4])
        elif msg.arbitration_id == 0xC0:
            self.soc = msg.data[0]
        elif msg.arbitration_id == 0xC1:
            self.current = struct.unpack(">h", msg.data[:2])[0]
        elif msg.arbitration_id == 0xC2:
            self.voltage = struct.unpack(">H", msg.data[:2])[0] / 100.0

    def get_total_voltage(self):
        # Prefer summed cell voltages for accuracy
        return sum([sum(cells) for cells in self.cellVoltages if cells])

    def get_soc(self):
        return self.soc

    def get_current(self):
        return self.current / 10.0

    def get_temperature(self):
        return max(self.moduleTemp) / 10.0 if self.moduleTemp else 25.0

    def get_firmware_version(self):
        return self.firmwareVersion

    def get_bms_type(self):
        return self.bms_type

    def get_hw_rev(self):
        return self.hw_rev
