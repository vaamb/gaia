# Gaia's structure

Gaia has a hierarchical structure with classes calling other lower-level classes.

- The uppermost class, ```Gaia```, is instantiated when the application starts
and it calls all the other classes.

- ```Gaia``` will first call ```{config_parser.}GeneralConfig```. ```GeneralConfig```
is a singleton that parses ```ecosystems.cfg``` and ```private.cfg``` and 
provides utility functions to easily interact with these files.
It is also able to reparse ```ecosystems.cfg``` and ```private.cfg``` when
detecting chances in those files.

- In parallel, it also creates an instance of ```{engine.}Engine```, another
singleton and passes ```GeneralConfig``` object to it.

- Based on the ecosystems configured in ```ecosystem.cfg```, ```Engine``` will
spawn ```{ecosystem.}Ecosystem```. ```Ecosystem``` all have a link to 
```{config_parser.}SpecifigConfig```, an utility class linked to 
```{GeneralConfig}``` that allow to easily interact with a given ecosystem 
config.
All the ```Ecosystems``` are hold into a dict in ```Engine.ecosystems```. 
Conversely, ```Ecosystems``` have a weakref to the ```Engine``` through 
```Ecosystem.engine```.

- Upon instantiation, ```Ecosystems``` themselves instantiate all the 
subroutines contained in ```{subroutines}.SUBROUTINES```. Based on the 
management specified in ```ecosystem.cfg```, it will start and stop the required
subroutines.
All the ```Subroutines``` are hold into a dict in ```Ecosystem.subroutines```. 
Conversely, ```Subroutines``` have a weakref to their ```Ecosystem``` through 
```Subroutine.ecosystem```.

- In order to interact with the greenhouse, ```Subroutines``` will call the
required ```{hardware.}Hardware```. ```Hardware``` are classes divided in
```Actuators``` and ```Sensors``` that both allow Gaia to interact with the 
physical world.
All the ```Hardware``` are hold into a dict in ```Subrourine.hardware```. 
Conversely, ```Hardware``` have a weakref to their ```Subrourine``` through 
```Hardware.subroutine```.
