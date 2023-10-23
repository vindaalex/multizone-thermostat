from collections.abc import Callable
from typing import Any
from datetime import timedelta
import voluptuous as vol

from homeassistant.components.climate import HVACMode, PRESET_AWAY

from .const import *


def validate_initial_control_mode(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        for hvac_mode in [HVACMode.COOL, HVACMode.HEAT]:
            if hvac_mode in obj:
                if all(
                    x in obj[hvac_mode]
                    for x in [CONF_ON_OFF_MODE, CONF_PROPORTIONAL_MODE]
                ):
                    raise vol.Invalid(
                        "The on_off and proportional mode have both been set {} mode".format(
                            hvac_mode
                        )
                    )
        return obj

    return validate


def validate_window(*keys: str) -> Callable:
    """Check if filter is active when setting window open detection."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        for hvac_mode in [HVACMode.COOL, HVACMode.HEAT]:
            if hvac_mode in obj and CONF_FILTER_MODE not in obj:
                try:
                    if (
                        CONF_WINDOW_OPEN_TEMPDROP
                        in obj[hvac_mode][CONF_PROPORTIONAL_MODE][CONF_PID_MODE]
                    ):
                        raise vol.Invalid(
                            "window open check included for {} mode but required temperature filter not set".format(
                                hvac_mode
                            )
                        )
                except Exception:
                    pass

        return obj

    return validate


def validate_initial_sensors(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        for hvac_mode in [HVACMode.HEAT, HVACMode.COOL]:
            if hvac_mode in obj:
                if CONF_ON_OFF_MODE in obj[hvac_mode] and not CONF_SENSOR in obj:
                    raise vol.Invalid(
                        "on-off control defined but no temperature sensor for {} mode".format(
                            hvac_mode
                        )
                    )
                if CONF_PROPORTIONAL_MODE in obj[hvac_mode]:
                    if (
                        CONF_PID_MODE in obj[hvac_mode][CONF_PROPORTIONAL_MODE]
                        and not CONF_SENSOR in obj
                    ):
                        raise vol.Invalid(
                            "PID control defined but no temperature sensor for {} mode".format(
                                hvac_mode
                            )
                        )
                    if (
                        CONF_WC_MODE in obj[hvac_mode][CONF_PROPORTIONAL_MODE]
                        and not CONF_SENSOR_OUT in obj
                    ):
                        raise vol.Invalid(
                            "Weather control defined but no outdoor temperature sensor for {} mode".format(
                                hvac_mode
                            )
                        )
                if CONF_MASTER_MODE in obj[hvac_mode]:
                    if CONF_SATELITES not in obj[hvac_mode][CONF_MASTER_MODE]:
                        raise vol.Invalid(
                            "Master mode defined but no satelite thermostats for {} mode".format(
                                hvac_mode
                            )
                        )
                    pwm_duration = timedelta(
                        seconds=obj[hvac_mode][CONF_MASTER_MODE][CONF_PWM_DURATION].get(
                            "seconds", 0
                        ),
                        hours=obj[hvac_mode][CONF_MASTER_MODE][CONF_PWM_DURATION].get(
                            "hours", 0
                        ),
                    )
                    cntrl_duration = timedelta(
                        seconds=obj[hvac_mode][CONF_MASTER_MODE][
                            CONF_CONTROL_REFRESH_INTERVAL
                        ].get("seconds", 0),
                        hours=obj[hvac_mode][CONF_MASTER_MODE][
                            CONF_CONTROL_REFRESH_INTERVAL
                        ].get("hours", 0),
                    )
                    if pwm_duration.seconds > 0 and pwm_duration != cntrl_duration:
                        raise vol.Invalid(
                            "Master mode {} ({} sec) not equal {} ({} sec)".format(
                                str(CONF_PWM_DURATION),
                                pwm_duration.seconds,
                                str(CONF_CONTROL_REFRESH_INTERVAL),
                                cntrl_duration.seconds,
                            ),
                        )

        return obj

    return validate


def validate_initial_preset_mode(*keys: str) -> Callable:
    """If an initial preset mode has been set, check if the values are set in both modes."""

    def validate_by_mode(obj: dict[str, Any], preset: str, config_preset: str):
        """Use a helper to validate mode by mode."""
        if HVACMode.HEAT in obj.keys() and config_preset not in obj[HVACMode.HEAT]:
            raise vol.Invalid(
                "The preset {} has been set as initial preset but the {} is not present on {} mode".format(
                    preset, config_preset, HVACMode.HEAT
                )
            )
        if HVACMode.COOL in obj.keys() and config_preset not in obj[HVACMode.COOL]:
            raise vol.Invalid(
                "The preset {} has been set as initial preset but the {} is not present on {} mode".format(
                    preset, config_preset, HVACMode.COOL
                )
            )

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        if CONF_INITIAL_PRESET_MODE in obj and obj[CONF_INITIAL_PRESET_MODE] != "none":
            if obj[CONF_INITIAL_PRESET_MODE] == PRESET_AWAY:
                validate_by_mode(obj, PRESET_AWAY, CONF_TARGET_TEMP_AWAY)
        return obj

    return validate


def validate_initial_hvac_mode(*keys: str) -> Callable:
    """If an initial hvac mode has been set, check if this mode has been configured."""

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        """Check this condition."""
        if (
            CONF_INITIAL_HVAC_MODE in obj
            and obj[CONF_INITIAL_HVAC_MODE] != HVACMode.OFF
            and obj[CONF_INITIAL_HVAC_MODE] not in obj.keys()
        ):
            raise vol.Invalid(
                "You cannot set an initial HVAC mode if you did not configure this mode {}".format(
                    obj[CONF_INITIAL_HVAC_MODE]
                )
            )
        return obj

    return validate


def check_presets_in_both_modes(*keys: str) -> Callable:
    """If one preset is set on one mode, then this preset is enabled and check it on the other modes."""

    def validate_by_preset(obj: dict[str, Any], conf: str):
        """Check this condition."""
        if conf in obj[HVACMode.HEAT] and conf not in obj[HVACMode.COOL]:
            raise vol.Invalid(
                "{} is set for {} but not for {}".format(
                    conf, HVACMode.HEAT, HVACMode.COOL
                )
            )
        if conf in obj[HVACMode.COOL] and conf not in obj[HVACMode.HEAT]:
            raise vol.Invalid(
                "{} is set for {} but not for {}".format(
                    conf, HVACMode.COOL, HVACMode.HEAT
                )
            )

    def validate(obj: dict[str, Any]) -> dict[str, Any]:
        if HVACMode.HEAT in obj.keys() and HVACMode.COOL in obj.keys():
            validate_by_preset(obj, CONF_TARGET_TEMP_AWAY)
        return obj

    return validate
