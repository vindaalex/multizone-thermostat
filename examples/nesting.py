import copy
import numpy as np
from scipy import ndimage
import itertools

data = {
    'rooms': ['wk', 'sl1', 'sl2', 'sl3', 'bk', 'sl4'],
    'area': [6, 4, 3, 2, 1,5],
    'pwm': [3, 2, 1, 8, 5,5],
}

pwm_max = 10

area, rooms, pwm = zip(*sorted(zip(data['area'], data['rooms'], data['pwm']),reverse=True))

# lid = {
#     'room':[],
#     'area':[],
#     'pwm':[],
# }

# packed = []

# for i, room in enumerate(rooms):
#     print(room, area[i])
#     if not packed:
#         packed.append(copy.deepcopy(lid))
#         packed[-1]['room'].append(room)
#         packed[-1]['area'].append(area[i])
#         packed[-1]['pwm'].append(pwm[i])
#         packed[-1]['start'].append(0)
#     else:
#         lid_option = {}
#         #loop through lids to check for empty spots
#         for n_lid, lid_item in enumerate(packed): 
#             # check each group for space
#             start_time = []
#             end_time = []
#             for n_pwm, _ in enumerate(lid_item['pwm']):
#                 start_time.append(lid_item['start'][n_pwm])
#                 end_time.append(lid_item['start'][n_pwm] + lid_item['pwm'][n_pwm])
#                 end_time.append(lid_item['start'][n_pwm] + lid_item['pwm'][n_pwm])

#                 filled_pwm = lid_item['pwm'][:-n_pwm or None]
#                 if n_pwm == 0:
#                     area_end = 0
#                 else:
#                     area_end = max(lid_item['area'][n_pwm:])
#                 max_area = max(lid_item['area']) - area_end
#                 #TODO: area not limiting when slightly larger or when filled pwm is small?
#                 #TODO: loop through all gaps
#                 if pwm_max - sum(filled_pwm) > pwm[i] and area[i] < max_area:
#                     fill_factor = max_area * (pwm[i] / (pwm_max - sum(lid_item['pwm'])))
#                     lid_option[str(n_lid)] = [n_pwm, fill_factor]
        
#         if not lid_option:
#             packed.append(copy.deepcopy(lid))
#             packed[-1]['room'].append(room)
#             packed[-1]['area'].append(area[i])
#             packed[-1]['pwm'].append(pwm[i])
#             packed[-1]['start'].append(0)      
#         else:
#             lid_id = list(lid_option.values())[0]
#             fill_factor = list(lid_option.values())[1]

#             area, rooms, pwm = zip(*sorted(zip(data['pwm'], data['rooms'], data['pwm']),reverse=True))


# print(packed)
# # lst = lst[:-n_pwm or None]


packed = []
lid = [None] * pwm_max

for i, room in enumerate(rooms):
    print(room, area[i])
    if not packed:
        new_lid = np.array(copy.deepcopy([lid for i in range(int(area[i]))]))
        new_lid[0:area[i], 0:pwm[i]] = room
        packed.append(new_lid)
    else:
        options = []
        opt_arr = []
        final_opt = []
        for li, lid_i in enumerate(packed):
            opt_arr.append([])
            # label, num_label = ndimage.label(lid_i == None)
            # object_slices =  ndimage.find_objects(label)
            # for slice in object_slices:
            #     size = np.shape(lid_i[slice])
            #     if size[0] > area[i] and size[1] > pwm[i]:
            #         start_loc = ndimage.maximum_position(label)
            #         filling = (area[i] / size[0]) * (pwm[i] / size[1])
            #         options.append([filling, li, start_loc])
            
            # loop through rows (area size)
            for xi in range(len(lid_i)):
                # loop through values (len = pwm)
                for yi, y in enumerate(reversed(lid_i[xi])):
                    if y != None:
                        options.append([li, xi, yi])
                        break
            
        #check available array sizes
        for li, x, y in options:
            if not opt_arr[li]:
                opt_arr[li].append([li, y, 1, x])
            else:
                for arr in opt_arr[li]:
                    if y >= arr[1] and li == arr[0]:
                        arr[2] +=1
                if max(np.array(opt_arr[li])[:,1]) < y:
                    opt_arr[li].append([li, y, 1, x])

        for lid_i in opt_arr:
            for arr in lid_i:
                if arr[1] >= pwm[i] and arr[2] >= area[i]:
                    arr.append((pwm[i] / arr[1]) * (area[i] / arr[2]))
                else:
                    arr.append(-1)
       
        if opt_arr:
            for arr in opt_arr:
                for opt in arr:
                    final_opt.append(opt)

            fill_fact = li = y_width = row_start = x_start = None
            if len(final_opt) > 1:
                fill_list = np.array(final_opt)[:,4]
                best_index = fill_list.tolist().index(max(fill_list))
                if final_opt[best_index][-1] != -1:
                    li, y_width, rows, x_start, fill_fact = final_opt[best_index]
            else:
                if len(final_opt[0]) == 5 and final_opt[-1] != -1:
                    li, y_width, rows, x_start, fill_fact = final_opt[0]

            if li != None:
                mod_lid = packed[li]
                for x in range(area[i]):
                    for y in range(pwm[i]):
                        mod_lid[x_start + x, pwm_max - y_width + y] = room

            # new_room = [room] * area[i]
            # mod_lid[start_loc[1]:area[i],start_loc[0]:pwm[i]] = [new_room for i in range(pwm[i])] 
            else:
                new_lid = np.array(copy.deepcopy([lid for i in range(int(area[i]))]))
                new_lid[0:area[i], 0:pwm[i]] = room
                packed.append(new_lid)
        else:
            new_lid = np.array(copy.deepcopy([lid for i in range(int(area[i]))]))
            new_lid[0:area[i], 0:pwm[i]] = room
            packed.append(new_lid)

for i,p in enumerate(packed):
    if i % 2 != 0:
        for pi, row in enumerate(p):
            p[pi] = list(reversed(p[pi]))

print('------------------------')
conv_packed = []
for i, lid in enumerate(packed):
    print('lid ', i)
    print(lid)
    conv_packed.append([])
    # loop through columns
    for i2, _ in enumerate(lid[0]):
        conv_packed[-1].append([])
        # unique rooms
        rooms = list(dict.fromkeys(lid[:,i2]))
        # areas of rooms
        for i3, item in enumerate(rooms):
            if item != None:
                index = data['rooms'].index(item)
                conv_packed[-1][i2].append(data['area'][index])

summed_packed=[0] * pwm_max
print('------------------------')
for i, lid in enumerate(conv_packed):
    print('lid ',i, ' areas')
    print(lid)
    for i2, areas in enumerate(lid):
        summed_packed[i2] += sum(areas)

print('summed areas')
print(summed_packed)

     