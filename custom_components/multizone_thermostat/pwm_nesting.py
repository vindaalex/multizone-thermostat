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

    def __init__(self, name, operation_mode, master_pwm, tot_area, min_load):
        """
        pwm max is equal to pwm scale
        all provided pwm per room are equal in pwm scale
        """
        self._logger = logging.getLogger(DOMAIN).getChild(name + ".nesting")
        self.operation_mode = operation_mode
        self.min_load = min_load
        self.master_pwm = master_pwm
        self.master_pwm_scale = NESTING_MATRIX / self.master_pwm
        self.master_area = tot_area
        self.area_scale = NESTING_MATRIX / tot_area

        self.packed = []
        self.scale_factor = {}
        self.offset = {}
        self.cleaned_rooms = []
        self.lid_segment = None
        self.area = None
        self.rooms = None
        self.pwm = None
        self.real_pwm = None
        self.start_time = None

    @property
    def get_pwm_max(self):
        """determine size of pwm for nesting"""
        # NOTE: what would be best method for pwm size for nesting?
        load_area = sum([a * b for a, b in zip(self.area, self.pwm)])
        if self.operation_mode == MASTER_CONTINUOUS:
            # check if load is above minimum
            if self.min_load > 0:
                # self.area and pwm are scaled already
                if load_area / NESTING_MATRIX >= self.min_load * NESTING_MATRIX:
                    return_value = NESTING_MATRIX
                else:
                    return_value = load_area / (self.min_load * NESTING_MATRIX)

            # no minimum defined thus check if continuous is possible
            # when sum is less than pwm scale
            # # use reduced pwm scale instead
            elif sum(self.pwm) < NESTING_MATRIX:
                return_value = load_area / max(self.area)

            else:
                # full pwm size can be used
                return_value = NESTING_MATRIX

        elif self.operation_mode == MASTER_BALANCED:
            return_value = sqrt(load_area)
            if self.min_load > 0:
                return_value = min(
                    return_value, load_area / (self.min_load * NESTING_MATRIX)
                )

        # max of pwm signals
        else:
            # nested_pwm = load_area / max(self.area)
            return_value = min(NESTING_MATRIX, max(self.pwm))

        return int(ceil(return_value))

    def satelite_data(self, sat_data):
        """convert new satelite data to correct format"""
        # clear previous nesting

        self.area = None
        self.rooms = None
        self.pwm = None
        self.real_pwm = None
        self.scale_factor = {}

        new_data = {}
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
        self.area, self.rooms, self.pwm, self.real_pwm = zip(
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

        # prepare pwm size for nesting
        self.lid_segment = [None] * self.get_pwm_max

    def create_lid(self, room_index, offset=0):
        """
        create a 2d array with length of pwm_max and rows equal to area
        fill/est array with room id equal to required pwm
        """
        new_lid = np.array(
            copy.deepcopy(
                [self.lid_segment for i in range(int(self.area[room_index]))]
            ),
            dtype=object,
        )
        # fill new lid with current room pwm need
        new_lid[
            int(floor(offset)) : int(ceil(self.area[room_index] + offset)),
            int(floor(offset)) : int(ceil(self.pwm[room_index] + offset)),
        ] = self.rooms[room_index]
        self.packed.append(new_lid)

    def nest_rooms(self, data=None):
        """Nest the rooms to get balanced heat requirement"""
        self.start_time = time.time()

        if data:
            self.packed = []
            self.cleaned_rooms = []
            self.satelite_data(data)

        if self.area is None or all(pwm == 0 for pwm in self.pwm):
            return

        # storage of all lids
        self.packed = []

        # loop through rooms
        # and create 2D arrays nested with room area-pwm
        # the maximum row size (pwm) is pwm_max
        # column size is variable and depends on nesting fit
        for i_r, room in enumerate(self.rooms):
            # first room in loop
            if not self.packed:
                self.create_lid(i_r)

            else:
                # check if current room area-pwm fits in any free space
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
                        except ValueError:
                            start_empty = None

                        if (
                            start_empty is not None
                            and list(reversed(lid_i[area_segment])).index(None) == 0
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
                if not options:
                    self.create_lid(i_r)
                else:
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
                            if (
                                max(np.array(opt_arr[lid_i], dtype=object)[:, 1])
                                < pwm_i
                            ):
                                opt_arr[lid_i].append([lid_i, pwm_i, 1, area_segment])

                    # loop over all free area-pwm options and check if room area-pwm fits free spaces
                    for lid_i in opt_arr:
                        # loop over all free spaces
                        for arr in lid_i:
                            # determine utilisation of free space for current room pwm
                            if arr[1] >= self.pwm[i_r] and arr[2] >= self.area[i_r]:
                                arr.append(
                                    (self.pwm[i_r] / arr[1]) * (self.area[i_r] / arr[2])
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
                            # fill free space with room pwm
                            # select lid with free space
                            mod_lid = self.packed[lid_i]

                            # fill area segments and pwm space with room id
                            for area_i in range(self.area[i_r]):
                                for pwm_i in range(self.pwm[i_r]):
                                    mod_lid[
                                        x_start + area_i,
                                        self.get_pwm_max - y_width + pwm_i,
                                    ] = room

                        # no feasible option thus create new lid to store room pwm
                        else:
                            self.create_lid(i_r)

                    # no option thus create new lid to store room pwm
                    else:
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
        if self.packed:
            self.offset = {}
            len_pwm = len(self.packed[0][0])
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

        else:
            return None

    def get_master_output(self):
        """control ouput (offset and pwm) for master"""
        if self.cleaned_rooms is not None and len(self.cleaned_rooms) > 0:
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

                end_time = min(end_time, self.get_pwm_max / self.master_pwm_scale)

            if master_offset is None:
                return [0, 0]
            else:
                return {
                    ATTR_CONTROL_OFFSET: master_offset,
                    ATTR_CONTROL_PWM_OUTPUT: end_time - master_offset,
                }
        else:
            return [0, 0]

    def remove_room(self, room):
        """remove room from nesting when room changed hvac mode"""
        self._logger.debug("'%s' removed from nesting", room)
        for i, pack in enumerate(self.packed):
            self.packed[i] = np.where(pack != room, pack, None)
            if self.packed[i].all() is None:
                self.packed.pop(i)
        self.cleaned_rooms = np.where(
            self.cleaned_rooms != room, self.cleaned_rooms, None
        )
        self.offset = np.where(self.offset != room, self.offset, None)

    def check_pwm(self, data, current_offset=0):
        """
        check if nesting length is still right for each room
        new added rooms are ignored and will be nested in the next control loop
        """
        # nested data is present
        if data:
            self.satelite_data(data)

        # new satelite states result in no requirement
        if self.area is None:
            self.packed = []
            return

        # check per area the nesting
        for i, room_i in enumerate(self.rooms):
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
                                break

                        # modify existing nesting
                        if index_start is not None and index_end is not None:
                            # difference = int((index_end - index_start) - self.pwm[i])
                            # if difference != 0:
                            for area_segment in lid:
                                if room_i in area_segment:
                                    area_segment[
                                        # index_start : index_end - difference
                                        index_start : min(
                                            int(ceil(index_start + self.pwm[i])),
                                            NESTING_MATRIX - 1,
                                        )
                                    ] = room_i

                if index_start is None or index_end is None:
                    self.create_lid(
                        i, current_offset / self.master_pwm * self.master_pwm_scale
                    )
            else:
                self.create_lid(
                    i, current_offset / self.master_pwm * self.master_pwm_scale
                )
