#!/bin/bash

echo "Installing Gaia"

# Enable I2C, SPI and camera
echo "Enabling I2C, SPI and camera"

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

echo "I2C, SPI and camera enabled";

echo "Installing some Python-unrelated packages";

sudo apt update > /dev/null;
sudo apt install -y libffi-dev libssl-dev > /dev/null;

echo "Creating Gaia directory";

# Create Gaia dir and sub dirs
mkdir -p "gaia"; cd "gaia"
GAIA_DIR=$PWD

if [ ! -d "python_venv" ]; then
  echo "Creating a python virtual environment"
  python3 -m venv python_venv
fi
source python_venv/bin/activate

mkdir -p "logs"
mkdir -p "scripts"
mkdir -p "lib"; cd "lib"

# Get Gaia and install the package
if [ ! -d "gaia" ]; then
  echo "Getting Gaia repository"
  git clone --branch stable https://gitlab.com/eupla/gaia.git "gaia" > /dev/null
  if [ $? = 0 ] ; then
    cd "gaia"
  else
    echo "Failed to get Gaia repository from git";
    exit 2
  fi
  echo "Updating pip setuptools and wheel"
  pip install --upgrade pip setuptools wheel
else
  echo "Detecting an existing installation, you should update it if needed. Stopping"
  exit 1
fi
echo "Installing Gaia and its dependencies"
pip install -e .
deactivate

# Make Gaia scripts easily available
cp main.py $GAIA_DIR/
cp -r scripts/ $GAIA_DIR/

cd "$GAIA_DIR/scripts/"
chmod +x start.sh stop.sh update.sh

if [ $(grep -ic "#>>>Gaia variables>>>" $HOME/.bash_profile) -eq 1 ]; then
  sed -i "/#>>>Gaia variables>>>/,/#<<<Gaia variables<<</d" $HOME/.bash_profile;
fi

echo "
#>>>Gaia variables>>>
# Gaia root directory
export GAIA_DIR=$GAIA_DIR

# Gaia utility function to start and stop the main application
gaia() {
  case \$1 in
    start) nohup \$GAIA_DIR/scripts/start.sh &> \$GAIA_DIR/logs/nohup.out & ;;
    stop) \$GAIA_DIR/scripts/stop.sh ;;
    stdout) tail \$GAIA_DIR/logs/nohup.out ;;
    update) bash \$GAIA_DIR/scripts/update.sh ;;
    *) echo 'Need an argument in start, stop, stdout or update' ;;
  esac
}
complete -W 'start stop stdout update' gaia
#<<<Gaia variables<<<
" >> $HOME/.bash_profile;

source $HOME/.bash_profile

echo "Gaia installed."
echo "It might be required to install extra python packages depending on the hardware used."
echo "To do so, install the required packages as indicated in the log files or in the docs and restart Gaia."
echo "To start Gaia, either use \`gaia start\` or go to the gaia directory, activate the virtual environment and run \`python main.py\`"
