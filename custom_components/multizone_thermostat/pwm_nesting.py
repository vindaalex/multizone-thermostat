"""Nesting routine.

Nesting of rooms by pwm and area size to get equal heat distribution
and determine when master needs to be operated
rooms switch delay are determined.
"""

import copy
import itertools
import logging
from math import ceil, floor
import time

import numpy as np

from . import DOMAIN
from .const import (
    ATTR_CONTROL_OFFSET,
    ATTR_CONTROL_PWM_OUTPUT,
    ATTR_ROOMS,
    ATTR_ROUNDED_PWM,
    ATTR_SCALED_PWM,
    CONF_AREA,
    CONF_PWM_DURATION,
    CONF_PWM_SCALE,
    NESTING_BALANCE,
    NESTING_DOMINANCE,
    NESTING_MARGIN,
    NESTING_MATRIX,
    NestingMode,
)


class Nesting:
    """Nest rooms by area size and pwm in order to get equal heat requirement."""

    def __init__(
        self,
        name: str,
        operation_mode: NestingMode,
        master_pwm: float,
        tot_area: float,
        min_load: float,
        pwm_threshold: float,
        min_prop_valve_opening: float,
    ) -> None:
        """Prepare nesting config.

        pwm max is equal to pwm scale
        all provided pwm per room are equal in pwm scale
        """
        self._logger = logging.getLogger(DOMAIN).getChild(name + ".nesting")
        self.operation_mode = operation_mode

        self.master_pwm = master_pwm
        self.master_pwm_scale = NESTING_MATRIX / self.master_pwm
        self.min_area = min_load * NESTING_MATRIX
        self.pwm_threshold = pwm_threshold / self.master_pwm * NESTING_MATRIX
        self.min_prop_valve_opening = min_prop_valve_opening * NESTING_MATRIX
        self.area_scale = NESTING_MATRIX / tot_area

        self.packed = []
        self.scale_factor = {}
        self.offset = {}
        self.cleaned_rooms = []
        self.area = []
        self.rooms = []
        self.pwm = []
        self.real_pwm = []
        self.start_time = []

        # proportional valves
        self.prop_pwm = []
        self.prop_area = []

    @property
    def load_on_off(self):
        """Nesting sum product of room area and pwm."""
        if len(self.pwm) == 0:
            return 0
        else:
            return sum([pwm_i * area_i for pwm_i, area_i in zip(self.area, self.pwm)])

    @property
    def load_prop(self):
        """Nesting proportional valves,sum product of room area and pwm."""
        if len(self.prop_pwm) == 0:
            return 0
        else:
            return sum(
                [pwm_i * area_i for pwm_i, area_i in zip(self.prop_pwm, self.prop_area)]
            )

    @property
    def load_total(self):
        """Nesting all rooms, sum product of room area and pwm."""
        return self.load_on_off + self.load_prop

    @property
    def area_avg_prop(self):
        """Continuous heat request from rooms with prop valves."""
        return self.load_prop / NESTING_MATRIX

    @property
    def max_all_pwm(self):
        """Max pwm of all rooms."""
        if self.pwm or self.pwm_prop:
            return max(*self.pwm, *self.prop_pwm)
        else:
            return 0

    @property
    def min_pwm_on_off(self):
        """Min pwm value of on-off valves."""
        if len(self.pwm) == 0:
            return 0

        none_zero_pwm = [i for i in self.pwm if i != 0]
        if len(none_zero_pwm) == 1:
            return 0

        return min(none_zero_pwm)

    @property
    def max_pwm_on_off(self):
        """Maxmimum pwm for on-off valves."""
        return max(self.pwm)

    @property
    def sum_pwm_on_off(self):
        """Sum pwm for on-off valves."""
        return sum(self.pwm)

    @property
    def min_area_on_off(self):
        """Minimum loading required by on-off valves."""
        return max(0, self.min_area - self.area_avg_prop)

    def pwm_for_continuous_mode(self):
        """Calculate nesting pwm for continous mode."""
        if self.min_area_on_off > 0:
            sum_pwm = 0
            load_area_rest = 0
            for i, a_i in enumerate(self.area):
                if a_i >= self.min_area_on_off:
                    sum_pwm += self.pwm[i]
                else:
                    load_area_rest = sum(
                        [a * b for a, b in zip(self.area[i:], self.pwm[i:])]
                    )
                    break

            # calc pwm duration for rest based on min_load compensated by prop valves
            pwm_rest = load_area_rest / self.min_area_on_off
            return_value = sum_pwm + pwm_rest

        # no minimum defined thus check if continuous is possible
        # when sum is less than pwm scale
        # use reduced pwm scale instead
        else:
            # no prop valves
            return_value = self.sum_pwm_on_off

        return return_value

    def pwm_for_balanced_mode(self):
        """Calculate nesting pwm for balanced mode."""
        # max area with pwm > 0
        max_area = max([a_i for i, a_i in enumerate(self.area) if self.pwm[i] != 0])

        # min domain nesting due to max area and max pwm
        load_envelope = max_area * self.max_pwm_on_off

        # shortest possible nesting
        min_pwm_nesting = self.max_pwm_on_off + self.min_pwm_on_off

        # pwm in case heat requirement with lowest continuous load
        # no nesting options
        if self.min_pwm_on_off == 0:
            return self.max_pwm_on_off

        # largest room is dominant: too little freedom for nesting
        # max area = peak load; max pwm is duration; if load_area is less nesting in pwm_max
        if load_envelope / self.load_on_off > NESTING_DOMINANCE:
            return self.max_pwm_on_off

        # min pwm extension would result in too low load
        if self.load_on_off / min_pwm_nesting < self.min_area_on_off > 0:
            return self.max_pwm_on_off

        # on-off needs nesting above minimum limit
        if self.min_area_on_off > 0:
            area_on_off = min(self.min_area_on_off, max_area)
            return_value = max(self.max_pwm_on_off, self.load_on_off / area_on_off)
        else:
            return_value = self.load_on_off / max_area

        return max(min_pwm_nesting, return_value)

    def pwm_for_minimum_mode(self):
        """Calculate nesting pwm for minimal on mode."""
        nested_pwm = self.load_on_off / NESTING_MATRIX
        return_value = max(self.max_pwm_on_off, nested_pwm)

        # pwm is lower than threshold without lower load
        if return_value < self.pwm_threshold and self.min_area_on_off == 0:
            return_value = self.pwm_threshold

        # pwm is lower than threshold with lower load
        elif return_value < self.pwm_threshold and self.min_area_on_off > 0:
            if (
                min(self.load_on_off / self.min_area_on_off, self.sum_pwm_on_off)
                > self.pwm_threshold
            ):
                return_value = self.pwm_threshold
            else:
                return_value = 0

        return return_value

    @property
    def pwm_for_nesting(self) -> int:
        """Determine size of pwm for nesting."""
        return_value = 0

        # no heat required
        if self.max_pwm_on_off == 0:
            return 0

        # # check if load requirement is too low
        if self.pwm_threshold > 0 and self.min_area_on_off > 0:
            if self.pwm_threshold * self.min_area > self.load_total:
                return 0

        # continuous operation possible due to prop valves
        if (
            self.operation_mode
            in [NestingMode.MASTER_CONTINUOUS, NestingMode.MASTER_BALANCED]
            and self.area_avg_prop > self.min_area > 0
        ):
            return NESTING_MATRIX

        # pwm as high as possible
        if self.operation_mode == NestingMode.MASTER_CONTINUOUS:
            return_value = self.pwm_for_continuous_mode()

        # balanced pwm duration versus heat requirement
        elif self.operation_mode == NestingMode.MASTER_BALANCED:
            return_value = self.pwm_for_balanced_mode()

        # max of pwm signals
        elif self.operation_mode == NestingMode.MASTER_MIN_ON:
            return_value = self.pwm_for_minimum_mode()

        # pwm below threshold
        if self.min_area_on_off > 0 and return_value < self.pwm_threshold > 0:
            return_value = 0

        # bound output minimal to max pwm and nesting matrix
        return_value = min(return_value, NESTING_MATRIX)

        # avoid too short off period when pwm threshold is specified
        if (
            self.pwm_threshold > 0
            and return_value != NESTING_MATRIX
            and return_value + self.pwm_threshold > NESTING_MATRIX
        ):
            return_value = NESTING_MATRIX - self.pwm_threshold

        return int(ceil(return_value))

    def max_nested_pwm(
        self, dt: float | None = None, forced_room: int | None = None
    ) -> float:
        """Get max length of self.packed."""
        if self.packed:
            max_packed = max([len(area_i) for lid in self.packed for area_i in lid])
        else:
            max_packed = 0

        if forced_room is not None:
            if (
                max_packed < dt + self.pwm[forced_room]
                and self.area[forced_room] < 0.15 * NESTING_MATRIX
            ):
                return max_packed
            else:
                return dt + self.pwm[forced_room]
        else:
            return max_packed

    def satelite_data(self, sat_data: dict) -> None:
        """Convert new satelite data to correct format."""
        # clear previous nesting
        self.area = []
        self.rooms = []
        self.pwm = []
        self.real_pwm = []
        self.scale_factor = {}
        new_data = {}

        self.prop_pwm = []
        self.prop_area = []

        if not sat_data:
            return

        new_data = {
            key: []
            for key in [CONF_AREA, ATTR_ROOMS, ATTR_ROUNDED_PWM, ATTR_SCALED_PWM]
        }

        for room, data in sat_data.items():
            # scale room pwm to master
            scale_factor = (
                # self.master_pwm / data[CONF_PWM_SCALE] * self.master_pwm_scale
                NESTING_MATRIX / data[CONF_PWM_SCALE]
            )
            self.scale_factor[room] = scale_factor

            # ignore proportional valves
            if data[CONF_PWM_DURATION] > 0:
                new_data[CONF_AREA].append(int(ceil(data[CONF_AREA] * self.area_scale)))
                new_data[ATTR_ROUNDED_PWM].append(
                    int(ceil(data[ATTR_CONTROL_PWM_OUTPUT] * scale_factor))
                )
                new_data[ATTR_SCALED_PWM].append(
                    data[ATTR_CONTROL_PWM_OUTPUT] * scale_factor
                )
                new_data[ATTR_ROOMS].append(room)
            else:
                self.prop_pwm.append(data[ATTR_CONTROL_PWM_OUTPUT] * scale_factor)
                self.prop_area.append(int(ceil(data[CONF_AREA] * self.area_scale)))

        if bool([a for a in new_data.values() if a == []]):
            return

        # area is constant and thereby sort on area gives
        # more constant routine order, largest room is expected
        # to require most heat thus dominant and most important
        # self.area, self.rooms, self.pwm, self.real_area, self.real_pwm = zip(
        self.area, self.rooms, self.pwm, self.real_pwm = (
            list(x)
            for x in zip(
                *sorted(
                    zip(
                        new_data[CONF_AREA],
                        new_data[ATTR_ROOMS],
                        new_data[ATTR_ROUNDED_PWM],
                        new_data[ATTR_SCALED_PWM],
                    ),
                    reverse=True,
                )
            )
        )

    def lid_segment(self, dt: int = None, forced_room: int | None = None) -> list:
        """Create a lid to store room nesting onto."""
        if forced_room is not None or self.packed:
            return [None] * self.max_nested_pwm(dt, forced_room)

        if self.packed:
            return [None] * self.max_nested_pwm()
        else:
            return [None] * self.pwm_for_nesting

    def create_lid(self, room_index: int, dt: int | None = None) -> None:
        """Create a 2d array with length of pwm_max and rows equal to area.

        fill/est array with room id equal to required pwm
        """
        if self.pwm_for_nesting == 0:
            return

        if dt is not None:
            # full nesting run
            time_shift = dt
            if (
                self.max_nested_pwm() < time_shift
                and self.area[room_index] < 0.15 * NESTING_MATRIX
            ):
                return
            forced_room = room_index
        else:
            # during middle of pwm loop
            forced_room = None
            time_shift = 0

        # newly created lid
        new_lid = np.array(
            copy.deepcopy(
                [
                    self.lid_segment(dt, forced_room)
                    for i in range(int(self.area[room_index]))
                ]
            ),
            dtype=object,
        )

        max_len = min(self.pwm[room_index] + time_shift, new_lid.shape[1])

        # fill new lid with current room pwm need
        new_lid[
            :,  # 0 : int(ceil(self.area[room_index])),
            time_shift:max_len,
        ] = self.rooms[room_index]
        self.packed.append(new_lid)

    def insert_room(self, room_index: int, dt: int = 0) -> bool:
        """Insert room to current nesting and return success."""
        nested = False
        options = []  # temp storage of each free spaces per room-area segment
        opt_arr = []  # temp storage of available array area-pwm sizes

        # loop over all stored lids and area segments and
        # store free pwm per area segment
        for i_l, lid_i in enumerate(self.packed):
            # add list for each lid to store free space
            opt_arr.append([])

            # loop over area segments (area size)
            for area_segment, _ in enumerate(lid_i):
                # loop over pwm (len = pwm max)
                try:
                    start_empty = list(lid_i[area_segment]).index(None)
                    start_empty = max(
                        start_empty, dt
                    )  # when run in the middle of pwm loop
                except ValueError:
                    start_empty = None

                if (
                    start_empty is not None
                    and list(reversed(lid_i[area_segment])).index(None)
                    == 0  # only when end is clear
                ):
                    options.append(
                        [
                            i_l,
                            area_segment,
                            len(lid_i[area_segment]) - start_empty,
                        ]
                    )

        # determine all possible free area-pwm size combinations
        if options:
            for lid_i, area_segment, pwm_i in options:
                if not opt_arr[lid_i]:
                    # first loop: store first area step
                    # [2] = 1 as start how many adjacent area segments have
                    # at least the same free pwm space
                    opt_arr[lid_i].append([lid_i, pwm_i, 1, area_segment])
                else:
                    # loop over all stored free space
                    # and check if current area segment has at least the same free pwm space
                    for arr in opt_arr[lid_i]:
                        # if area segment is from same lid and pwm_i is equal or larger then 'arr' pwm size
                        if pwm_i >= arr[1] and lid_i == arr[0]:
                            # increase adjacent segment counter
                            arr[2] += 1
                    # when pwm_i is larger than other stored free space options
                    # create a new options with pwm size equal to pwm_i
                    if max(np.array(opt_arr[lid_i], dtype=object)[:, 1]) < pwm_i:
                        opt_arr[lid_i].append([lid_i, pwm_i, 1, area_segment])

            # loop over all free area-pwm options and check if room area-pwm fits free spaces
            for lid_i in opt_arr:
                # loop over all free spaces
                for arr in lid_i:
                    # determine utilisation of free space for current room pwm
                    if (
                        arr[1] >= self.pwm[room_index]
                        and arr[2] >= self.area[room_index]
                    ):
                        arr.append(
                            (self.pwm[room_index] / arr[1])
                            * (self.area[room_index] / arr[2])
                        )
                    else:
                        # current room pwm does not fit this free space
                        arr.append(-1)

            # when free space(s) are available
            # determine best fitting options
            if opt_arr:
                final_opt = []
                lid_i = None
                y_width = None
                x_start = None

                # extract storage options
                # remove of options per lid and merge them
                for lid_id in opt_arr:
                    for store_option in lid_id:
                        if store_option[-1] != -1:
                            final_opt.append(store_option)

                if not final_opt:
                    return nested

                # when multiple storage options are present select the one with best fit
                if len(final_opt) > 1:
                    fill_list = np.array(final_opt, dtype=object)[:, 4]
                    best_index = fill_list.tolist().index(max(fill_list))
                    lid_i, y_width, _, x_start, _ = final_opt[best_index]

                # one option thus no choice
                elif len(final_opt[0]) == 5 and final_opt[0][-1] != -1:
                    lid_i, y_width, _, x_start, _ = final_opt[0]

                # nest best found free space option with current room area-pwm
                if lid_i is not None:
                    nested = True
                    # fill free space with room pwm
                    # select lid with free space
                    mod_lid = self.packed[lid_i]
                    lid_bckup = copy.deepcopy(mod_lid)

                    # fill area segments and pwm space with room id
                    try:
                        mod_lid[
                            x_start : x_start + self.area[room_index],
                            np.shape(mod_lid)[1] - y_width : np.shape(mod_lid)[1]
                            - y_width
                            + self.pwm[room_index],
                        ] = self.rooms[room_index]
                    except IndexError as e:
                        nested = False
                        mod_lid = lid_bckup
                        self._logger.error("in nesting %s", mod_lid)
                        self._logger.error("error: %s", str(e))
        return nested

    def nest_rooms(self, data: dict = None) -> None:
        """Nest the rooms to get balanced heat requirement."""
        self.start_time = time.time()
        self.packed = []
        self.cleaned_rooms = []
        self.offset = {}

        self.satelite_data(data)

        if self.area is None or all(pwm == 0 for pwm in self.pwm):
            return

        # loop through rooms
        # and create 2D arrays nested with room area-pwm
        # the maximum row size (pwm) is pwm_max
        # column size is variable and depends on nesting fit
        for i_r, _ in enumerate(self.rooms):
            if self.pwm[i_r] == 0:
                continue

            # first room in loop
            if not self.packed:
                self.create_lid(i_r)
            # check if current room area-pwm fits in any free space
            elif not self.insert_room(i_r):
                # no option thus create new lid to store room pwm
                self.create_lid(i_r)

    def distribute_nesting(self) -> None:
        """Shuffles packs to get best distribution."""
        if not self.packed:
            return

        if len(self.packed) == 1:
            return

        if self.operation_mode == NestingMode.MASTER_CONTINUOUS:
            # shuffle list to mix start and ends
            for i, lid_i in enumerate(self.packed):
                if i % 2:
                    for area_segment, _ in enumerate(lid_i):
                        lid_i[area_segment] = list(reversed(lid_i[area_segment]))

            # create list of variations
            option_list = list(
                itertools.product([False, True], repeat=len(self.packed))
            )

            # remove duplicate options
            len_options = len(option_list) - 1
            for i_o, opt in enumerate(reversed(option_list)):
                if [not elem for elem in opt] in option_list:
                    option_list.pop(len_options - i_o)

            # loop through all options
            for opt in option_list:
                test_set = copy.deepcopy(self.packed)

                # loop through lids and check if reverse is required
                for i_p, lid_i in enumerate(test_set):
                    if opt[i_p]:
                        for area_segment, _ in enumerate(lid_i):
                            lid_i[area_segment] = list(reversed(lid_i[area_segment]))

                # check load balance
                balance_result = self.nesting_balance(test_set)

                # check if balance is small enough
                if balance_result is not None:
                    if abs(balance_result) <= NESTING_BALANCE:
                        self.packed = test_set
                        self._logger.debug(
                            "finished time %.4f, balance %.4f",
                            time.time() - self.start_time,
                            balance_result,
                        )
                        return

        # balanced mode or min pwm
        else:
            # check current balance
            balance_result = self.nesting_balance(self.packed)
            if balance_result is not None:
                if abs(balance_result) <= NESTING_BALANCE:
                    return

            for lid_i in reversed(self.packed):
                for area_segment, _ in enumerate(lid_i):
                    lid_i[area_segment] = list(reversed(lid_i[area_segment]))

                # determine the equality over pwm
                balance_result = self.nesting_balance(self.packed)
                self._logger.debug(
                    "nesting balance %.4f",
                    balance_result,
                )

                if balance_result is not None:
                    if abs(balance_result) <= NESTING_BALANCE:
                        return

    def nesting_balance(self, test_set: list) -> float | None:
        """Get balance of areas over pwm signal."""
        cleaned_area = []
        if not test_set:
            return None

        for i, lid in enumerate(test_set):
            # loop over pwm
            for i_2, _ in enumerate(lid[0]):
                # last lid add space to store ..
                if i == 0:
                    cleaned_area.append(0)

                # extract unique rooms by fromkeys method
                rooms = list(dict.fromkeys(lid[:, i_2]))
                for room in rooms:
                    if room is not None:
                        room_area = self.area[self.rooms.index(room)] / self.area_scale
                        cleaned_area[i_2] += room_area

        if 0 in cleaned_area:
            return 1

        moment_area = 0
        for i, area in enumerate(cleaned_area):
            moment_area += i * area
        self._logger.debug("area distribution \n %s", cleaned_area)

        return (moment_area / sum(cleaned_area) - (len(cleaned_area) - 1) / 2) / len(
            cleaned_area
        )

    def get_nesting(self) -> dict:
        """Get offset per room with offset in satellite pwm scale."""
        len_pwm = self.max_nested_pwm()
        if len_pwm == 0:
            return {}

        self.offset = {}
        self.cleaned_rooms = [[] for _ in range(len_pwm)]
        for lid in self.packed:
            # loop over pwm
            # first check if some are at end
            # extract unique rooms by fromkeys method
            if len_pwm == NESTING_MATRIX:
                # self.operation_mode == NestingMode.MASTER_CONTINUOUS
                # and self.pwm_for_nesting == NESTING_MATRIX
                rooms = list(dict.fromkeys(lid[:, -1]))
                rooms = [r_i for r_i in rooms if r_i is not None]
                if not rooms:
                    continue
                for room in rooms:
                    self.cleaned_rooms[len_pwm - 1].append(room)
                    if room not in self.offset:
                        room_pwm = self.real_pwm[self.rooms.index(room)]
                        # offset in satellite pwm scale
                        self.offset[room] = (
                            NESTING_MATRIX - room_pwm
                        ) / self.scale_factor[room]

            # define offsets others
            for i_2 in range(lid.shape[1]):
                # last one already done
                if i_2 < len_pwm - 1:
                    # extract unique rooms by fromkeys method
                    rooms = list(dict.fromkeys(lid[:, i_2]))
                    rooms = [r_i for r_i in rooms if r_i is not None]
                    if not rooms:
                        continue
                    for room in rooms:
                        if room not in self.cleaned_rooms[i_2]:
                            self.cleaned_rooms[i_2].append(room)
                        if room not in self.offset:
                            self.offset[room] = i_2 / self.scale_factor[room]

        return self.offset

    def get_master_output(self) -> dict:
        """Control ouput (offset and pwm) for master."""
        end_time = 0
        end_time_prop = 0
        master_offset = None
        # nested rooms present
        if (
            self.cleaned_rooms is not None and len(self.cleaned_rooms) > 0
            # and self.rooms
        ):
            # loop over nesting to find start offset
            for pwm_i, rooms in enumerate(self.cleaned_rooms):
                if len(rooms) > 0 and master_offset is None:
                    master_offset = pwm_i

            # find max end time
            room_end_time = [0]
            for i_r, room in enumerate(self.rooms):
                if room in self.offset:
                    # take actual pwm into account and not rounded
                    # scale offsets back to NESTING_MATRIX domain
                    room_end = (
                        self.offset[room] * self.scale_factor[room] + self.real_pwm[i_r]
                    )
                    room_end_time.append(room_end)

            end_time = max(room_end_time)
            self._logger.debug("pwm on-off '%s'", end_time / self.master_pwm_scale)

        if master_offset is None:
            master_offset = 0

        # proportional valves require heat
        if self.load_prop > 0:
            # prop valves are full cycle open
            
            # too much load
            if self.load_total / NESTING_MATRIX > end_time - master_offset:
                end_time_prop = self.load_total / NESTING_MATRIX

            # continuous operation possible due to prop valves
            if (
                self.operation_mode
                in [NestingMode.MASTER_BALANCED, NestingMode.MASTER_CONTINUOUS]
                and self.area_avg_prop > self.min_area > 0
            ):
                end_time_prop = NESTING_MATRIX

            self._logger.debug(
                "pwm proportional '%s'", end_time_prop / self.master_pwm_scale
            )
            # assure sufficient opening
            end_time_prop = max(
                end_time_prop,
                self.pwm_threshold,
                self.min_prop_valve_opening,
            )

        end_time = max(end_time, end_time_prop) / self.master_pwm_scale
        master_offset /= self.master_pwm_scale
        self._logger.debug("master start '%s'; end '%s", master_offset, end_time)
        return {
            ATTR_CONTROL_OFFSET: master_offset,
            ATTR_CONTROL_PWM_OUTPUT: end_time - master_offset,
        }

    def remove_room(self, room: str) -> None:
        """Remove room from nesting when room changed hvac mode.

        room needs to be removed from:
        - cleaned_rooms, packed, offset
        """
        self._logger.debug("'%s' removed from nesting", room)

        # update packed
        for i, pack in enumerate(self.packed):
            pack = np.where(pack != room, pack, None)

            len_pack = len(pack) - 1
            for j, sub_area in enumerate(reversed(pack)):
                if (sub_area == None).all():  # noqa: E711
                    # new_pack = np.append(new_pack, [sub_area])
                    pack = np.delete(pack, len_pack - j, 0)

            self.packed[i] = copy.copy(pack)

        len_pack = len(self.packed) - 1

        # remove items from packed which are empty
        for i, pack in enumerate(reversed(self.packed)):
            if not pack.any():
                self.packed.pop(len_pack - i)

        # update cleaned rooms
        for i, lid in enumerate(self.cleaned_rooms):
            for ii, room_i in enumerate(lid):
                if room_i == room:
                    self.cleaned_rooms[i][ii] = ""

        self.cleaned_rooms = list(filter(None, self.cleaned_rooms))

        # update list with offsets
        _ = self.offset.pop(room, None)

    def nesting_bounds(self, room: str) -> list:
        """Find room in nesting."""
        index_start = None
        index_end = None
        free_space = 0
        # find current area
        for pack_i, lid in enumerate(self.packed):  # noqa: B007
            # loop over pwm
            for area_segment in lid:
                # find start and end nesting
                if room in area_segment:
                    index_start = list(area_segment).index(room)
                    index_end = len(area_segment) - list(reversed(area_segment)).index(
                        room
                    )

                    # check free space
                    if len(area_segment) > index_end:
                        if all(
                            pwm_i is None for pwm_i in area_segment[index_end + 1 :]
                        ):
                            free_space = len(area_segment) - index_end
                        else:
                            free_space = -1
                    break

        return pack_i, index_start, index_end, free_space

    def update_nesting(
        self,
        lid_index: int,
        room_index: int,
        index_start: int,
        index_end: int,
        free_space: int,
    ) -> None:
        """Udpate room nestign with update."""
        lid = self.packed[lid_index]
        old_pwm = index_end - index_start

        # extend when too short
        if old_pwm < self.pwm[room_index] and free_space > 0:
            if lid.shape[1] < self.max_nested_pwm():
                new_length = self.max_nested_pwm() - lid.shape[1]
                lid = np.lib.pad(
                    lid,
                    (
                        (0, 0),
                        (0, new_length),
                    ),
                    "constant",
                    constant_values=(None),
                )
            # fill new created area
            for area_segment in lid:
                max_fill = min(index_start + self.pwm[room_index], len(area_segment))
                if room_index in area_segment:
                    area_segment[index_start:max_fill] = room_index

        # when pwm has lowered
        elif old_pwm > self.pwm[room_index]:
            for area_segment in lid:
                if room_index in area_segment:
                    area_segment[
                        index_start + self.pwm[room_index] + 1 : len(area_segment)
                    ] = None

    def check_pwm(self, data: dict, dt: float = 0) -> None:
        """Check if nesting length is still right for each room."""
        self.satelite_data(data)
        self._logger.debug("check nesting @ %s of pwm loop", round(dt, 2))

        time_past = floor(dt * NESTING_MATRIX)

        # new satelite states result in no requirement
        if self.area is None:
            self.packed = []
            self.cleaned_rooms = []
            self.offset = {}
            return

        # remove nested rooms when not present
        if self.packed:
            current_rooms = list(self.offset.keys())
            for room in current_rooms:
                if room not in self.rooms:
                    self.remove_room(room)

        # check per room the nesting
        for room_i, room in enumerate(self.rooms):
            if self.pwm[room_i] == 0:
                self.remove_room(room)
                continue

            if not self.packed:
                self.create_lid(room_i, dt=time_past)
            else:
                # find room
                pack_i, index_start, index_end, free_space = self.nesting_bounds(room)

                # when the current room is not found
                if index_start is None or index_end is None:
                    if not self.insert_room(room_i, dt=time_past):
                        self.create_lid(room_i, dt=time_past)
                # modify existing nesting
                else:
                    self.update_nesting(
                        pack_i, room_i, index_start, index_end, free_space
                    )
