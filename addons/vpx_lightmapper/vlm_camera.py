#    Copyright (C) 2022  Vincent Bousquet
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>

import bpy
import array
import os
import pathlib
import gpu
import math
import mathutils
import functools
from gpu_extras.presets import draw_texture_2d
from gpu_extras.batch import batch_for_shader

from . import vlm_utils
from . import vlm_collections


def fit_camera(context, camera_object, camera_inclination, camera_layback, bake_col):
    """Update bake camera position based on its inclination, in order to fit the following constraints:
    - look at the center of the playfield
    - view all baked objects
    """
    camera_fov = camera_object.data.angle
    playfield_left = 0.0
    playfield_top = 0.0 
    global_scale = vlm_utils.get_global_scale(context)
    playfield_height = (context.scene.vlmSettings.playfield_height * global_scale) / (1.0625 / 50.0)
    playfield_width = (context.scene.vlmSettings.playfield_width * global_scale) / (1.0625 / 50.0)
    layback = mathutils.Matrix.Shear('XY', 4, (0, math.tan(math.radians(camera_layback) / 2)))
    camera_angle = math.radians(camera_inclination)
    camera_object.rotation_euler = mathutils.Euler((camera_angle, 0.0, 0.0), 'XYZ')
    camera_object.data.shift_x = 0
    camera_object.data.shift_y = 0
    view_vector = mathutils.Vector((0, math.sin(camera_angle), -math.cos(camera_angle)))
    aspect_ratio = 1.0
    for i in range(3): # iterations since it depends on the aspect ratio fitting which change after each computation
        # Compute the camera distance with the current aspect ratio
        camera_object.location = (playfield_left + 0.5 * playfield_width, -playfield_top -0.5 * playfield_height, 0)
        modelview_matrix = camera_object.matrix_basis.inverted()
        s = 1.0 / math.tan(camera_fov/2.0)
        sx = s if aspect_ratio > 1.0 else s/aspect_ratio
        sy = s if aspect_ratio < 1.0 else s*aspect_ratio
        min_dist = 0
        for obj in bake_col.all_objects:
            if obj.type == 'MESH': # and not obj.hide_get() and not obj.hide_render:
                bbox_corners = [modelview_matrix @ obj.matrix_world @ layback @ mathutils.Vector(corner) for corner in obj.bound_box]
                proj_x = map(lambda a: abs(sx * a.x + a.z), bbox_corners)
                proj_y = map(lambda a: abs(sy * a.y + a.z), bbox_corners)
                min_dist = max(min_dist, max(proj_x), max(proj_y))
        camera_object.location.y -= min_dist * view_vector.y
        camera_object.location.z -= min_dist * view_vector.z
        # adjust aspect ratio and compute camera shift to fill the render output
        modelview_matrix = camera_object.matrix_basis.inverted()
        projection_matrix = camera_object.calc_matrix_camera(context.evaluated_depsgraph_get())
        max_x = max_y = -10000000
        min_x = min_y = 10000000
        for obj in bake_col.all_objects:
            if obj.type == 'MESH': # and not obj.hide_get() and not obj.hide_render:
                bbox_corners = [projection_matrix @ modelview_matrix @ obj.matrix_world @ layback @ mathutils.Vector((corner[0], corner[1], corner[2], 1)) for corner in obj.bound_box]
                proj_x = [o for o in map(lambda a: a.x / a.w, bbox_corners)]
                proj_y = [o for o in map(lambda a: a.y / a.w, bbox_corners)]
                min_x = min(min_x, min(proj_x))
                min_y = min(min_y, min(proj_y))
                max_x = max(max_x, max(proj_x))
                max_y = max(max_y, max(proj_y))
        aspect_ratio = (max_x - min_x) / (max_y - min_y)
        render_size = vlm_utils.get_render_size(context)
        context.scene.render.resolution_x = int(render_size[1] * aspect_ratio)
        context.scene.render.resolution_y = render_size[1]
    # Center on render output
    camera_object.data.shift_x = 0.25 * (max_x + min_x)
    camera_object.data.shift_y = 0.25 * (max_y + min_y)
    
