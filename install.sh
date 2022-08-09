#!/bin/bash

# https://willcarh.art/blog/how-to-write-better-bash-spinners
spinner_pid=

spin(){
    while :; do
        for s in / - \\ \|; do
            printf "\r$s";
            sleep .1;
        done;
    done
}

function start_spinner {
    set +m
    { spin & } 2>/dev/null
    spinner_pid=$!
}

function stop_spinner {
    { kill -9 $spinner_pid && wait; } 2>/dev/null
    set -m
    echo -en "\033[2K\r"
}

trap stop_spinner EXIT;


echo "Enabling I2C, SPI and camera"
start_spinner
# Enable I2C
if [ $(grep -ic "^dtparam=i2c_arm=on" /boot/config.txt) -eq 0 ]; then
  echo "dtparam=i2c_arm=on" >> /boot/config.txt;
fi

if [ -f /etc/modules ]; then
  if [ $(grep -ic "^i2c-dev" /etc/modules) -eq 0 ]; then
    echo "i2c-dev" >> /etc/modules;
  fi
fi

# Enable 1-Wire
if [ $(grep -ic "^dtoverlay=w1-gpio" /boot/config.txt) -eq 0 ]; then
  echo "dtoverlay=w1-gpio" >> /boot/config.txt;
fi

# Enable camera
if [ $(grep -ic "^gpu_mem=" /boot/config.txt) -eq 0 ]; then
  echo "gpu_mem=128" >> /boot/config.txt;
fi

if [ $(grep -ic "^start_x=1" /boot/config.txt) -eq 0 ]; then
  echo "start_x=1" >> /boot/config.txt;
fi
stop_spinner;
echo "I2C, SPI and camera enabled";

# TODO: install camera packages (open cv related libraries)
echo "Installing python virtual environment";
start_spinner;
python3 -m venv venv;
stop_spinner;
echo "Python virtual environment installed";

source venv/bin/activate;

echo "Installing required python packages";
start_spinner;
pip3 install --upgrade setuptools;
pip3 install -r requirements.txt;
stop_spinner;
echo "Required python packages installed";


while true; do
    read -rp "Do you want to log your data to a database? [Y/N]" use_database
    case $use_database in
        [Yy]* )
            echo "Installing required packages";
            start_spinner;
            pip3 install sqlalchemy;
            stop_spinner;
            echo "Required packages installed";
            # TODO: change config file
            break;;
        [Nn]* ) break;;
        * ) echo "Please answer Y or N.";;
    esac
done

while true; do
    read -rp "Do you want Gaia to connect to Ouranos? [Y/N]" connect
    case $connect in
        [Yy]* )
            echo "Do you want to use socketio or a kombu supported service?"
            select option in "Socket.IO" "Kombu"; do
                case $option in
                    Socket.IO )
                      echo "Installing Socket.IO";
                      start_spinner;
                      pip3 install socketio[client] websocket-client;
                      stop_spinner;
                      echo "Socket.IO installed";;
                    Kombu )
                      echo "Installing Kombu";
                      start_spinner;
                      nohup pip3 install kombu;
                      stop_spinner;
                      echo "Kombu installed";;
                esac
            done;;
        [Nn]* ) break;;
        * ) echo "Please answer Y or N.";;
    esac
done


echo "Gaia is now installed";

while true; do
    read -rp "Do you want to automatically launch Gaia on startup? [Y/N]" auto_start
    case $auto_start in
        [Yy]* )
            if [ $(crontab -l | grep -ic "^@reboot.*gaia/run.sh") -eq 0 ]; then
                (crontab -l ; echo "@reboot /bin/bash ${PWD}/run.sh") | crontab;
            fi;;
        [Nn]* ) break;;
        * ) echo "Please answer Y or N.";;
    esac
done

echo "It might be required to install extra packages depending on the hardware used";
echo "To do so, install the required packages as indicated in the log files or in the docs and restart Gaia"
echo "To start Gaia, run './run.sh'"

deactivate

sudo reboot
