# ... all your imports ...

from ubmsbattery import UbmsBattery

# ... rest of your code before DbusBatteryService ...

class DbusBatteryService:
    def __init__(
        self,
        servicename,
        deviceinstance,
        voltage,
        capacity,
        numberOfModules=8,
        numberOfStrings=2,
        productname="Valence U-BMS",
        connection="can0",
    ):
        self.minUpdateDone = 0
        self.dailyResetDone = 0
        self.lastUpdated = 0
        self._bat = UbmsBattery(
            capacity=capacity,
            voltage=voltage,
            connection=connection,
            numberOfModules=numberOfModules,
            numberOfStrings=numberOfStrings,
        )

        try:
            self._dbusservice = VeDbusService(
                servicename + ".socketcan_" + connection + "_di" + str(deviceinstance),
                register=False
            )
        except Exception as e:
            logging.error("VeDbusService init failed: %s", str(e))
            sys.exit(1)

        # ... all path creation below, using self._bat.numberOfModules, self._bat.numberOfStrings, etc. ...

        BATTERY_CELL_DATA_FORMAT = 1

        if BATTERY_CELL_DATA_FORMAT > 0:
            for i in range(1, self._bat.cellsPerModule * self._bat.numberOfModules + 1):
                cellpath = (
                    "/Cell/%s/Volts"
                    if (BATTERY_CELL_DATA_FORMAT & 2)
                    else "/Voltages/Cell%s"
                )
                self._dbusservice.add_path(
                    cellpath % (str(i)),
                    None,
                    writeable=True,
                    gettextcallback=lambda p, v: "{:0.3f}V".format(v) if v is not None else ""
                )
                if BATTERY_CELL_DATA_FORMAT & 1:
                    self._dbusservice.add_path(
                        "/Balances/Cell%s" % (str(i)), None, writeable=True
                    )
            pathbase = "Cell" if (BATTERY_CELL_DATA_FORMAT & 2) else "Voltages"
            self._dbusservice.add_path(
                "/%s/Sum" % pathbase,
                None,
                writeable=True,
                gettextcallback=lambda p, v: "{:2.2f}V".format(v) if v is not None else ""
            )
            self._dbusservice.add_path(
                "/%s/Diff" % pathbase,
                None,
                writeable=True,
                gettextcallback=lambda p, v: "{:0.3f}V".format(v) if v is not None else ""
            )

        # ... rest of your code unchanged, always using self._bat.numberOfModules for any cell/module iteration ...

def main():
    from argparse import ArgumentParser
    parser = ArgumentParser(description="dbus_ubms", add_help=True)
    parser.add_argument("-i", "--interface", help="CAN interface")
    parser.add_argument("-c", "--capacity", help="capacity in Ah", type=int)
    parser.add_argument("-v", "--voltage", help="maximum charge voltage V", type=float)
    parser.add_argument("--modules", help="number of modules", type=int, default=8)
    parser.add_argument("--strings", help="number of parallel strings", type=int, default=2)
    parser.add_argument("-d", "--debug", help="enable debug logging", action="store_true")
    parser.add_argument("-p", "--print", help="print only")

    args = parser.parse_args()
    logging.basicConfig(
        format="%(levelname)-8s %(message)s",
        level=(logging.DEBUG if args.debug else logging.INFO),
    )

    if not args.interface:
        logging.info("No CAN interface specified, using default can0")
        args.interface = "can0"

    if not args.capacity:
        logging.warning("Battery capacity not specified, using default (130Ah)")
        args.capacity = 130

    if not args.voltage:
        logging.error("Maximum charge voltage not specified. Exiting.")
        return

    os.system(f"ip link set {args.interface} type can bitrate 250000")
    os.system(f"ifconfig {args.interface} up")

    from dbus.mainloop.glib import DBusGMainLoop

    if sys.version_info.major == 2:
        import gobject
        gobject.threads_init()
    DBusGMainLoop(set_as_default=True)

    DbusBatteryService(
        servicename="com.victronenergy.battery",
        connection=args.interface,
        deviceinstance=0,
        capacity=int(args.capacity),
        voltage=float(args.voltage),
        numberOfModules=args.modules,
        numberOfStrings=args.strings,
    )

    mainloop = GLib.MainLoop()
    mainloop.run()

if __name__ == "__main__":
    main()
