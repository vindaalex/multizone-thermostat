[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# custom_components/multizone_thermostat
see also https://community.home-assistant.io/t/multizone-thermostat-incl-various-control-options

This is a home assistant custom component. It is a thermostat including various control options, such as: on-off, PID, weather controlled. The thermostat can be used in stand-alone mode or as zoned heating (master- satellites).

Note:
This is only the required software to create a (zoned) thermostat. Especially zoned heating systems will affect the flow in your heating system vy closing and opening valves. Please check your heating system if modifications are requried to handle the flow variations, such as: pump settings, bypass valves etc.
## Installation:
1. Go to <conf-dir> default /homeassistant/.homeassistant/ (it's where your configuration.yaml is)
2. Create <conf-dir>/custom_components/ directory if it does not already exist
3. Clone this repository content into <conf-dir>/custom_components/
4. Reboot HA without any configuration using the code to install requirements
5. Set up the multizone_thermostat and have fun


# thanx to:
by borrowing and continuouing on code from and thanx to:
- DB-CL https://github.com/DB-CL/home-assistant/tree/new_generic_thermostat stale pull request with detailed seperation of hvac modes
- fabian degger (PID thermostat) originator PID control in generic thermostat, https://github.com/aendle/custom_components
some changes to the PID controller:
- aarmijo https://github.com/aarmijo/custom_components
- osi https://github.com/osirisinferi/custom_components
- wout https://github.com/Wout-S/custom_components




# multi zone on-off and proportional controller thermostat

## Control modes:
### on-off:
the thermostat wll switch on or off dekending the setpoint and specified hysteris

### proportional mode:
Two control modes are included to control the thermostat:
- PID (temperature and valve control)
- Linear (weather compensating)

proportional controller will be called periodically.
If no pwm interval is defined, it will set the state of "heater" from 0 to "difference" value. Else, it will turn off and on the heater proportionally.

#### sensor filter:
An unscneted kalman filter is present to smoothen the temperature readings in case of of irregular updates. This could be the case for battery operated temperature sensors such as zigbee devices. This can be usefull in case of PID controller where derivative is controlled (speed of temperature change).
The filter intesity is defined by a factor between 0 to 5 (integer).
0 = no filter
5 = max smoothing

#### PID controller:
https://en.wikipedia.org/wiki/PID_controller

The control is based on NC actuators

heat mode: Kp & Ki positive, Kd negative
cool mode: Kp & Ki negative, Kd positive

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

Initial parameter and backgroud info:
* Cohen-Coon Tuning Rules P/PI/PID https://blog.opticontrols.com/archives/383

* AMIGO PI settings https://jckantor.github.io/CBE30338/04.06-PID-Controller-Tuning.html

* AMIGO PID settings http://www.cpdee.ufmg.br/~palhares/Revisiting_Ziegler_Nichols_step_response_method_for_PID.pdf

* underfloor heating parameter optimisation: https://www.google.nl/url?sa=t&rct=j&q=&esrc=s&source=web&cd=&cad=rja&uact=8&ved=2ahUKEwi5htyg5_buAhXeQxUIHaZHB5QQFjAAegQIBBAD&url=https%3A%2F%2Fwww.mdpi.com%2F1996-1073%2F13%2F8%2F2068%2Fhtml&usg=AOvVaw3CukGrgPjpIO2eKM619BIn

The python PID module:
[https://github.com/hirschmann/pid-autotune](https://github.com/hirschmann/pid-autotune)

PID controller explained. Would recommoned to read some of it:
[https://controlguru.com/table-of-contents/](https://controlguru.com/table-of-contents/)

PID controller and Ziegler-Nichols method:
[https://electronics.stackexchange.com/questions/118174/pid-controller-and-ziegler-nichols-method-how-to-get-oscillation-period](https://electronics.stackexchange.com/questions/118174/pid-controller-and-ziegler-nichols-method-how-to-get-oscillation-period)

Ziegler–Nichols Tuning:
[https://www.allaboutcircuits.com/projects/embedded-pid-temperature-control-part-6-zieglernichols-tuning/](https://www.allaboutcircuits.com/projects/embedded-pid-temperature-control-part-6-zieglernichols-tuning/)


#### weather compensating controller:
error = setpoint - outdoor_temp
output = error * ka + kb

heat mode: ka positive, kb negative
cool mode: ka negitive, kb positive


### master mode:
With this mode a multizone heating system an be created. Controlled rooms (satelites) can be linked to a master. The setpoint, room temperature and valve position will be read from the satelites and are averaged based on the room area. This mode can be run in combination with PID and Linear mode and in addition a satelite valve position PID controller is present. The satelite valve PID controller is defeind under master config

The valve control is based on NC actuators

heat and cool mode: Kp & Ki negative, Kd positive

# Parameters:

## main:
* name (Required): Name of thermostat.
* target_sensor (optional): entity_id for a temperature sensor, target_sensor.state must be temperature. Not required when running in master mode: satelites are used.
* sensor_filter(Optional): use unscented kalman filter to smoothen the temperature sensor readings. Especially usefull in case of irregular sensor updates such as battery operated devices (for instance zigbee sensor). Default = 0 (off) (see section 'sensor filter' for more details)
* sensor_out (optional): entity_id for a outdoor temperature sensor, sensor_out.state must be temperature. Only required when running weather mode.
* initial_hvac_mode (Optional): Set the initial operation mode. Valid values are off or cool or heat. Default is off
* initial_preset_mode (Optional): Set the default mode. Default is None
* precision (Optional): specifiy precision of system: 0.1, 0.5 or 1
* unique_id (Optional): specify name for entity in registry else unique name is based on specified sensors and switches
* room_area (Optional): ratio (room area) for averiging thermostat when required when operating as satelite. Default is 0

checks for sensor and switch
* sensor_stale_duration (Optional): safety to turn switches of when sensor has not updated wthin specified period. Default no check
* passive_switch_check (Optional): check at midnight if switch hasn't been operated for a secified time (passive_switch_duration per hvac_mode defined) to avoid stuck/jammed valve. Default is False. Per hvac_mode duration (where switch is specified) is specified

recovery of settings
* restore_from_old_state (Optional): restore certain old configuration and modes after restart. (setpoints, KP,KI,PD values, modes). Default is False
* restore_parameters (Optional): specify if previous controller parameters need to be restored. Default is false
* restore_integral (Optional): If PID integral needs to be restored. Avoid long restoration times. Default is false

### hvac modes:
hvac mode by:
* heat: | cool: (at least 1 to be included)
with the data (as sub)::
* entity_id (Required): entity_id for heater/cool switch, must be a toggle or proportional device (pwm =0).
* min_temp (Optional): Set minimum set point available (default: 17 (heat) or 20 (cool)).
* max_temp (Optional): Set maximum set point available (default: 24(heat) or 35 (cool)).
* initial_target_temp (Optional): Set initial target temperature. Failure to set this variable will result in target temperature being set to null on startup.(default: 19(heat) or 28 (cool)).
* away_temp (Optional): Set the temperature used by “away_mode”. If this is not specified, away_mode feature will not get activated.
* passive_switch_duration (Optional): specifiy per switch the maximum time before forcing toggle to avoid jammed valve.
further define one of: 'on_off_mode' or 'proportional_mode'

#### on_off mode:
on_off_mode: (Optional) (sub of hvac mode)
with the data (as sub):
* hysteresis_tolerance_on (Optional): temperature offset to switch on. default is 0.5
* hysteresis_tolerance_off (Optional): temperature offset to switch off. default is 0.5
* min_cycle_duration (Optional): Min duration to change switch status. If this is not specified, min_cycle_duration feature will not get activated.
* keep_alive (Optional): Min duration to re-run an update cycle. If this is not specified, feature will not get activated.


#### proportional mode:
proportional_mode: (Optional) (sub of hvac mode)
with the data (as sub):
* control_interval (Required): interval that controller is updated.
* minimal_diff (Optional): Set the minimal difference before activating swtich. To avoid very short off-on-off changes. Default is off
* difference (Optional): Set analog output offset to 0 (default 100). Example: If it's 500 the output Value can be everything between 0 and 500.
* pwm (Optional): Set period time for pwm signal in seconds. If it's not set, pwm is sending proportional value to switch. Default = 0
* resolution (optional): Set the stepsize between min and max difference. Default = 0

controller modes: (PID, Linear, Master)

##### PID controller:
PID controller (sub of proportional mode)
* PID_mode: (Optional)(as sub of proportional mode)
with the data (as sub):
* kp (Required): Set PID parameter, p control value.
* ki (Required): Set PID parameter, i control value.
* kd (Required): Set PID parameter, d control value.
* min_diff (Optional): Overide global minimum output. Default varies with chosen settings.
* max_diff (Optional): Overide global maximum output. Default = 'difference'
*window_open_tempdrop (Optional): Notice temporarily open window. Define temperature drop velocity below which PID is frozen to avoid integral and derative build-up. drop in Celcius per hour. Should  be negative. Default = off.

##### weather compensation:
Linear controller (sub of proportional mode)
* weather_mode: (Optional)(as sub of proportional mode)
with the data (as sub):
* ka (Required): Set PID parameter, ka control value.
* kb (Required): Set PID parameter, kb control value.
* max_diff (Optional): Overide global maximum output. Default = 'difference'

##### zone/valve controller:
Master controller (sub of proportional mode)
* MASTER_mode: (Optional)(as sub of proportional mode)
with the data (as sub):
* satelites (Required): list of climate entities to follow by the master (excl head part 'climate.')
Valve control mode is included as part of MASTER_mode
* goal (Optional): Setpoint of target valve position
* kp (Required): Set PID parameter, p control value.
* ki (Required): Set PID parameter, i control value.
* kd (Required): Set PID parameter, d control value.
* min_diff (Optional): Overide global minimum output. Default varies with chosen settings.
* max_diff (Optional): Overide global maximum output. Default = 'difference'
* For autotune parameters see section 'autotune'

## configuration.yaml
on-off mode - heat only
```
climate:
  - platform: multizone_thermostat
    name: satelite1
    sensor: sensor.fake_sensor_1
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 100
    precision: 0.5
    sensor_stale_duration:
      minutes: 20
    restore_from_old_state: True

    heat:
      entity_id: switch.fake_heater_switch
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
        on_off_mode:
          hysteresis_tolerance_on: 0.5
          hysteresis_tolerance_off: 1
          min_cycle_duration:
            minutes: 5
          keep_alive:
            minutes: 3
```

on-off mode - heat on and cool

```
climate:
  - platform: multizone_thermostat
    name: satelite1
    sensor: sensor.fake_sensor_1
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 100
    precision: 0.5
    sensor_stale_duration:
      minutes: 20

    heat:
      entity_id: switch.fake_heater_switch
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
        on_off_mode:
          hysteresis_tolerance_on: 0.5
          hysteresis_tolerance_off: 1
          min_cycle_duration:
            minutes: 5
          keep_alive:
            minutes: 3
    cool:
      entity_id: switch.fake_cool_switch
      min_temp: 24
      max_temp: 32
      initial_target_temp: 25
      away_temp: 28
        on_off_mode:
          hysteresis_tolerance_on: 0.5
          hysteresis_tolerance_off: 1
          min_cycle_duration:
            minutes: 5
          keep_alive:
            minutes: 3
```

proportional mode

```
  - platform: multizone_thermostat
    name: PID_example
    sensor: sensor.temp_sl1
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 20
    precision: 0.1
    heat:
      entity_id: switch.valve1
      min_temp: 15
      max_temp: 24
      initial_target_temp: 18
      away_temp: 16
      proportional_mode:
        control_interval:
          seconds: 60
        difference: 100
        minimal_diff: 5
        pwm:
          seconds: 180
        PID_mode:
          kp: 30
          ki: 0.003
          kd: -24000
    sensor_stale_duration:
      hours: 12
    restore_from_old_state: True
    restore_parameters: False
    restore_integral: True

```

Linear mode (weather compensating)

```
  - platform: multizone_thermostat
    name: weather_example
    sensor_out: sensor.br_temperature
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    precision: 0.1
    heat:
      entity_id: switch.switch1
      initial_target_temp: 20
      away_temp: 16
      proportional_mode:
        control_interval:
          seconds: 60
        difference: 100
        minimal_diff: 5
        pwm:
          seconds: 600
        weather_mode:
          ka: 2
          kb: -6

    sensor_stale_duration:
      hours: 12
    restore_from_old_state: True
    restore_parameters: False
    restore_integral: True
```

master - satelite mode

```

  - platform: multizone_thermostat
    name: master_example
    sensor_out: sensor.br_temperature
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    precision: 0.1
    heat:
      entity_id: switch.mainvalve
      initial_target_temp: 20
      away_temp: 16
      proportional_mode:
        control_interval:
          seconds: 60
        difference: 100
        minimal_diff: 5
        pwm:
          seconds: 600
        PID_mode:
          kp: 3
          ki: 0
          kd: 0
        weather_mode:
          ka: 2
          kb: -6
        MASTER_mode:
          satelites: [living, sleep1]
          goal: 80
          kp: -0.15
          ki: 0
          kd: 0
    sensor_stale_duration:
      hours: 12
    restore_from_old_state: True
    restore_parameters: False
    restore_integral: True

  - platform: multizone_thermostat
    name: living
    sensor: sensor.temp_living
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 60
    precision: 0.1
    heat:
      entity_id: switch.valve_living
      min_temp: 15
      max_temp: 24
      initial_target_temp: 20
      away_temp: 16
      proportional_mode:
        control_interval:
          seconds: 60
        difference: 100
        minimal_diff: 5
        pwm:
          seconds: 180
        PID_mode:
          kp: 30
          ki: 0.005
          kd: -24000
    sensor_stale_duration:
      hours: 12
    restore_from_old_state: True
    restore_parameters: False
    restore_integral: True


  - platform: multizone_thermostat
    name: sleep1
    sensor: sensor.temp_sl1
    initial_hvac_mode: "off"
    initial_preset_mode: "none"
    room_area: 20
    precision: 0.1
    heat:
      entity_id: switch.valve_sleep1
      min_temp: 15
      max_temp: 24
      initial_target_temp: 18
      away_temp: 16
      proportional_mode:
        control_interval:
          seconds: 60
        difference: 100
        minimal_diff: 5
        pwm:
          seconds: 180
        PID_mode:
          kp: 30
          ki: 0.003
          kd: -24000
    sensor_stale_duration:
      hours: 12
    restore_from_old_state: True
    restore_parameters: False
    restore_integral: True
```


examples to use attribute data

get valve position
```
  - platform: template
    sensors:
      valve_position:
        friendly_name: 'main valveposition'
        value_template: "{{ state_attr('climate.mainswitch', 'hvac_def')['heat']['valve_pos'] | float}}"
        unit_of_measurement: "%"

get (filtered) room temperature

  - platform: template
    sensors:
      temperature_room1:
        friendly_name: 'temperature room1'
        value_template: "{{ state_attr('climate.room1', 'current_temp_filt') | float}}"
        unit_of_measurement: "°C"
