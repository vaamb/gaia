from .climate import gaiaClimate
from .health import gaiaHealth
from .light import gaiaLight
from .sensors import gaiaSensors


SUBROUTINES = (gaiaSensors, gaiaLight, gaiaClimate, gaiaHealth)
