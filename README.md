[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)

# custom_components/multizone_thermostat

by borrowing and continuouing on code from and thanx to:
- DB-CL https://github.com/DB-CL/home-assistant/tree/new_generic_thermostat stale pull request with detailed seperation of hvac modes
- fabian degger (PID thermostat) originator PID control in generic thermostat, https://github.com/aendle/custom_components
some changes to the PID controller:
- aarmijo https://github.com/aarmijo/custom_components
- osi https://github.com/osirisinferi/custom_components
- wout https://github.com/Wout-S/custom_components


## Installation:
1. Go to <conf-dir> default /homeassistant/.homeassistant/ (it's where your configuration.yaml is)
2. Create <conf-dir>/custom_components/ directory if it does not already exist
3. Clone this repository content into <conf-dir>/custom_components/
4. Reboot HA without any configuration using the code to install requirements
5. Set up the multizone_thermostat and have fun

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

### Autotune:
WARNING: autotune is not tested, only code updates from above repo's are included as those seem to have 'improved things'. I'm not able to test these the autotune due to the slow reacting heating system. USE ON YOUR OWN RISK.

You can use the autotune feature to set the PID parameters.
The PID parmaters set by the autotune will overide the original PID values from the config and will be maintained when the restore_state is set the True. Restarting the climate with restore will maintain the autotune PID values. These are not written back to your climate yml file.
To save the parameters read the climate entity attributes, and copy the values to your config.

# Parameters:

## main:
* name (Required): Name of thermostat.
* target_sensor (optional): entity_id for a temperature sensor, target_sensor.state must be temperature. Not required when running in master mode: satelites are used.
* sensor_out (optional): entity_id for a outdoor temperature sensor, sensor_out.state must be temperature. Only required when running weather mode.
* initial_hvac_mode (Optional): Set the initial operation mode. Valid values are off or cool or heat. Default is off
* initial_preset_mode (Optional): Set the default mode. Default is None
* precision (Optional): specifiy precision of system: 0.1, 0.5 or 1
* unique_id (Optional): specify name for entity in registry else unique name is based on specified sensors and switches
* room_area (Optional): ratio (room area) for averiging thermostat when required when operating as satelite. Default is 0
* sensor_stale_duration (Optional): safety to turn switches of when sensor has not updated wthin specified period. Default no check
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
* For autotune parameters see section 'autotune'

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

## Autotune parameters:
* autotune (Optional): Choose a string for autotune settings.  If it's not set autotune is disabled.

tuning_rules | Kp_divisor, Ki_divisor, Kd_divisor
------------ | -------------
"ziegler-nichols" | 34, 40, 160
"tyreus-luyben" | 44,  9, 126
"ciancone-marlin" | 66, 88, 162
"pessen-integral" | 28, 50, 133
"some-overshoot" | 60, 40,  60
"no-overshoot" | 100, 40,  60
"brewing" | 2.5, 6, 380

* autotune_control_type (Optional): (default none). Disables the
tuning rules and sets the Ziegler-Nichols control type     according to: https://en.wikipedia.org/wiki/Ziegler%E2%80%93Nichols_method

  Possible values: p, pi, pd, classic_pid, pessen_integral_rule,
                    some_overshoot, no_overshoot

* noiseband (Optional): (default 0.5) Set noiseband (float).Determines by how much the input value must overshoot/undershoot the setpoint before the state changes during autotune.
* autotune_lookback (Optional): (default 60s). The reference period in seconds for local minima/maxima.





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
    restore_parameters: False

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
    restore_from_old_state: False
    restore_parameters: False

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
    restore_parameters: False
    restore_integral: True

    heat:
      entity_id: switch.fake_heater_switch
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
      proportional_mode:
        control_interval:
          minutes: 1
        difference: 100
        min_diff: 5
        pwm:
          minutes: 10
        PID_mode:
          kp: 5
          ki: 0.001
          kd: 100
          derative_avg:
            minutes: 20

```

Linear mode (weather compensating)

```
      proportional_mode:
        control_interval:
          minutes: 1
        difference: 100
        pwm:
          minutes: 10
        weather_mode:
          sensor_out: sensor.fake_sensor_out
          ka: 1
          kb: -5
```

master - satelite mode

```
  - platform: generic_thermostat
    name: main_valve
    initial_hvac_mode: "off"
    initial_preset_mode: "none"

    precision: 0.5

    heat:
      entity_id: switch.fake_heater_master
      min_temp: 15
      max_temp: 24
      initial_target_temp: 19
      away_temp: 12
      proportional_mode:
        control_interval:
          minutes: 1
        difference: 100
        pwm:
          minutes: 10
        weather_mode:
          sensor_out: sensor.fake_sensor_out
          ka: 1
          kb: -5
        MASTER_mode:
          satelites: [livingroom,]
```

