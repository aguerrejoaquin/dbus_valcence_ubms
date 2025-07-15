## dbus_ubms
 CANBUS to dbus bridge for a Valence U-BMS to provide battery monitor service on Victronenergy Venus OS
 The idea here is to use a CAN-USB converter connected to the USB port of a Raspberry running VenusOS or Victron device directly.

 Note: I have personally used this on a Victron Cervo GX with a USB-Converter connected to a USB port.
 ![image](https://github.com/user-attachments/assets/fa433fa7-630d-4237-817a-b10d3de3babf)


## Preparation in VenusOS
1) Go to settings --> Services 
2) Plug-in the USB-CAN converter to the device and check what service appears. (in my case is under VE.CAN port)
![image](https://github.com/user-attachments/assets/df4bfa9a-f93c-4e85-bd4d-beb1ce399b87)


3) Get into VE.CAN port to configure as below
--> VE.CAN port --> CAN bus profile --> "and select CAN-BUS BMS (500kbit/s)"
![image](https://github.com/user-attachments/assets/6f1d8d3e-ae78-4b33-931a-c62dd7c0e2f0)


 ## Use this code at your own risk.
 
## Installation

Depending if you are using a Raspi or a Victron device (ex: Cerbo GX) you will need to use different codes to install this library. 

## Installation on Raspi
```
with git:
 opkg install git
 git clone https://github.com/gimx/dbus_ubms.git
 cd dbus_ubms/ext
 git clone https://github.com/victronenergy/velib_python.git

or download the above projects as archives, copy and unzip to root home
```

## Install git on Victron

Install git
```
 /opt/victronenergy/swupdate-scripts/resize2fs.sh
 opkg update
 opkg install git
```

Clone library
```
 git clone https://github.com/aguerrejoaquin/dbus_valence_ubms.git
 cd dbus_valence_ubms/ext
 git clone https://github.com/victronenergy/velib_python.git
```

## Preparation on Raspi
```
 sudo apt-get install libgtk2.0-dev  libdbus-1-dev libgirepository1.0-dev python-gobject python-can
 sudo pip install dbus-python can pygobject
```

## Preparation on Victron (may not be needed if previously installed Python)
```
 cd
 dbus_valence_ubms/prep_ubms.sh
```

## Run from command line (recommended to test this before next steps)
Check with the "ifconfig" for the CAN number port (ex: can0). You will need to know the CAN port that your USB converter is assigned. 
-v max voltage of the pack in series. I use (4) 27XP-12v batteries with a max charge voltage of 14V each.
-c capacity in Ah of the system (only the ones in parallel)

1. Debug/test the raw CAN reading with ubmsbattery.py
This will run the CAN listener/debugger for 10 seconds by default:
Adjust --duration if you want it to run longer.

```
cd dbus_valence_ubms
python3 ubmsbattery.py --modules 16 --strings 4 --capacity 650 --voltage 29.0 --connection can0 --duration 10
```
2. Run the D-Bus service with dbus_ubms.py
This will start the Victron D-Bus service with your configuration:

```
cd dbus_valence_ubms
python3 dbus_ubms.py --modules 16 --strings 4 --capacity 650 --voltage 29.0 --interface can0
```
Notes:

You might need to run with sudo if you get permission errors (especially for CAN and D-Bus).
If you want more or less debug output, set the logging level in the code or run with sudo.


## Run as a service: 
NOTE: IF you have your can connection in another port number, you need to change can0 by yours.

Add service files to the service folder and edit your run file with you port, volt, and capacity values
```
cd
ln -s /home/root/dbus_valence_ubms/service /service/dbus-ubms.can0
cd /service/dbus-ubms.can0
nano run
```

Edit, save, and exit

Add rc.local file into /data folder. This rc.local file is called after each boot and run all that its inside. Be aware if you already have this file, it will be overwrite. In this case I suggest to edit and add the lines that are in the file to yours.
after that, you can just reboot or call svc command to run the service.
```
 cp dbus_valence_ubms/rc.local /data/rc.local
 svc -u /service/dbus-ubms.can0
```
NOTE: IF you have your can connection in another port number, you need to change can0 in the rc.local file.

<img width="1179" height="633" alt="image" src="https://github.com/user-attachments/assets/b3bced6d-d0ef-4c97-93e4-4b34911bc549" />



## Configuration of U-BMS
```
 #It is very important that the U-BMS is configured for the correct number of batteries; otherwise, it will show 0% and alarms.

 set SOC calculation to minimum (not average)
 set voltage scaling factor to 1
 set VMU slave mode (error on timeout maybe on or off)
 configure C3 to single discharge/charge contactor, ie no separate charge path, no pre-charge, no on/off charge control
 connect C3 (and route battery + through it)
 connect battery voltage
 connect CAN and CAN 5V supply
 connect +12V for System and Ignition
 In a system SxPy (Series, Parallel), assign IDs to each modules with X bein the 1 to x and the parallel starts with x+1)
``` 
## Additional comments
You may experience that after activating the can port in the GUI, it will appear a new BMS (ex: LG battery) with wrong data. This happens because the is a service that runs automatically looking for BMSs once a CAN connection is detected. At this point, you have two options: you can ignore this, or you can disable this service for the port that you will be using for your BMS.
For example in my case, I used:
```
svc -d /service/can-bus-bms.can0
```
In this case, I suggest editing your rc.local file and adding that line so it will get disabled on boot.


## Credits
 - Majority of the protocol reverse engineering work was done by @cogito44 http://cogito44.free.fr
 - This code has been developed using information from the following (re-)sources
   - https://github.com/victronenergy/venus/wiki/howto-add-a-driver-to-Venus
   - https://github.com/victronenergy/venus/wiki/dbus#battery
   - https://github.com/victronenergy/velib_python
   - https://groups.google.com/forum/#!msg/victron-dev-venus/nCrpONiWYs0/-Z4wnkEJAAAJ;context-place=forum/victron-dev-venus
   - /opt/victronenergy/vrmlogger/datalist.py
   - https://groups.google.com/forum/#!searchin/victron-dev-venus/link$20to$20service%7Csort:date/victron-dev-venus/-AJzKTxk-3k/fOt707ZeAAAJ

