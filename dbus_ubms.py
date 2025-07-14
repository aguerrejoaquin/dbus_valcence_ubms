#!/usr/bin/env python3

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
import os

SERVICE_NAME = "com.victronenergy.battery.socketcan_can0"
BASE_PATH = "/com/victronenergy/battery/socketcan_can0"

# Each value exported as its own object with the BusItem interface
class BusItem(dbus.service.Object):
    def __init__(self, bus, path, initial_value):
        super().__init__(bus, path)
        self.value = initial_value

    @dbus.service.method("com.victronenergy.BusItem", in_signature='', out_signature='v')
    def GetValue(self):
        return self.value

    @dbus.service.method("com.victronenergy.BusItem", in_signature='v', out_signature='')
    def SetValue(self, val):
        self.value = val

    @dbus.service.signal("com.victronenergy.BusItem", signature="v")
    def PropertiesChanged(self, val):
        pass

def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    bus.request_name(SERVICE_NAME)

    # Venus OS expects these at minimum:
    items = {
        "/Mgmt/ProcessName": os.path.basename(__file__),
        "/Mgmt/ProcessVersion": "1.0",
        "/ProductId": 0,
        "/ProductName": "Valence U-BMS",
        "/Serial": "VALENCE-UBMS",
        "/Dc/0/Voltage": 52.0,
        "/Dc/0/Current": 0.0,
        "/Soc": 100,
    }
    busitems = []
    for path, val in items.items():
        full_path = BASE_PATH + path
        busitems.append(BusItem(bus, full_path, val))

    print("Venus OS D-Bus battery service registered.")
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
