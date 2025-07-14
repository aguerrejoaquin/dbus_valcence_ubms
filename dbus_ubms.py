# ... (rest of the imports and class definitions remain unchanged)

    def _update(self):
        # --- Read battery values ---
        v = self.battery.get_pack_voltage()
        c = self.battery.current
        soc = self.battery.soc

        min_v = min([min(cells) for cells in self.battery.cellVoltages if any(cells)]) if self.battery.cellVoltages else 0
        max_v = max([max(cells) for cells in self.battery.cellVoltages if any(cells)]) if self.battery.cellVoltages else 0
        min_id = max_id = (0, 0)
        for m, cells in enumerate(self.battery.cellVoltages):
            for cidx, val in enumerate(cells):
                if val == min_v:
                    min_id = (m, cidx)
                if val == max_v:
                    max_id = (m, cidx)
        min_t = self.battery.minCellTemperature
        max_t = self.battery.maxCellTemperature
        min_tid = max_tid = 0

        alarms = {
            '/System/Alarms/LowCellVoltage':   int(min_v < self.thresholds['min_cell_voltage']),
            '/System/Alarms/HighCellVoltage':  int(max_v > self.thresholds['max_cell_voltage']),
            '/System/Alarms/CellImbalance':    int((max_v - min_v) > self.thresholds['cell_imbalance']),
            '/System/Alarms/LowSoc':           int(soc < self.thresholds['min_soc']),
            '/System/Alarms/HighChargeCurrent':int(c > self.thresholds['max_charge_current']),
            '/System/Alarms/HighDischargeCurrent': int(abs(c) > self.thresholds['max_discharge_current']),
            '/System/Alarms/CellTemperature':  int((max_t > self.thresholds['max_cell_temp']) or (min_t < self.thresholds['min_cell_temp'])),
        }

        alarm_state = any(bool(a) for a in alarms.values())
        if alarm_state != self.last_alarm_state:
            self.set_relay(alarm_state)
            self.last_alarm_state = alarm_state

        # --- Debug output ---
        log.info(f"D-Bus Update: Voltage={v:.2f}V, Current={c}A, SOC={soc}%")
        log.info(f"MinCellV={min_v}mV (Module,Cell={min_id}), MaxCellV={max_v}mV (Module,Cell={max_id})")
        log.info(f"MinCellT={min_t}C, MaxCellT={max_t}C")
        for alarm_path, alarm_val in alarms.items():
            log.info(f"Alarm {alarm_path}: {'ON' if alarm_val else 'OFF'}")

        # --- Publish to D-Bus ---
        self.set_dbus_value('/Dc/0/Voltage', v)
        self.set_dbus_value('/Dc/0/Current', c)
        self.set_dbus_value('/Soc', soc)
        self.set_dbus_value('/System/MinCellVoltage', min_v / 1000.0)
        self.set_dbus_value('/System/MaxCellVoltage', max_v / 1000.0)
        self.set_dbus_value('/System/MinCellVoltageCellId', int("%d%02d" % min_id) if min_id[0] >= 0 else 0)
        self.set_dbus_value('/System/MaxCellVoltageCellId', int("%d%02d" % max_id) if max_id[0] >= 0 else 0)
        self.set_dbus_value('/System/MinCellTemperature', min_t)
        self.set_dbus_value('/System/MaxCellTemperature', max_t)
        self.set_dbus_value('/System/MinCellTemperatureCellId', min_tid)
        self.set_dbus_value('/System/MaxCellTemperatureCellId', max_tid)
        for path, value in alarms.items():
            self.set_dbus_value(path, value)

        return True  # Continue timer
