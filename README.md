[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# custom_components/multizone_thermostat
see also https://community.home-assistant.io/t/multizone-thermostat-incl-various-control-options

This is a home assistant custom component. It is a thermostat including various control options, such as: on-off, PID, weather controlled. The thermostat can be used in stand-alone mode or as zoned heating (master with satellites).

Note:
This is only the required software to create a (zoned) thermostat. Especially zoned heating systems will affect the flow in your heating system vy closing and opening valves. Please check your heating system if modifications are requried to handle the flow variations, such as: pump settings, bypass valves etc.
## Installation:
1. Go to <conf-dir> default /homeassistant/.homeassistant/ (it's where your configuration.yaml is)
2. Create <conf-dir>/custom_components/ directory if it does not already exist
3. Clone this repository content into <conf-dir>/custom_components/
4. Reboot HA without any configuration using the code to install requirements
5. Set up the multizone_thermostat and have fun


# thanx to:
by borrowing and continuouing on code from:
- DB-CL https://github.com/DB-CL/home-assistant/tree/new_generic_thermostat stale pull request with detailed seperation of hvac modes
- fabian degger (PID thermostat) originator PID control in generic thermostat, https://github.com/aendle/custom_components

# Operation modes
The multizone thermostat can operate in two modes:
- thermostats can operate stand-alone, thus without interaction with others
- thermostats can operate under the control of a master controller scheduling and balancing the heat request

Each configured thermostat has to be configured for that specific room. A thermostat can operate by either hyesteric (on-off mode) or proportional mode (weather compensation and PID mode). The PID and weather compensation can be combined or one of both can be used. Only a satelite operating in proportional mode can be used as satelite as hysteric operation (on-off by a dT) cannot run in synchronised mode with other satelites and the master.

When a master controller is included it will coordinate all enlisted satelites valve opening and closures. When the master is activated to heat or cool it will trigger the satelites to update their controller and from that moment it interacts with the master. A satelite interaction with the master will be updated when the master is activated or switched off. When the master is activated to heat or cool the controller routine is synced to the master controller interval time. When the master is switched off the satelite will return to its stand-alone mode with its own settings. The master itself gets the satelite state (pwm signal) and return the moment the satelite has to open or close valves. The master determines the moment when the satelite valves is opened, the satelite itself still determines the valve opening time.

# Room thermostat configuration (not for master config)
This thermostat is used for satelite or stand-alone operation mode. 
The thermostat can be configured for a wide variation of hardware specifications and options:
- The switch can be a on-off switch or proportional (0-100) valve
- The switch can be of the type normally closed (NC) or normally opened (NO)
- Operation for heating and cool are specified indiviually
- For sensors with irregular update intervals such as battery operated sensors an optional uncented kalman filter is included
- Window open detection can be included
- Valve stuck prevention 
- Restore operational configuration after HA reboot

## Thermostat configuration
* platform (Required): 'multizone_thermostat'
* name (Required): Name of thermostat.
* unique_id (Optional): specify name for entity in registry else unique name is based on specified sensors and switches
* room_area (Optional): Required when operating in satelite mode. The room area is needed to determine the scale effect of the room to the total heat requirement. Default = 0 (only stand alone mode possible, not allowed for satelite mode)

sensors (at least one sensor needs to be specified):
* sensor (Optional): entity_id of the temperature sensor, sensor.state must be temperature (float). Not required when running in weather compensation only.
* filter_mode (Optional): unscented kalman filter can be used to smoothen the temperature sensor readings. Especially usefull in case of irregular sensor updates such as battery operated devices (for instance battery operated zigbee sensor). Default = 0 (off) (see section 'sensor filter' for more details)
* sensor_out (Optional): entity_id for a outdoor temperature sensor, sensor_out.state must be temperature (float). Only required when running weather mode. No filtering possible.

* initial_hvac_mode (Optional): Set the initial operation mode. Valid values are 'off', 'cool' or 'heat'. Default = off
* initial_preset_mode (Optional): Set the default mode. Default is normal operational mode. Allowed alternative is 'away'

* precision (Optional): specifiy setpoint precision: 0.1, 0.5 or 1
* detailed_output (Optional): include detailed control output including PID contributions and sub-control (pwm) output. To include detailed output use 'on'. Use this option limited for debugging and tuning only as it increases the database size. Default = off

checks for sensor and switch:
* sensor_stale_duration (Optional): safety routine to turn switches off when sensor has not updated for a specified time period. Specify time period. Default is not activated.
* passive_switch_check (Optional): check at midnight (02:00) if switch hasn't been operated for a secified time (passive_switch_duration per hvac_mode defined) to avoid stuck/jammed valve. Per hvac_mode the duration (where switch is specified) is specified. During satelite mode only activated when master is idle or off. Specify 'True' to activate. Default = False.

recovery of settings
* restore_from_old_state (Optional): restore certain old configuration and modes after restart. Specify 'True' to activate. (setpoints, KP,KI,PD values, modes). Default = False
* restore_parameters (Optional): specify if previous controller parameters need to be restored. Specify 'True' to activate. Default = False
* restore_integral (Optional): If PID integral needs to be restored. Avoid long restoration times. Specify 'True' to activate. Default = False
### HVAC modes: heat or cool (sub entity config)
The control is specified per hvac mode (heat, cool). At least 1 to be included.
EAch HVAC mode should include one of the control modes: on-off, proportional or master.

Generic HVAC mode setting:
* entity_id (Required): This can be an on-off switch or a proportional valve(input_number, etc)
* switch_mode (Optional): Specify if switch (valve) is normally closed 'NC' or normally open 'NO'. Default = 'NC'

* min_target_temp (Optional): Lower limit temperature setpoint. Default heat=17, cool=20
* max_target_temp (Optional): Upper limit temperature setpoint. Default for heat=24, cool=35
* initial_target_temp (Optional): Initial setpoint at start. Default for heat=19, cool=28
* away_temp (Optional): Setpoint when away preset is activated. Defining an away setpoint will make away preset available. default no away preset mode available. 

* passive_switch_duration (Optional): specifiy per switch the maximum time before forcing toggle to avoid jammed valve. Specify a time period. Default is not activated.

#### on-off mode (Optional) (sub of hvac mode)
The thermostat will switch on or off depending the setpoint and specified hysteris. Configured under 'on_off_mode:' 
with the data (as sub of 'on_off_mode:'):
* hysteresis_on (Required): Lower bound: temperature offset to switch on. default is 0.5
* hysteresis_off (Required): Upper bound: temperature offset to switch off. default is 0.5
* min_cycle_duration (Optional): Min duration to change switch status. If this is not specified, min_cycle_duration feature will not get activated. Specify a time period.
* control_interval (Optional): Min duration to re-run an update cycle. If this is not specified, feature will not get activated. Specify a time period.

#### proportional mode (Optional) (sub of hvac mode)
Configured under 'proportional_mode:' 
Two control modes are included to control the proportional thermostat. A single one can be specfied or combined. The control output of both are summed.
- PID controller: control by setpoint and room temperature
- Weather compensating: control by room- and outdoor temperature

The proportional controller is called periodically and specified by control_interval.
If no pwm interval is defined, it will set the state of "heater" from 0 to "difference" value as for a proportional valve. Else, when pwm is specified it will operate in on-off mode and will switch proportionally with the pwm signal.

* control_interval (Required): interval that controller is updated. The satelites should have a control_interval equal to the master or the master control_interval should be dividable by the satelite control_interval. Specify a time period.
* pwm_duration (Optional): Set period time for pwm signal. If it's not set, pwm is sending proportional value to switch. Specify a time period. Default = 0
* pwm_scale (Optional): Set analog output offset to 0. Example: If it's 500 the output value can be between 0 and 500. Default = 100
* pwm_resolution (optional): Set the resolution of the pwm_scale between min and max difference. Default = 50 (50 steps between 0 and 100)
* pwm_threshold (Optional): Set the minimal difference before activating switch. To avoid very short off-on-off or on-off-on changes. Default is not acitvated

##### PID controller (Optional) (sub of proportional mode)
PID controller. Configured under 'PID_mode:'
error = setpoint - room temp 
output = error * Kp + sum(error * dT) * Ki + error / dT * Kd

heat mode: Kp & Ki positive, Kd negative
cool mode: Kp & Ki negative, Kd positive

with the data (as sub):
* kp (Required): Set PID parameter, p control value.
* ki (Required): Set PID parameter, i control value.
* kd (Required): Set PID parameter, d control value.
* pwm_scale_low (Optional): Overide lower bound pwm scale for this mode. Default = 0
* pwm_scale_high (Optional): Overide upper bound pwm scale for this mode. Default = 'pwm_scale'
* window_open_tempdrop (Optional): notice temporarily opened window. Define minimum temperature drop speed below which PID is frozen to avoid integral and derative build-up. drop in Celcius per hour. Should be negative value. Default = off.

##### Weather compensating controller (Optional) (sub of proportional mode)
Weather compensation controller. Configured under 'weather_mode:'
error = setpoint - outdoor_temp
output = error * ka + kb

heat mode: ka positive, kb negative
cool mode: ka negitive, kb positive

with the data (as sub):
* ka (Required): Set PID parameter, ka control value.
* kb (Required): Set PID parameter, kb control value.
* pwm_scale_low (Optional): Overide lower bound pwm scale for this mode. Default = pwm_scale * -1
* pwm_scale_high (Optional): Overide upper bound pwm scale for this mode. Default = 'pwm_scale'

# Master configuration
The configuration scheme is similar as for a satelite only with the following differences.

* name: For master mode no name possible, it will be 'master'
* room_area (Required): For master it should be equal to the total heated area. 
* sensor (optional): For master mode not applicable
* filter_mode (Optional): For master mode not applicable
* sensor_out (optional): For master mode not applicable
* precision (Optional): For master mode not applicable

### HVAC modes: heat or cool (sub entity config)
The control is specified per hvac mode (heat, cool). At least 1 to be included.
EAch HVAC mode should include one of the control modes: on-off, proportional or master.

Generic HVAC mode setting:
* min_target_temp (Optional): For master mode not applicable
* max_target_temp (Optional): For master mode not applicable
* initial_target_temp (Optional): For master mode not applicable
* away_temp (Optional): For master mode not applicable
#### on-off mode (Optional) (sub of hvac mode)
For master mode not applicable

#### proportional mode (Optional) (sub of hvac mode)
For master mode not applicable

#### Master configuration (Required) (sub of hvac mode)
Specify the control of the satelites. Configured under 'master_mode:'

Referenced thermostats (satelites) will be linked to this controller. The heat or cool requirement will be read from the satelites and are processed to determine master valve opening and adjust timing of satelite openings. 

The master will check satelite states and group them in on-off and proportional valves. The govering group will define the opening time of the master valve.  

The master can operate in 'minimal_on', 'balanced' or 'continuous' mode. This will determine the satelite timing scheduling. For the minimal_on mode the master valve is opened as short as possible, for balanced mode the opening time is balanced between heating power and duration and for continuous mode the valve opening time is extended as long as possible. All satelite valves operating as on-off switch are used for the nesting are scheduled in time to get a balanced heat requirement. In 'continuous' mode the satelite timing is scheduled aimed such that a continuous heat requirement is created. The master valve will be opened continuous when sufficient heat is needed. In low demand conditions an on-off mode is maintained. 

The preset mode changes on the master will be synced to the satelites.

with the data (as sub):
* satelites (Required): between square brackets defined list of thermostats by their name 
* operation_mode (Optional): satelite nesting method: "minimal_on", "balanced" or "continuous". Default = "balanced"
* lower_load_scale (Optional): For nesting assumed minimum required load heater. Default = 0.2. (a minimum heating capacity of 20%  assumed based on 100% when all rooms required heat)

##### Valve controller (Optional) (sub of master mode)
Adjust the master control output for satelites with proportional valves to a goal control value. For instance: when the satelites with proportional valve has a maximum opening of 10% in can lower the control output for the master and thereby forces an increased opening of the satelite valve. Assumed that a large valve opening is equal to better heat transfer.

Configured under 'PID_valve_mode:'
* goal (Optional): Wanted precentage opening of valve (between 0 and 1)
* kp (Required): Set PID parameter, p control value.
* ki (Required): Set PID parameter, i control value.
* kd (Required): Set PID parameter, d control value.
* pwm_scale_low (Optional): For master mode not applicable
* pwm_scale_high (Optional): For master mode not applicable
# Sensor filter (filter_mode):
An unscneted kalman filter is present to smoothen the temperature readings in case of of irregular updates. This could be the case for battery operated temperature sensors such as zigbee devices. This can be usefull in case of PID controller where derivative is controlled (speed of temperature change).
The filter intesity is defined by a factor between 0 to 5 (integer).
0 = no filter
5 = max smoothing

# PID controller:
https://en.wikipedia.org/wiki/PID_controller

examples:
slow low temperature underfloor heating:
  PID_mode:
    kp: 30
    ki: 0.005
    kd: -24000

high temperature radiator:
  PID_mode:
    kp: 80
    ki: 0.09
    kd: -5000

* underfloor heating parameter optimisation: https://www.google.nl/url?sa=t&rct=j&q=&esrc=s&source=web&cd=&cad=rja&uact=8&ved=2ahUKEwi5htyg5_buAhXeQxUIHaZHB5QQFjAAegQIBBAD&url=https%3A%2F%2Fwww.mdpi.com%2F1996-1073%2F13%2F8%2F2068%2Fhtml&usg=AOvVaw3CukGrgPjpIO2eKM619BIn

PID controller explained. Would recommoned to read some of it:
[https://controlguru.com/table-of-contents/](https://controlguru.com/table-of-contents/)


# DEBUGGING:
debugging is possible by enabling logger in configuration with following configuration
```
logger:
  default: info
  logs:
    multizone_thermostat: debug
```    
# Services callable from HA:
Several services are included to change the active configuration of a satelite or master. 
## set_mid_diff:
Change the 'minimal_diff'
## set_preset_mode:
Change the 'preset'
## set_pid:
Change the current kp, ki, kd values of the PID or Valve PID controller
## set_integral:
Change the PID integral value contribution
## set_goal:
Change the target valve opening for proportional valves
## set_ka_kb:
Change the ka and kb for the weather controller
## set_filter_mode:
change the UKF filter level for the temperature sensor
## detailed_output:
Control the attribute output for PID-, WC-contributions and control output
# configuration examples
see examples folder  