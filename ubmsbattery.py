#!/usr/bin/env python3

"""
Data acquisition and decoding of Valence U-BMS messages on CAN bus
To overcome the low resolution of the pack voltage(>=1V) the cell voltages are summed up.
In order for this to work the first x modules of a xSyP pack should have assigned module IDs 1 to x
The BMS should be operated in slave mode, VMU packages are being sent.

This version restores CLI debug mode (as in version 43) and adapts module update timeouts
and "stale data" logic to the number of modules, according to the Valence spec:
"CAN message rate is determined by the number of modules in the system,
for each group of 3 modules, the message rate increases by 600ms."
"""

import logging
import can
import struct
import time
import sys

class UbmsBattery(can.Listener):
    opModes = {0: "Standby", 1: "Charge", 2: "Drive"}
    guiModeKey = {252: 0, 3: 2}
    opState = {0: 14, 1: 9, 2: 9}
    # Victron BMS states
    # 0-8 init
    #   9 running
    #  10 error
    #  12 shutdown
    #  13 updating
    #  14 standby
    #  15 going to run
    #  16 pre-charge
    #  17 contactor check

    def __init__(self, voltage, capacity, connection, numberOfModules=8, numberOfStrings=2, cellsPerModule=4):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        self.numberOfModules = numberOfModules
        self.numberOfStrings = numberOfStrings
        self.modulesInSeries = int(self.numberOfModules / self.numberOfStrings)
        self.cellsPerModule = cellsPerModule
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
        self.cellVoltages = [[0 for _ in range(self.cellsPerModule)] for _ in range(self.numberOfModules)]
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

        # Track last update time for each module for freshness
        self.lastModuleUpdate = [0 for _ in range(self.numberOfModules)]

        try:
            self._ci = can.interface.Bus(
                channel=connection,
                bustype="socketcan",
                can_filters=[
                    {"can_id": 0x0CF, "can_mask": 0xFF0},  # BMS status
                    {"can_id": 0x180, "can_mask": 0xFFF},  # Firmware version and BMS type
                ],
            )
        except Exception as e:
            logging.error(f"CAN interface error: {e}")
            sys.exit(1)

        self.running = False

    def get_module_update_timeout(self):
        """
        Calculate expected module update interval based on Valence spec.
        Return timeout in seconds, with 2x margin.
        """
        base_interval = 0.6  # 600ms
        groups_of_3 = max(1, self.numberOfModules / 3)
        expected_interval = groups_of_3 * base_interval
        # Use double the interval as a timeout margin
        return expected_interval * 2

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
        now = time.time()
        self.updated = msg.timestamp

        # Track update time for modules
        # Example for cell voltage CAN IDs (0x350–0x36F)
        if 0x350 <= msg.arbitration_id <= 0x36F:
            module_idx = (msg.arbitration_id - 0x350) // 2
            if 0 <= module_idx < self.numberOfModules:
                self.lastModuleUpdate[module_idx] = now
                # Parse cell voltages (example for 4 cells per module)
                if len(msg.data) >= 8:
                    for i in range(self.cellsPerModule):
                        msb = msg.data[2*i]
                        lsb = msg.data[2*i+1]
                        voltage = ((msb << 8) | lsb) / 1000.0  # mV to V
                        self.cellVoltages[module_idx][i] = voltage
                    self.moduleVoltage[module_idx] = sum(self.cellVoltages[module_idx])
                    logging.debug(f"Module {module_idx+1} voltages: {self.cellVoltages[module_idx]} -> {self.moduleVoltage[module_idx]:.3f} V")
        # Add similar tracking for other per-module messages as needed.

        # Example: Module current, SOC, temp, etc., can be parsed similarly based on spec.

    def check_modules_freshness(self):
        """
        Check all modules for freshness based on dynamic timeout.
        Returns a list of module indices that are stale.
        """
        now = time.time()
        timeout = self.get_module_update_timeout()
        stale = []
        for i, last in enumerate(self.lastModuleUpdate):
            # Allow modules that have never reported yet
            if last == 0:
                continue
            if now - last > timeout:
                stale.append(i)
        return stale

    def log_stale_modules(self):
        """
        For diagnostics. Call periodically. Logs any stale modules.
        """
        stale = self.check_modules_freshness()
        if stale:
            logging.warning(
                f"Modules with no update for {self.get_module_update_timeout():.1f}s: {[i+1 for i in stale]}"
            )

    def debug_summary(self):
        """
        Print a summary of current battery & module state for debug.
        """
        print("--- DEBUG SUMMARY ---")
        print(f"Pack voltage: {self.voltage:.2f} V")
        print(f"Module voltages: {['{:.3f}'.format(v) for v in self.moduleVoltage]}")
        print(f"Module SOCs: {self.moduleSoc}")
        print(f"Last updates: {['{:.1f}s'.format(time.time()-t) if t > 0 else 'never' for t in self.lastModuleUpdate]}")
        stale = self.check_modules_freshness()
        if stale:
            print(f"STALE/NO DATA modules: {[i+1 for i in stale]}")
        else:
            print("All modules reporting in expected interval.")

    def run(self, duration=30, debug_interval=2):
        """
        Main debug loop for CLI use.
        """
        self.running = True
        print(f"Debug running for {duration}s with {self.numberOfModules} modules ({self.numberOfStrings} strings, {self.cellsPerModule} cells/module).")
        self._set_operational_filters()
        start = time.time()
        next_debug = start
        while time.time() - start < duration:
            try:
                msg = self._ci.recv(timeout=0.1)
                if msg:
                    self.on_message_received(msg)
            except KeyboardInterrupt:
                print("Exiting debug loop.")
                break
            except Exception as e:
                logging.error(f"CAN receive error: {e}")
            now = time.time()
            if now >= next_debug:
                self.debug_summary()
                next_debug = now + debug_interval
        print("Debug finished.")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Valence U-BMS CAN debug tool")
    parser.add_argument("--modules", type=int, default=8, help="Number of modules")
    parser.add_argument("--strings", type=int, default=2, help="Number of parallel strings")
    parser.add_argument("--cells", type=int, default=4, help="Cells per module")
    parser.add_argument("--capacity", type=int, default=100, help="Battery capacity (Ah)")
    parser.add_argument("--voltage", type=float, default=52.0, help="Battery max charge voltage (V)")
    parser.add_argument("--connection", type=str, default="can0", help="SocketCAN interface")
    parser.add_argument("--duration", type=int, default=30, help="Debug run duration (s)")
    parser.add_argument("--loglevel", type=str, default="INFO", help="Log level (DEBUG,INFO,WARNING,ERROR)")

    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.loglevel.upper()), format="%(asctime)s %(levelname)s %(message)s")

    bat = UbmsBattery(
        voltage=args.voltage,
        capacity=args.capacity,
        connection=args.connection,
        numberOfModules=args.modules,
        numberOfStrings=args.strings,
        cellsPerModule=args.cells,
    )

    bat.run(duration=args.duration)
