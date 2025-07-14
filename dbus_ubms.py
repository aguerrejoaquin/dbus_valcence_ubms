#!/usr/bin/env python3

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib
import os
import sys

SERVICE_NAME = 'com.victronenergy.battery.socketcan_can0'
OBJECT_PATH_BASE = '/com/victronenergy/battery/socketcan_can0'

# Minimal BusItem implementation
class BusItem(dbus.service.Object):
    def __init__(self, bus, path, initial_value):
        dbus.service.Object.__init__(self, bus, path)
        self.value = initial_value

    @dbus.service.method('com.victronenergy.BusItem', in_signature='', out_signature='v')
    def GetValue(self):
        return self.value

    @dbus.service.method('com.victronenergy.BusItem', in_signature='v', out_signature='')
    def SetValue(self, val):
        self.value = val

    # Venus OS expects Change event
    @dbus.service.signal('com.victronenergy.BusItem', signature='v')
    def PropertiesChanged(self, val):
        pass

def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    bus.request_name(SERVICE_NAME)

    # Export minimal required paths
    items = {
        '/Mgmt/ProcessName': os.path.basename(sys.argv[0]),
        '/Mgmt/ProcessVersion': '1.0',
        '/ProductId': 0,
        '/ProductName': 'Valence U-BMS',
        '/Serial': 'VALENCE-UBMS',
        '/Dc/0/Voltage': 52.0,
        '/Dc/0/Current': 0.0,
        '/Soc': 100,
    }
    # Register each item as a BusItem object
    busitems = []
    for path, val in items.items():
        busitems.append(BusItem(bus, OBJECT_PATH_BASE + path, val))

    print("Venus OS D-Bus battery service registered.")
    GLib.MainLoop().run()

if __name__ == '__main__':
    main()
