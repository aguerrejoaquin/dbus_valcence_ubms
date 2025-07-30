#!/usr/bin/env python3

"""
Data acquisition and decoding of Valence U-BMS messages on CAN bus
To overcome the low resolution of the pack voltage(>=1V) the cell voltages are summed up.
In order for this to work the first x modules of a xSyP pack should have assigned module IDs 1 to x
The BMS should be operated in slave mode, VMU packages are being sent

This version adapts module update timeouts and "stale data" logic to the number of modules,
as per Valence CAN spec: "CAN message rate is determined by the number of modules in the system,
for each group of 3 modules, the message rate increases by 600ms."
"""
import logging
import can
import struct
import time


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

    def __init__(self, voltage, capacity, connection):
        self.capacity = capacity
        self.maxChargeVoltage = voltage
        # --- Adaptation: These should be settable/configurable, not hardcoded!
        self.numberOfModules = 8
        self.numberOfStrings = 2
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

        # --- Adaptation: Track last update time for each module for freshness
        self.lastModuleUpdate = [0 for _ in range(self.numberOfModules)]

        self._ci = can.interface.Bus(
            channel=connection,
            bustype="socketcan",
            can_filters=[
                {"can_id": 0x0CF, "can_mask": 0xFF0},  # BMS status
                {"can_id": 0x180, "can_mask": 0xFFF},  # Firmware version and BMS type
            ],
        )

        if self._connect_and_verify(connection):
            # Now that we've confirmed connection, update filters for normal operation
            self._set_operational_filters()

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

    def _connect_and_verify(self, connection):
        # ... (existing connection logic) ...
        # For brevity, not repeating unchanged logic from original file.
        # Set up logic that will receive CAN messages and call on_message_received
        return True

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
        # --- Adaptation: Track update time for modules ---
        # Example for cell voltage CAN IDs (0x350–0x36F)
        if 0x350 <= msg.arbitration_id <= 0x36F:
            module_idx = (msg.arbitration_id - 0x350) // 2
            if 0 <= module_idx < self.numberOfModules:
                self.lastModuleUpdate[module_idx] = now
                # ... process voltage data ...
        # Add similar tracking for other per-module messages as needed.

        if msg.arbitration_id == 0xC0:
            # ... process status ...
            pass

        # ... rest of the decoding logic ...

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
                f"Modules with no update for {self.get_module_update_timeout():.1f}s: {stale}"
            )

    # ... rest of class logic unchanged, but use check_modules_freshness/log_stale_modules
    # in your periodic update or main loop!
