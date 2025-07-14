#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import time
import logging

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

from ubmsbattery import UbmsBattery

try:
    import RPi.GPIO as GPIO
    gpio_available = True
except ImportError:
    gpio_available = False

log = logging.getLogger("dbus_ubms")
logging.basicConfig(level=logging.INFO)

class DbusUbmsService(dbus.service.Object):
    def __init__(self, battery, servicename='com.victronenergy.battery.ttyUBMS_Can0', deviceinstance=0, gpio_relay_pin=None):
        self.battery = battery
        self.deviceinstance = deviceinstance
        self.gpio_relay_pin = gpio_relay_pin
        self.last_alarm_state = False

        bus = dbus.SystemBus()
        dbus.service.Object.__init__(self, bus, '/')
        self._service = bus.request_name(servicename)
        self.paths = {}

        # Configurable thresholds
        self.thresholds = {
            'min_cell_voltage': 2700,    # mV
            'max_cell_voltage': 3600,    # mV
            'cell_imbalance':    50,     # mV
            'max_cell_temp':     55,     # degC
            'min_cell_temp':     0,      # degC
            'max_charge_current': 100,   # A
            'max_discharge_current': 100,# A
            'min_soc':           5,      # %
        }

        # GPIO relay support
        if gpio_available and gpio_relay_pin is not None:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(gpio_relay_pin, GPIO.OUT)
            self.set_relay(False)

        # D-Bus static paths
        static_paths = {
            '/Mgmt/ProcessName': 'dbus_ubms',
            '/Mgmt/ProcessVersion': '1.0',
            '/DeviceInstance': self.deviceinstance,
            '/ProductId': 0xA042,
            '/ProductName': 'Valence U-BMS',
            '/FirmwareVersion': '1.0',
            '/HardwareVersion': '1.0',
            '/Connected': 1,
            '/Capacity': self.battery.capacity,
            '/System/NrOfBatteries': 1,
            '/System/NrOfModules': self.battery.numberOfModules,
            '/System/NrOfCellsPerModule': self.battery.cellsPerModule,
            '/System/NrOfCellsPerBattery': self.battery.numberOfModules * self.battery.cellsPerModule,
        }
        for p, v in static_paths.items():
            self.add_path(p, v)

        # Dynamic paths (updated every second)
        self.dynamic_paths = [
            '/Dc/0/Voltage',
            '/Dc/0/Current',
            '/Soc',
            '/System/MinCellVoltage',
            '/System/MaxCellVoltage',
            '/System/MinCellVoltageCellId',
            '/System/MaxCellVoltageCellId',
            '/System/MinCellTemperature',
            '/System/MaxCellTemperature',
            '/System/MinCellTemperatureCellId',
            '/System/MaxCellTemperatureCellId',
            '/System/Alarms/CellImbalance',
            '/System/Alarms/LowCellVoltage',
            '/System/Alarms/HighCellVoltage',
            '/System/Alarms/LowSoc',
            '/System/Alarms/HighChargeCurrent',
            '/System/Alarms/HighDischargeCurrent',
            '/System/Alarms/CellTemperature',
        ]
        for p in self.dynamic_paths:
            self.add_path(p, 0)

        # Add dynamic paths for each module's SOC
        for i in range(self.battery.numberOfModules):
            path = f'/Module/{i}/Soc'
            self.add_path(path, 0)
            self.dynamic_paths.append(path)

        # GLib Timer for updates
        GLib.timeout_add(1000, self._update)

    def add_path(self, path, value):
        self.paths[path] = value

    def set_dbus_value(self, path, value):
        if self.paths.get(path) != value:
            self.paths[path] = value
            self.PropertiesChanged('com.victronenergy.BusItem', {path: value}, [])

    @dbus.service.signal(dbus_interface='com.victronenergy.BusItem', signature='sa{sv}as')
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def set_relay(self, state):
        if gpio_available and self.gpio_relay_pin is not None:
            GPIO.output(self.gpio_relay_pin, GPIO.HIGH if state else GPIO.LOW)
            log.info(f"Relay set to {'ON' if state else 'OFF'}")

    def _update(self):
        # --- Read battery values ---
        v = self.battery.get_pack_voltage()
        c = self.battery.current
        soc = self.battery.soc

        # Prepare min/max cell voltage and temperature, with IDs if possible
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
        min_tid = max_tid = 0  # Only pack-level available for temperature

        # --- Alarms ---
        alarms = {
            '/System/Alarms/LowCellVoltage':   int(min_v < self.thresholds['min_cell_voltage']),
            '/System/Alarms/HighCellVoltage':  int(max_v > self.thresholds['max_cell_voltage']),
            '/System/Alarms/CellImbalance':    int((max_v - min_v) > self.thresholds['cell_imbalance']),
            '/System/Alarms/LowSoc':           int(soc < self.thresholds['min_soc']),
            '/System/Alarms/HighChargeCurrent':int(c > self.thresholds['max_charge_current']),
            '/System/Alarms/HighDischargeCurrent': int(abs(c) > self.thresholds['max_discharge_current']),
            '/System/Alarms/CellTemperature':  int((max_t > self.thresholds['max_cell_temp']) or (min_t < self.thresholds['min_cell_temp'])),
        }

        # --- Relay logic: trip relay on any alarm ---
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
        for i, msoc in enumerate(self.battery.moduleSoc):
            log.info(f"Module {i} SOC: {msoc}%")

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
        # Publish each module's SOC
        for i, msoc in enumerate(self.battery.moduleSoc):
            self.set_dbus_value(f'/Module/{i}/Soc', msoc)

        return True  # Continue timer

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--capacity", type=int, default=650)
    parser.add_argument("--voltage", type=float, default=29.0)
    parser.add_argument("--connection", type=str, default="can0")
    parser.add_argument("--modules", type=int, default=16)
    parser.add_argument("--strings", type=int, default=4)
    parser.add_argument("--deviceinstance", type=int, default=0)
    parser.add_argument("--gpio-relay-pin", type=int, default=None, help="GPIO pin to control relay (BCM numbering)")
    parser.add_argument("--min_cell_voltage", type=int, default=2700)
    parser.add_argument("--max_cell_voltage", type=int, default=3600)
    parser.add_argument("--cell_imbalance", type=int, default=50)
    parser.add_argument("--max_cell_temp", type=int, default=55)
    parser.add_argument("--min_cell_temp", type=int, default=0)
    parser.add_argument("--max_charge_current", type=int, default=100)
    parser.add_argument("--max_discharge_current", type=int, default=100)
    parser.add_argument("--min_soc", type=int, default=5)
    args = parser.parse_args()

    # --- D-Bus main loop must be set BEFORE any D-Bus object creation ---
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    logging.basicConfig(level=logging.INFO)

    battery = UbmsBattery(
        capacity=args.capacity,
        voltage=args.voltage,
        connection=args.connection,
        numberOfModules=args.modules,
        numberOfStrings=args.strings
    )

    service = DbusUbmsService(
        battery,
        deviceinstance=args.deviceinstance,
        gpio_relay_pin=args.gpio_relay_pin
    )

    # Override thresholds from args
    for k in service.thresholds:
        argval = getattr(args, k, None)
        if argval is not None:
            service.thresholds[k] = argval

    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        battery.notifier.stop()
        battery._ci.shutdown()
        sys.exit(0)

if __name__ == "__main__":
    main()
