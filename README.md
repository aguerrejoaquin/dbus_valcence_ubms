## dbus_ubms
 CANBUS to dbus bridge for a Valence U-BMS to provide battery monitor service on Victronenergy Venus OS
 The idea here is to use a CAN-USB converter connected to the USB port of a Raspberry running VenusOS or Victron device directly.

 Note: I have personally used this on a Victron Cervo GX with a USB-Converter connected to a USB port.
 <img width="342" alt="image" src="https://github.com/aguerrejoaquin/dbus_ubms/assets/132913905/3ff1d289-ec77-4b8e-8e78-0eb32ecf9fd4">

## Preparation in VenusOS
1) Go to settings --> Services 
2) Plug-in the USB-CAN converter to the device and check what service appears. (in my case is under VE.CAN port)
<img width="484" alt="image" src="https://github.com/aguerrejoaquin/dbus_ubms/assets/132913905/de253755-bb9e-47c7-9806-611ae5da5dc5">

3) Get into VE.CAN port to configure as below
--> VE.CAN port --> CAN bus profile --> "and select CAN-BUS BMS (500kbit/s)"
 <img width="484" alt="image" src="https://github.com/aguerrejoaquin/dbus_ubms/assets/132913905/0b19cd64-0dac-4470-b194-789076d40edf">

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
 git clone https://github.com/aguerrejoaquin/dbus_ubms.git
 cd dbus_ubms/ext
 git clone https://github.com/victronenergy/velib_python.git
```

## Preparation on Raspi
```
 sudo apt-get install libgtk2.0-dev  libdbus-1-dev libgirepository1.0-dev python-gobject python-can
 sudo pip install dbus-python can pygobject
```

## Preparation on Victron
```
 cd
 dbus_ubms/prep_ubms.sh
```

## Run from command line (recommended to test this before next steps)
Check with the "ifconfig" for the CAN number port (ex: can0). You will need to know the CAN port that your USB converter is assigned. 
-v max voltage of the pack in series. I use (4) 27XP-12v batteries with a max charge voltage of 14V each.
-c capacity in Ah of the system (only the ones in parallel)

```
 cd dbus_ubms

 python dbus_ubms.py -i can0 -v 56.0 -c 288
 or
 nohup python dbus_ubms.py -i can0 -v 29.0 -c 650 &
```

## Run as a service: 
NOTE: IF you have your can connection in another port number, you need to change can0 by yours.

Add service files to the service folder and edit your run file with you port, volt, and capacity values
```
cd
ln -s /home/root/dbus_ubms/service /service/dbus-ubms.can0
cd /service/dbus-ubms.can0
nano run
```

Edit, save, and exit

Add rc.local file into /data folder. This rc.local file is called after each boot and run all that its inside. Be aware if you already have this file, it will be overwrite. In this case I suggest to edit and add the lines that are in the file to yours.
after that, you can just reboot or call svc command to run the service.
```
 cp /dbus_ubms/rc.local /data/rc.local
 svc -u /service/dbus-ubms.can0
```
NOTE: IF you have your can connection in another port number, you need to change can0 in the rc.local file.

<img width="482" alt="image" src="https://github.com/aguerrejoaquin/dbus_ubms/assets/132913905/92a5a7d5-18ee-4723-93b2-5928f5e55524">

<img width="487" alt="image" src="https://github.com/aguerrejoaquin/dbus_ubms/assets/132913905/a591bfb3-fa9a-4ba6-88b7-01df24a50bf7">


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
 in a system with x modules in series and multiple in parallel, module numbers 1 to x have to be assigned to one string, pack voltage calculation depends on this 
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

