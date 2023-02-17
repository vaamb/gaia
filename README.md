Gaia
====

Gaia is a small automation software used to control and monitor a greenhouse / 
a plant growth chamber or any other enclosed environment such as an aquarium or
a terrarium.

While Gaia is a standalone app, it can connect to 
[Ouranos](https://gitlab.com/eupla/ouranos.git), a small server that can be used
to more easily configure Gaia with a graphical user interface, log the data, draw
graphs ...

Note
----

Gaia is still in development and might not work properly.

Installation
------------

Gaia is written in Python (v. >= 3.7) and requires some extra dependencies,
some that might not be shipped with Python.

Make sure you have them before trying to install Gaia.

To do so on a Raspberry Pi, use

``apt update; apt install python3 python3-pip python3-venv git rustc`` (or 
``sudo apt update; sudo apt install python3 python3-pip python3-venv git rustc`` 
if required).

Then, copy the install.sh script from the script directory and 
run it in the directory in which you want to install Gaia
