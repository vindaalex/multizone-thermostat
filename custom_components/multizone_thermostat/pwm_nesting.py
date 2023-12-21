"""
Nesting of rooms by pwm and area size to get equal heat distribution
and determine when master needs to be operated
rooms switch delay are determined.
"""

import copy
import itertools
import logging
import time
from math import ceil, floor, sqrt

import numpy as np

from . import DOMAIN
from .const import (
    CONF_AREA,
    CONF_PWM_DURATION,
    CONF_PWM_SCALE,
    ATTR_CONTROL_PWM_OUTPUT,
    ATTR_CONTROL_OFFSET,
    MASTER_CONTINUOUS,
    MASTER_BALANCED,
    NESTING_BALANCE,
    NESTING_MATRIX,
)

ATTR_ROOMS = "rooms"
ATTR_SCALED_PWM = "scaled_pwm"
ATTR_ROUNDED_PWM = "rounded_pwm"


class Nesting:
    """Nest rooms by area size and pwm in order to get equal heat requirement"""

    def __init__(
        self, name, operation_mode, master_pwm, tot_area, min_load, pwm_threshold
    ) -> None:
        """
        pwm max is equal to pwm scale
        all provided pwm per room are equal in pwm scale
        """
        self._logger = logging.getLogger(DOMAIN).getChild(name + ".nesting")
        self.operation_mode = operation_mode

        self.master_pwm = master_pwm
        self.master_pwm_scale = NESTING_MATRIX / self.master_pwm
        self.min_load = min_load / self.master_pwm * NESTING_MATRIX
        self.pwm_limit = pwm_threshold / self.master_pwm * NESTING_MATRIX

        self.area_scale = NESTING_MATRIX / tot_area

        self.packed = []
        self.scale_factor = {}
        self.offset = {}
        self.cleaned_rooms = []
        self.area = None
        self.rooms = None
        self.pwm = None
        self.real_pwm = None
        self.start_time = None

    @property
    def get_pwm_max(self):
        """Determine size of pwm for nesting."""
        # max pwm of rooms
        pwm_max = max(self.pwm)
        if pwm_max == 0:
            return 0
        load_area = sum([a * b for a, b in zip(self.area, self.pwm)])

        if self.operation_mode == MASTER_CONTINUOUS:
            # check if load is above minimum
            if self.min_load > 0:
                sum_pwm = 0
                return_value = 0
                for i, a_i in enumerate(self.area):
                    if a_i >= self.min_load:
                        sum_pwm += self.pwm[i]
                    else:
                        load_area_rest = sum(
                            [a * b for a, b in zip(self.area[i:], self.pwm[i:])]
                        )
                        if load_area_rest / self.min_load >= NESTING_MATRIX - sum_pwm:
                            return_value = NESTING_MATRIX
                        else:
                            return_value = sum_pwm + load_area_rest / self.min_load
                        continue

                if return_value == 0 and sum_pwm > 0:
                    return_value = sum_pwm
                else:
                    # only small areas present
                    return_value = load_area / self.min_load

            # no minimum defined thus check if continuous is possible
            # when sum is less than pwm scale
            # # use reduced pwm scale instead
            elif sum(self.pwm) < NESTING_MATRIX:
                return_value = load_area / max(self.area)

            else:
                # full pwm size can be used
                return_value = NESTING_MATRIX

        # balanced pwm duration versus heat requirement
        elif self.operation_mode == MASTER_BALANCED:
            index_max_pwm = self.pwm.index(max(self.pwm))
            index_min_pwm = self.pwm.index(min([i for i in self.pwm if i != 0]))
            # index_max_area = self.area.index(max(self.area))
            index_max_area = self.area.index(
                max([a_i for i, a_i in enumerate(self.area) if self.pwm[i] != 0])
            )
            # equal pwm duration as capacity (=room area)
            pwm_sqr = max(sqrt(load_area), pwm_max)
            # pwm in case heat requirement with (25% of area or max room area)
            min_load = max(self.min_load, NESTING_MATRIX * MIN_MASTER_LOAD)
            # lowest continuous pwm
            pwm_low_load = max(load_area / max(min_load, *self.area), pwm_max)
            # load-area of room with max pwm
            load_area_pwm_max = self.area[index_max_pwm] * pwm_max
            # load-area of largest room
            load_area_area_max = self.area[index_max_area] * self.pwm[index_max_area]
            load_area_abs_max = self.area[index_max_area] * pwm_max

            # in case nesting could result in continous opening
            # and sufficient other than the 'load_area_pwm_max' rooms
            # require heat (enough options for nesting)
            if NESTING_MATRIX - pwm_low_load < self.pwm_limit:
                # full pwm size can be used
                return_value = NESTING_MATRIX
            else:
                return_value = pwm_sqr

                if (
                    # largest room is dominant: too little freedom for nesting
                    load_area_area_max > load_area * NESTING_DOMINANCE
                    # max area = peak load; max pwm is duration; if load_area is less nesting in pwm_max
                    or load_area_abs_max > load_area * NESTING_DOMINANCE
                    # min pwm extensino would result in too low load
                    or load_area / (pwm_max + self.pwm[index_min_pwm]) < min_load
                    # # sqr is too low for nesting
                    # or pwm_sqr < pwm_max
                ):
                    return_value = pwm_max
                elif (
                    pwm_sqr < pwm_max + self.pwm[index_min_pwm]
                    and pwm_max + self.pwm[index_min_pwm] < pwm_low_load
                ):
                    return_value = pwm_max + self.pwm[index_min_pwm]

                # if self.min_load > 0:
                #     return_value = min(return_value, load_area / self.min_load)

        # max of pwm signals
        elif self.operation_mode == MASTER_MIN_ON:
            nested_pwm = load_area / NESTING_MATRIX
            return_value = max(pwm_max, nested_pwm)

        # bound output minimal to max pwm and nesting matrix
        return_value = min(max(pwm_max, return_value), NESTING_MATRIX)
        return int(ceil(return_value))

    def max_nested_pwm(self, dt=None, forced_room=None):
        """get max length of self.packed"""
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

    def satelite_data(self, sat_data):
        """convert new satelite data to correct format"""
        # clear previous nesting
        self.area = None
        self.rooms = None
        self.pwm = None
        self.real_pwm = None
        self.scale_factor = {}
        new_data = {}

        if not sat_data:
            return

        new_data = {
            key: []
            for key in [CONF_AREA, ATTR_ROOMS, ATTR_ROUNDED_PWM, ATTR_SCALED_PWM]
        }

        for room, data in sat_data.items():
            scale_factor = (
                self.master_pwm / data[CONF_PWM_SCALE] * self.master_pwm_scale
            )
            self.scale_factor[room] = scale_factor

            # ignore proportional valves
            if data[CONF_PWM_DURATION] > 0:
                new_data[CONF_AREA].append(int(ceil(data[CONF_AREA] * self.area_scale)))
                new_data[ATTR_ROUNDED_PWM].append(
                    int(ceil(data[ATTR_CONTROL_PWM_OUTPUT] * scale_factor))
                )
                new_data[ATTR_SCALED_PWM].append(
                    (data[ATTR_CONTROL_PWM_OUTPUT] * scale_factor)
                )
                new_data[ATTR_ROOMS].append(room)

        if bool([a for a in new_data.values() if a == []]):
            return

        # area is constant and thereby sort on area gives
        # more constant routine order, largest room is expected
        # to require most heat thus dominant and most important
        # self.area, self.rooms, self.pwm, self.real_area, self.real_pwm = zip(
        self.area, self.rooms, self.pwm, self.real_pwm = [
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
        ]

    def lid_segment(self, dt=None, forced_room=None):
        # prepare pwm size for nesting
        if forced_room is not None or self.packed:
            return [None] * self.max_nested_pwm(dt, forced_room)
        elif self.packed:
            return [None] * self.max_nested_pwm()
        else:
            return [None] * self.get_pwm_max

    def create_lid(self, room_index, dt=None):
        """
        create a 2d array with length of pwm_max and rows equal to area
        fill/est array with room id equal to required pwm
        """
        if self.get_pwm_max == 0:
            return

        # check if function started from pwm check or full nesting run
        if dt is not None:
            time_shift = dt
            if (
                self.max_nested_pwm() < time_shift
                and self.area[room_index] < 0.15 * NESTING_MATRIX
            ):
                return
            forced_room = room_index
        else:
            forced_room = None
            time_shift = 0

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

    def insert_room(self, room_index, dt=0):
        """inserted room to current nesting"""
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
                    # end_empty = len(lid_i[area_segment]) - 1 - reversed(lid_i[area_segment]).index(None)
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
                        # TODO: store only feasible skip -1
                        final_opt.append(store_option)

                # when multiple storage options are present select the one with best fit
                if len(final_opt) > 1:
                    fill_list = np.array(final_opt, dtype=object)[:, 4]
                    best_index = fill_list.tolist().index(max(fill_list))
                    if final_opt[best_index][-1] != -1:
                        # best option
                        lid_i, y_width, _, x_start, _ = final_opt[best_index]

                # one option thus no choice
                else:
                    # check if it is a proper fit
                    if len(final_opt[0]) == 5 and final_opt[0][-1] != -1:
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
                        # max_pwm = min(self.get_pwm_max, np.shape(mod_lid)[1])
                        # for area_i in range(self.area[room_index]):
                        #     for pwm_i in range(self.pwm[room_index]):
                        mod_lid[
                            x_start : x_start + self.area[room_index],
                            np.shape(mod_lid)[1] - y_width : np.shape(mod_lid)[1]
                            - y_width
                            + self.pwm[room_index],
                        ] = self.rooms[room_index]
                    except IndexError as e:
                        nested = False
                        mod_lid = lid_bckup
                        # self._logger.error(f"failed nesting {self.rooms[room_index]} area {area_i} of {self.area[room_index]} with pwm {pwm_i} of {self.pwm[room_index]} in shape {mod_lid.shape} (offs {offset}; xstart {x_start}; pwmmax {self.get_pwm_max}; y_width {y_width}")
                        self._logger.error(f"in nesting {mod_lid}")
                        self._logger.error(f"error: {str(e)}")
        return nested

    def nest_rooms(self, data=None):
        """Nest the rooms to get balanced heat requirement"""
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
            # first room in loop
            if not self.packed:
                self.create_lid(i_r)

            else:
                # check if current room area-pwm fits in any free space
                if not self.insert_room(i_r):
                    # no option thus create new lid to store room pwm
                    self.create_lid(i_r)

    def distribute_nesting(self):
        """
        reverse packs to get best distribution
        """
        if self.packed:
            if len(self.packed) > 1:
                if self.operation_mode == MASTER_CONTINUOUS:
                    for i, lid_i in enumerate(self.packed):
                        if i % 2:
                            for area_segment, _ in enumerate(lid_i):
                                lid_i[area_segment] = list(
                                    reversed(lid_i[area_segment])
                                )

                    # create list of variations
                    option_list = list(
                        itertools.product([False, True], repeat=len(self.packed))
                    )
                    # remove mirrored options
                    len_options = len(option_list) - 1
                    for i_o, opt in enumerate(reversed(option_list)):
                        if [not elem for elem in opt] in option_list:
                            option_list.pop(len_options - i_o)

                    # loop through all options
                    for i_o, opt in enumerate(option_list):
                        test_set = copy.deepcopy(self.packed)
                        for i_p, lid_i in enumerate(test_set):
                            if opt[i_p]:
                                for area_segment, _ in enumerate(lid_i):
                                    lid_i[area_segment] = list(
                                        reversed(lid_i[area_segment])
                                    )
                        balance_result = self.nesting_balance(test_set)
                        if balance_result is not None:
                            if abs(balance_result) <= NESTING_BALANCE:
                                self.packed = test_set
                                self._logger.debug(
                                    "finished time %.4f, balance %.4f",
                                    time.time() - self.start_time,
                                    balance_result,
                                )
                                return
                else:
                    balance_result = self.nesting_balance(self.packed)
                    if balance_result is not None:
                        if abs(balance_result) <= NESTING_BALANCE:
                            # self.packed = test_set
                            # self._logger.debug(
                            #     "finished time %.4f", time.time() - self.start_time
                            # )
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
                                # self.packed = test_set
                                return

    def nesting_balance(self, test_set):
        """get balance of areas over pwm signal"""
        cleaned_area = []
        if test_set:
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
                            room_area = (
                                self.area[self.rooms.index(room)] / self.area_scale
                            )
                            cleaned_area[i_2] += room_area

            if 0 in cleaned_area:
                return 1

            moment_area = 0
            for i, area in enumerate(cleaned_area):
                moment_area += i * area
            self._logger.debug("area distribution \n %s", cleaned_area)
            return (
                moment_area / sum(cleaned_area) - (len(cleaned_area) - 1) / 2
            ) / len(cleaned_area)
        else:
            return None

    def get_nesting(self):
        """get offset per room with offset in satelite pwm scale"""
        len_pwm = self.max_nested_pwm()
        if len_pwm == 0:
            return {}

        self.offset = {}
        self.cleaned_rooms = [[] for _ in range(len_pwm)]
        for lid in self.packed:
            # loop over pwm
            # first check if some are at end
            # extract unique rooms by fromkeys method
            if (
                self.operation_mode == MASTER_CONTINUOUS
                and self.get_pwm_max == NESTING_MATRIX
            ):
                rooms = list(dict.fromkeys(lid[:, -1]))
                for room in rooms:
                    if room is not None:
                        self.cleaned_rooms[len_pwm - 1].append(room)
                        if room not in self.offset:
                            room_pwm = self.real_pwm[self.rooms.index(room)]
                            self.offset[room] = (
                                len_pwm - room_pwm
                            ) / self.scale_factor[room]

            for i_2, _ in enumerate(lid[0]):
                # last one already done
                if i_2 < len_pwm - 1:
                    # extract unique rooms by fromkeys method
                    rooms = list(dict.fromkeys(lid[:, i_2]))
                    for room in rooms:
                        if room is not None:
                            if room not in self.cleaned_rooms[i_2]:
                                self.cleaned_rooms[i_2].append(room)
                            if room not in self.offset:
                                self.offset[room] = i_2 / self.scale_factor[room]

        return self.offset

    def get_master_output(self):
        """control ouput (offset and pwm) for master"""
        if (
            self.cleaned_rooms is not None
            and len(self.cleaned_rooms) > 0
            and self.rooms
        ):
            master_offset = None
            for pwm_i, rooms in enumerate(self.cleaned_rooms):
                if len(rooms) > 0 and master_offset is None:
                    master_offset = pwm_i / self.master_pwm_scale
                # elif rooms:
                #     self._logger.warning("Nested satelites include multiple on-off loops in single pwm loop : '%s'", self.cleaned_rooms)
            # find max end time
            end_time = 0
            if len(self.cleaned_rooms[-1]) > 0:
                end_time = self.get_pwm_max / self.master_pwm_scale
            else:
                for i_r, room in enumerate(self.rooms):
                    if room in self.offset:
                        room_end_time = (
                            self.offset[room] * self.scale_factor[room]
                            + self.real_pwm[i_r]
                        ) / self.master_pwm_scale

                        end_time = max(end_time, room_end_time)

                # end_time = min(end_time, self.get_pwm_max / self.master_pwm_scale)
                end_time = min(
                    end_time, len(self.cleaned_rooms) / self.master_pwm_scale
                )

            if master_offset is None:
                return {
                    ATTR_CONTROL_OFFSET: 0,
                    ATTR_CONTROL_PWM_OUTPUT: 0,
                }
            else:
                return {
                    ATTR_CONTROL_OFFSET: master_offset,
                    ATTR_CONTROL_PWM_OUTPUT: end_time - master_offset,
                }
        else:
            return {
                ATTR_CONTROL_OFFSET: 0,
                ATTR_CONTROL_PWM_OUTPUT: 0,
            }

    def remove_room(self, room):
        """remove room from nesting when room changed hvac mode"""
        self._logger.debug("'%s' removed from nesting", room)
        for i, pack in enumerate(self.packed):
            pack = np.where(pack != room, pack, None)

            len_pack = len(pack) - 1
            for j, sub_area in enumerate(reversed(pack)):
                if (sub_area == None).all():
                    # new_pack = np.append(new_pack, [sub_area])
                    pack = np.delete(pack, len_pack - j, 0)

            self.packed[i] = copy.copy(pack)

        len_pack = len(self.packed) - 1
        for i, pack in enumerate(reversed(self.packed)):
            if not pack.any():
                self.packed.pop(len_pack - i)

        # self.cleaned_rooms = np.where(
        #     self.cleaned_rooms != room, self.cleaned_rooms, None
        # )
        for i, lid in enumerate(self.cleaned_rooms):
            for ii, room_i in enumerate(lid):
                if room_i == room:
                    self.cleaned_rooms[i][ii] = ""
        self.cleaned_rooms = list(filter(None, self.cleaned_rooms))

        if self.rooms:
            if room in self.rooms:
                self.rooms.remove(room)

        self.offset = np.where(self.offset != room, self.offset, None)

    def check_pwm(self, data, dt=0):
        """
        check if nesting length is still right for each room
        """
        self.satelite_data(data)

        time_past = floor(dt * NESTING_MATRIX)

        # new satelite states result in no requirement
        if self.area is None:
            self.packed = []
            self.cleaned_rooms = []
            self.offset = {}
            return

        # remove nested rooms when not present
        current_rooms = list(self.get_nesting().keys())
        for room in current_rooms:
            if room not in self.rooms:
                self.remove_room(room)

        # check per room the nesting
        for i, room_i in enumerate(self.rooms):
            if self.pwm[i] == 0:
                self.remove_room(room_i)
            else:
                index_start = None
                index_end = None
                if self.packed:
                    # find current area
                    for lid in self.packed:
                        # loop over pwm
                        if any(room_i in lid_i for lid_i in lid):
                            # found area in current pack
                            for area_segment in lid:
                                # find start and end nesting
                                if room_i in area_segment:
                                    index_start = list(area_segment).index(room_i)
                                    index_end = len(area_segment) - list(
                                        reversed(area_segment)
                                    ).index(room_i)

                                    free_space = 0
                                    if len(area_segment) > index_end:
                                        if all(
                                            pwm_i is None
                                            for pwm_i in area_segment[index_end + 1 :]
                                        ):
                                            free_space = len(area_segment) - index_end
                                        else:
                                            free_space = -1
                                    break

                            # modify existing nesting
                            if index_start is not None and index_end is not None:
                                old_pwm = index_end - index_start
                                # extend when too short
                                if old_pwm < self.pwm[i] and free_space > 0:
                                    if lid.shape[1] < self.max_nested_pwm():
                                        new_length = (
                                            self.max_nested_pwm() - lid.shape[1]
                                        )
                                        lid = np.lib.pad(
                                            lid,
                                            (
                                                (0, 0),
                                                (0, new_length),
                                            ),
                                            "constant",
                                            constant_values=(None),
                                        )
                                    for area_segment in lid:
                                        max_fill = min(
                                            index_start + self.pwm[i], len(area_segment)
                                        )
                                        if room_i in area_segment:
                                            area_segment[
                                                # index_start : index_end - difference
                                                index_start:max_fill
                                            ] = room_i
                                elif old_pwm > self.pwm[i]:
                                    if room_i in area_segment:
                                        area_segment[
                                            index_start + self.pwm[i] + 1 : len(
                                                area_segment
                                            )
                                        ] = None

                    # when the current room is not found
                    if index_start is None or index_end is None:
                        if not self.insert_room(i, dt=time_past):
                            self.create_lid(i, dt=time_past)
                else:
                    self.create_lid(i, dt=time_past)
