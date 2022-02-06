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

bl_info = {
    "name": "Visual Pinball X Light Mapper",
    "author": "Vincent Bousquet",
    "version": (0, 0, 1),
    "blender": (3, 0, 0),
    "description": "Import/Export Visual Pinball X tables and perform automated light baking",
    "warning": "Requires installation of dependencies",
    "wiki_url": "",
    "tracker_url": "",
    "support": "COMMUNITY",
    "category": "Import-Export"}

import bpy
import os
import sys
import glob
import time
import math
import mathutils
import importlib
import subprocess
from bpy_extras.io_utils import (ImportHelper, axis_conversion)
from bpy.props import (StringProperty, BoolProperty, IntProperty, FloatProperty, FloatVectorProperty, EnumProperty, PointerProperty)
from bpy.types import (Panel, Menu, Operator, PropertyGroup, AddonPreferences, Collection)
from rna_prop_ui import PropertyPanel

# TODO
# - Add an option for size factor between render and texture (or split render / texture size option)


# Use import.reload for all submodule to allow iterative development using bpy.ops.script.reload()
if "vlm_dependencies" in locals():
    importlib.reload(vlm_dependencies)
else:
    from . import vlm_dependencies
if "vlm_collections" in locals():
    importlib.reload(vlm_collections)
else:
    from . import vlm_collections
if "vlm_utils" in locals():
    importlib.reload(vlm_utils)
else:
    from . import vlm_utils
if "vlm_uvpacker" in locals():
    importlib.reload(vlm_uvpacker)
else:
    from . import vlm_uvpacker
if "vlm_occlusion" in locals():
    importlib.reload(vlm_occlusion)
else:
    from . import vlm_occlusion
if "vlm_camera" in locals():
    importlib.reload(vlm_camera)
else:
    from . import vlm_camera

# Only load submodules that have external dependencies if they are satisfied
dependencies = (
    # OLE lib: https://olefile.readthedocs.io/en/latest/Howto.html
    vlm_dependencies.Dependency(module="olefile", package=None, name=None),
    # Pillow image processing lib: https://pillow.readthedocs.io/en/stable/
    vlm_dependencies.Dependency(module="PIL", package="Pillow", name="Pillow"),
    # Win32 native lib: https://github.com/mhammond/pywin32
    vlm_dependencies.Dependency(module="win32crypt", package="pywin32", name=None),
)
dependencies_installed = vlm_dependencies.import_dependencies(dependencies)
if dependencies_installed:
    if "biff_io" in locals():
        importlib.reload(biff_io)
    else:
        from . import biff_io
    if "vlm_import" in locals():
        importlib.reload(vlm_import)
    else:
        from . import vlm_import
    if "vlm_export" in locals():
        importlib.reload(vlm_export)
    else:
        from . import vlm_export
    if "vlm_baker" in locals():
        importlib.reload(vlm_baker)
    else:
        from . import vlm_baker


class VLM_Scene_props(PropertyGroup):
    # Importer options
    light_size: FloatProperty(name="Light Size", description="Light size factor from VPX to Blender", default = 5.0)
    light_intensity: FloatProperty(name="Light Intensity", description="Light intensity factor from VPX to Blender", default = 250.0)
    insert_size: FloatProperty(name="Insert Size", description="Inserts light size factor from VPX to Blender", default = 0.0)
    insert_intensity: FloatProperty(name="Insert Intensity", description="Insert intensity factor from VPX to Blender", default = 25.0)
    process_inserts: BoolProperty(name="Convert inserts", description="Detect inserts and converts them", default = True)
    use_pf_translucency_map: BoolProperty(name="Translucency Map", description="Generate a translucency map for inserts", default = True)
    process_plastics: BoolProperty(name="Convert plastics", description="Detect plastics and converts them", default = True)
    bevel_plastics: FloatProperty(name="Bevel plastics", description="Bevel converted plastics", default = 1.0)
    camera_inclination: FloatProperty(name="Inclination", description="Camera inclination", default = 15.0, update=vlm_camera.camera_inclination_update)
    camera_layback: FloatProperty(name="Layback", description="Camera layback", default = 35.0, update=vlm_camera.camera_inclination_update)
    layback_mode: EnumProperty(
        items=[
            ('disable', 'Disable', 'Disable layback', '', 0),
            ('deform', 'Deform', 'Apply layback to geometry. This breaks reflection/refraction', '', 1),
            ('camera', 'Camera', 'Apply layback to camera.', '', 2)
        ],
        name='Layback mode',
        default='camera', 
        update=vlm_camera.camera_inclination_update
    )
    # Baker options
    last_bake_step: EnumProperty(
        items=[
            ('unstarted', 'Unstarted', '', '', 0),
            ('groups', 'Groups', '', '', 1),
            ('renders', 'Rendered', '', '', 2),
            ('meshes', 'Meshes', '', '', 3),
            ('packmaps', 'Packmaps', '', '', 4),
        ],
        name='Last Bake Step',
        default='unstarted'
    )
    tex_size: EnumProperty(
        items=[
            ('256', '256', '256x256', '', 256),
            ('512', '512', '512x512', '', 512),
            ('1024', '1024', '1024x1024', '', 1024),
            ('2048', '2048', '2048x2048', '', 2048),
            ('4096', '4096', '4096x4096', '', 4096),
            ('8192', '8192', '8192x8192', '', 8192),
        ],
        name='Render size',
        default='256', update=vlm_camera.camera_inclination_update
    )
    render_aspect_ratio: FloatProperty(name="Render AR", description="Aspect ratio of render bakes", default = 1.0)
    padding: IntProperty(name="Padding", description="Padding between bakes", default = 2, min = 0)
    remove_backface: FloatProperty(name="Backface Limit", description="Angle (degree) limit for backfacing geometry removal", default = 0.0)
    uv_packer: EnumProperty(
        items=[
            ('blender', 'Blender', 'Use Blender internal UV island packing', '', 0),
            ('uvpacker', 'UVPacker', 'Use UVPacker for packing islands', '', 1),
        ],
        name='UV Packer',
        description='UV Packer to use',
        default='uvpacker'
    )
    packmap_tex_factor: EnumProperty(
        items=[
            ('0.25', '1:4', 'Create packmap at a quarter of the render size', '', 0),
            ('0.5', '1:2', 'Create packmap at half the render size', '', 1),
            ('1.0', '1:1', 'Create packmap at the render size', '', 2),
        ],
        name="Packmap Tex Ratio",
        default='1.0'
    )
    bake_packmap_mode: EnumProperty(
        items=[
            ('gpu', 'GPU', 'Render packmap on GPU, fast, low memory requirements, no HDR/padding support', '', 0),
            ('eevee', 'Eevee', 'Render packmap with eevee ortho, speed is ok, HDR but no padding support', '', 1),
            ('cycle_seq', 'Cycle Seq', 'Render packmap with Cycle bakes, one render at a time, utterly slow, HDR & padding support', '', 2),
            ('cycle', 'Cycle', 'Render packmap with Cycle bakes, one bake at a time, rather slow, very high memory requirmeents, HDR & padding support', '', 3),
        ],
        name="Packmap mode",
        default='cycle'
    )
    # Exporter options
    export_image_type: EnumProperty(
        items=[
            ('png', 'PNG', 'Use PNG images', '', 0),
            ('webp', 'WEBP', 'Use WebP images', '', 1),
        ],
        name='Image format',
        description='Image format used in exported table',
        default='webp'
    )
    export_mode: EnumProperty(
        items=[
            ('default', 'Default', 'Add bakes and lightmap to the table', '', 0),
            ('hide', 'Hide', 'Hide items that have been baked', '', 1),
            ('remove', 'Remove', 'Delete items that have been baked', '', 2),
            ('remove_all', 'Remove All', 'Delete items and images that have been baked', '', 3),
        ],
        name='Export mode',
        default='remove_all'
    )
    # Active table informations
    table_file: StringProperty(name="Table", description="Table filename", default="")
    playfield_size: FloatVectorProperty(name="Playfield size:", description="Size of the playfield in VP unit", default=(0, 0, 0, 0), size=4)


class VLM_Collection_props(PropertyGroup):
    bake_mode: EnumProperty(
        items=[
            ('default', 'Default', 'Default bake process', '', 0),
            ('movable', 'Movable', 'Bake to a splitted movable mesh', '', 1),
            ('playfield', 'Playfield', 'Bake to a dedicated orthographic playfield image', '', 2)
        ],
        name='Bake Mode',
        description='Bake mode for the selected collection',
        default='default'
    )
    light_mode: EnumProperty(
        items=[
            ('world', 'World', 'Contribute to base lighting', '', 0),
            ('group', 'Group', 'Bake all lights as a single group', '', 1),
            ('split', 'Split', 'Bake each light separately', '', 2)
        ],
        name='Light Mode',
        description='Light mode for the selected collection',
        default='group'
    )
    is_active_mat: BoolProperty(name="Active Material", description="True if this bake group need an 'Active' material (non opaque, under playfield,...)", default = False)


class VLM_Object_props(PropertyGroup):
    # Bake objects properties
    vpx_object: StringProperty(name="VPX", description="Identifier of reference VPX object", default = '')
    vpx_subpart: StringProperty(name="Part", description="Sub part identifier for multi part object like bumpers,...", default = '')
    layback_offset: FloatProperty(name="Layback offset", description="Y offset caused by current layback", default = 0.0)
    import_mesh: BoolProperty(name="Mesh", description="Update mesh on import", default = True)
    import_transform: BoolProperty(name="Transform", description="Update transform on import", default = True)
    render_group: IntProperty(name="Render Group", description="ID of group for batch rendering", default = -1)
    is_rgb_led: BoolProperty(name="RGB Led", description="RGB Led (lightmapped to white for dynamic colors)", default = False)
    # Movable objects bake settings
    movable_lightmap_threshold: FloatProperty(name="Lightmap threshold", description="Light threshold for generating a lightmap (1 for no lightmaps)", default = 1.0)
    movable_influence: EnumProperty(
        items=[
            ('indirect', 'Indirect', 'Allow indirect contribution of this object to other bakes', '', 0),
            ('hide', 'Hide', 'Hide this object from the other bakes', '', 1),
        ],
        default='indirect'
    )
    # Bake result properties
    bake_name: StringProperty(name="Name", description="Lighting situation identifier", default="")
    bake_objects: StringProperty(name="Object", description="Object or collection of object used to create this bake/lightmap", default="")
    bake_light: StringProperty(name="Light", description="Light or collection of lights used to create this lightmap", default="")
    bake_type: EnumProperty(
        items=[
            ('default', 'Default', "Default non static opaque bake", '', 0),
            ('static', 'Static', 'Static bake', '', 1),
            ('active', 'Active', "'Active', i.e. non opaque, bake", '', 2),
            ('lightmap', 'Lightmap', 'Additive lightmap bake', '', 3),
            ('playfield', 'Playfield', 'Bake to a orthographic playfield sized image', '', 4)
        ],
        name="Type",
        default='default'
    )
    bake_hdr_scale: FloatProperty(name="HDR Scale", description="Light intensity factor to be applied for HDR correction", default=1)
    bake_tex_factor: FloatProperty(name="Tex Ratio", description="Texture size factor", default=1)
    bake_packmap: IntProperty(name="Packmap", description="ID of output packmap (multiple bakes may share a packmap)", default = -1)
    bake_packmap_width: IntProperty(name="Width", description="Packmap Texture width", default=1)
    bake_packmap_height: IntProperty(name="Height", description="Packmap Texture height", default=1)


class VLM_OT_new(Operator):
    bl_idname = "vlm.new_operator"
    bl_label = "New"
    bl_description = "Start a new empty project"
    bl_options = {"REGISTER", "UNDO"}
    
    def execute(self, context):
        context.scene.render.engine = 'CYCLES'
        context.scene.cycles.samples = 64
        context.scene.render.film_transparent = True
        context.scene.cycles.use_preview_denoising = True
        context.scene.vlmSettings.table_file = ""
        context.scene.vlmSettings.last_bake_step = "unstarted"
        vlm_collections.delete_collection(vlm_collections.get_collection('ROOT'))
        vlm_collections.setup_collections()
        vlm_utils.load_library()
        return {'FINISHED'}


class VLM_OT_new_from_vpx(Operator, ImportHelper):
    bl_idname = "vlm.new_from_vpx_operator"
    bl_label = "Import"
    bl_description = "Start a new VPX lightmap project"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".vpx"
    filter_glob: StringProperty(default="*.vpx", options={'HIDDEN'}, maxlen=255,)
    
    def execute(self, context):
        context.scene.render.engine = 'CYCLES'
        context.scene.cycles.samples = 64
        context.scene.render.film_transparent = True
        context.scene.cycles.use_preview_denoising = True
        context.scene.vlmSettings.table_file = ""
        vlm_collections.delete_collection(vlm_collections.get_collection('ROOT'))
        context.scene.vlmSettings.last_bake_step = "unstarted"
        return vlm_import.read_vpx(context, self.filepath)


class VLM_OT_update(Operator):
    bl_idname = "vlm.update_operator"
    bl_label = "Update"
    bl_description = "Update this project from the VPX file"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(cls, context):
        return os.path.exists(bpy.path.abspath(context.scene.vlmSettings.table_file))

    def execute(self, context):
        vlmProps = context.scene.vlmSettings
        context.scene.vlmSettings.last_bake_step = "unstarted"
        return vlm_import.read_vpx(context, bpy.path.abspath(context.scene.vlmSettings.table_file))


class VLM_OT_select_occluded(Operator):
    bl_idname = "vlm.select_ocluded_operator"
    bl_label = "Select Occluded"
    bl_description = "Select occluded objects"
    bl_options = {"REGISTER", "UNDO"}
    
    def execute(self, context):
        return vlm_occlusion.select_occluded(context)


class VLM_OT_compute_render_groups(Operator):
    bl_idname = "vlm.compute_render_groups_operator"
    bl_label = "1. Groups"
    bl_description = "Evaluate render groups"
    bl_options = {"REGISTER", "UNDO"}
    
    def execute(self, context):
        return vlm_baker.compute_render_groups(self, context)


class VLM_OT_render_all_groups(Operator):
    bl_idname = "vlm.render_all_groups_operator"
    bl_label = "2. Render"
    bl_description = "Render all groups for all lighting situation"
    bl_options = {"REGISTER"}
    
    def execute(self, context):
        return vlm_baker.render_all_groups(self, context)


class VLM_OT_create_bake_meshes(Operator):
    bl_idname = "vlm.create_bake_meshes_operator"
    bl_label = "3. Bake Meshes"
    bl_description = "Create all bake meshes for all lighting situation"
    bl_options = {"REGISTER", "UNDO"}
    
    def execute(self, context):
        return vlm_baker.create_bake_meshes(self, context)


class VLM_OT_render_packmaps(Operator):
    bl_idname = "vlm.render_packmaps_operator"
    bl_label = "4. Packmaps"
    bl_description = "Render all packmaps"
    bl_options = {"REGISTER", "UNDO"}
    
    def execute(self, context):
        return vlm_baker.render_packmaps(self, context)


class VLM_OT_export_vpx(Operator):
    bl_idname = "vlm.export_vpx_operator"
    bl_label = "5. Export VPX"
    bl_description = "Export to an updated VPX table file"
    bl_options = {"REGISTER"}
    
    def execute(self, context):
        return vlm_export.export_vpx(self, context)


class VLM_OT_batch_bake(Operator):
    bl_idname = "vlm.batch_bake_operator"
    bl_label = "Batch All"
    bl_description = "Performs all the bake steps in a batch, then export an updated VPX table (lengthy operation)"
    bl_options = {"REGISTER", "UNDO"}
    
    def execute(self, context):
        start_time = time.time()
        print(f"\nStarting complete bake batch...")
        vlm_baker.compute_render_groups(self, context)
        vlm_baker.render_all_groups(self, context)
        vlm_baker.create_bake_meshes(self, context)
        vlm_baker.render_packmaps(self, context)
        vlm_export.export_vpx(self, context)
        print(f"\nBatch baking performed in {vlm_utils.format_time(time.time() - start_time)}")
        return {"FINISHED"}


class VLM_OT_state_hide(Operator):
    bl_idname = "vlm.state_hide_operator"
    bl_label = "Hide"
    bl_description = "Hide object from bake"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(cls, context):
        root_col = vlm_collections.get_collection('ROOT', create=False)
        target_col = vlm_collections.get_collection('HIDDEN', create=False)
        return root_col is not None and target_col is not None and \
            next((o for o in context.selected_objects if o.name in root_col.all_objects and o.name not in target_col.all_objects), None) is not None

    def execute(self, context):
        root_col = vlm_collections.get_collection('ROOT', create=False)
        target_col = vlm_collections.get_collection('HIDDEN', create=False)
        if root_col is not None and target_col is not None:
            for obj in [obj for obj in context.selected_objects if obj.name in root_col.all_objects and obj.name not in target_col.all_objects]:
                target_col.objects.link(obj)
                [col.objects.unlink(obj) for col in obj.users_collection if col != target_col]
        return {"FINISHED"}


class VLM_OT_state_indirect(Operator):
    bl_idname = "vlm.state_indirect_operator"
    bl_label = "Indirect"
    bl_description = "Hide object from bake, but keep indirect interaction"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(cls, context):
        root_col = vlm_collections.get_collection('ROOT', create=False)
        target_col = vlm_collections.get_collection('INDIRECT', create=False)
        return root_col is not None and target_col is not None and \
            next((o for o in context.selected_objects if o.name in root_col.all_objects and o.name not in target_col.all_objects), None) is not None

    def execute(self, context):
        root_col = vlm_collections.get_collection('ROOT', create=False)
        target_col = vlm_collections.get_collection('INDIRECT', create=False)
        if root_col is not None and target_col is not None:
            for obj in [obj for obj in context.selected_objects if obj.name in root_col.all_objects and obj.name not in target_col.all_objects]:
                target_col.objects.link(obj)
                [col.objects.unlink(obj) for col in obj.users_collection if col != target_col]
        return {"FINISHED"}


class VLM_OT_state_bake(Operator):
    bl_idname = "vlm.state_bake_operator"
    bl_label = "Bake"
    bl_description = "Enable objects for baking"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(cls, context):
        root_col = vlm_collections.get_collection('ROOT', create=False)
        target_col = vlm_collections.get_collection('BAKE', create=False)
        return root_col is not None and target_col is not None and \
            next((o for o in context.selected_objects if o.name in root_col.all_objects and o.name not in target_col.all_objects and o.type != 'LIGHT'), None) is not None
        return False

    def execute(self, context):
        root_col = vlm_collections.get_collection('ROOT', create=False)
        target_col = vlm_collections.get_collection('BAKE DEFAULT', create=False)
        if root_col is not None and target_col is not None:
            for obj in [obj for obj in context.selected_objects if obj.name in root_col.all_objects and obj.name not in target_col.all_objects and obj.type != 'LIGHT']:
                target_col.objects.link(obj)
                [col.objects.unlink(obj) for col in obj.users_collection if col != target_col]
        return {"FINISHED"}


class VLM_OT_state_import_mesh(Operator):
    bl_idname = "vlm.state_import_mesh"
    bl_label = "Mesh"
    bl_description = "Update mesh on import"
    bl_options = {"REGISTER", "UNDO"}
    enable_import: bpy.props.BoolProperty()
    
    @classmethod
    def poll(cls, context):
        bake_col = vlm_collections.get_collection('ROOT', create=False)
        return bake_col is not None and next((obj for obj in context.selected_objects if obj.name in bake_col.all_objects), None) is not None

    def execute(self, context):
        bake_col = vlm_collections.get_collection('ROOT', create=False)
        if bake_col is not None:
            for obj in [obj for obj in context.selected_objects if obj.name in bake_col.all_objects]:
                obj.vlmSettings.import_mesh = self.enable_import
        return {"FINISHED"}


class VLM_OT_state_import_transform(Operator):
    bl_idname = "vlm.state_import_transform"
    bl_label = "Transform"
    bl_description = "Update transform on import"
    bl_options = {"REGISTER", "UNDO"}
    enable_transform: bpy.props.BoolProperty()
    
    @classmethod
    def poll(cls, context):
        bake_col = vlm_collections.get_collection('ROOT', create=False)
        return bake_col is not None and next((obj for obj in context.selected_objects if obj.name in bake_col.all_objects), None) is not None

    def execute(self, context):
        bake_col = vlm_collections.get_collection('ROOT', create=False)
        if bake_col is not None:
            for obj in [obj for obj in context.selected_objects if obj.name in bake_col.all_objects]:
                obj.vlmSettings.import_transform = self.enable_transform
        return {"FINISHED"}


class VLM_OT_clear_render_group_cache(Operator):
    bl_idname = "vlm.clear_render_group_cache"
    bl_label = "Clear Cache"
    bl_description = "Remove render group from cache"
    bl_options = {"REGISTER"}
    
    @classmethod
    def poll(cls, context):
        bake_col = vlm_collections.get_collection('BAKE', create=False)
        if bake_col is not None:
            files = glob.glob(bpy.path.abspath(f"{vlm_utils.get_bakepath(context, type='RENDERS')}") + "* - Group *.exr")
            for obj in [obj for obj in context.selected_objects if obj.name in bake_col.all_objects and obj.vlmSettings.render_group >= 0]:
                if next((f for f in files if f.endswith(f' {obj.vlmSettings.render_group}.exr')),None) != None:
                    return True
        return False

    def execute(self, context):
        bake_col = vlm_collections.get_collection('BAKE', create=False)
        delete_set = {}
        if bake_col is not None:
            files = glob.glob(bpy.path.abspath(f"{vlm_utils.get_bakepath(context, type='RENDERS')}") + "* - Group *.exr")
            for obj in [obj for obj in context.selected_objects if obj.name in bake_col.all_objects and obj.vlmSettings.render_group >= 0]:
                for f in (f for f in files if f.endswith(f' {obj.vlmSettings.render_group}.exr')):
                    delete_set[f] = True
            for f in delete_set:
                os.remove(f)
        return {"FINISHED"}


class VLM_OT_select_render_group(Operator):
    bl_idname = "vlm.select_render_group"
    bl_label = "Select"
    bl_description = "Select all object from this render group"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(cls, context):
        bake_col = vlm_collections.get_collection('BAKE', create=False)
        return bake_col is not None and next((obj for obj in context.selected_objects if obj.name in bake_col.all_objects and obj.vlmSettings.render_group >= 0), None) is not None

    def execute(self, context):
        bake_col = vlm_collections.get_collection('BAKE', create=False)
        if bake_col is not None:
            for obj in [obj for obj in context.selected_objects if obj.name in bake_col.all_objects and obj.vlmSettings.render_group >= 0]:
                for other in bake_col.all_objects:
                    if other.vlmSettings.render_group == obj.vlmSettings.render_group:
                        other.select_set(True)
        return {"FINISHED"}


class VLM_OT_select_packmap_group(Operator):
    bl_idname = "vlm.select_packmap_group"
    bl_label = "Select"
    bl_description = "Select all object from this packmap"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(cls, context):
        bake_col = vlm_collections.get_collection('BAKE RESULT', create=False)
        return bake_col is not None and next((obj for obj in context.selected_objects if obj.name in bake_col.all_objects and obj.vlmSettings.bake_packmap >= 0), None) is not None

    def execute(self, context):
        bake_col = vlm_collections.get_collection('BAKE RESULT', create=False)
        if bake_col is not None:
            for obj in [obj for obj in context.selected_objects if obj.name in bake_col.all_objects and obj.vlmSettings.bake_packmap >= 0]:
                for other in bake_col.all_objects:
                    if other.vlmSettings.bake_packmap == obj.vlmSettings.bake_packmap:
                        other.select_set(True)
        return {"FINISHED"}


class VLM_OT_load_render_images(Operator):
    bl_idname = "vlm.load_render_images_operator"
    bl_label = "Load/Unload Renders"
    bl_description = "Load/Unload render images for preview"
    bl_options = {"REGISTER", "UNDO"}
    
    @classmethod
    def poll(cls, context):
        object_col = vlm_collections.get_collection('BAKE RESULT', create=False)
        if object_col is not None:
            for obj in context.selected_objects:
                if obj.name in object_col.all_objects:
                    return True
        return False

    def execute(self, context):
        vlmProps = context.scene.vlmSettings
        result_col = vlm_collections.get_collection('BAKE RESULT')
        bakepath = vlm_utils.get_bakepath(context, type='RENDERS')
        for obj in context.selected_objects:
            if obj.name in result_col.all_objects:
                paths = [f"{bakepath}{obj.vlmSettings.bake_name} - Group {i}.exr" for i,_ in enumerate(obj.data.materials)]
                images = [vlm_utils.image_by_path(path) for path in paths]
                all_loaded = all((not os.path.exists(bpy.path.abspath(path)) or im is not None for path, im in zip(paths, images)))
                print(images)
                if all_loaded:
                    for im in images:
                        if im != None and im.name != 'VLM.NoTex': bpy.data.images.remove(im)
                else:
                    for path, mat in zip(paths, obj.data.materials):
                        _, im = vlm_utils.get_image_or_black(path)
                        mat.node_tree.nodes["BakeTex"].image = im
        return {"FINISHED"}


class VLM_PT_Importer(bpy.types.Panel):
    bl_label = "VPX Importer"
    bl_category = "VLM"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        vlmProps = context.scene.vlmSettings
        row = layout.row()
        row.scale_y = 1.5
        row.operator(VLM_OT_new.bl_idname)
        row.operator(VLM_OT_new_from_vpx.bl_idname)
        row.operator(VLM_OT_update.bl_idname)
        layout.prop(vlmProps, "table_file")
        layout.prop(vlmProps, "light_size")
        layout.prop(vlmProps, "light_intensity")
        layout.separator()
        layout.prop(vlmProps, "process_plastics")
        layout.prop(vlmProps, "bevel_plastics")
        layout.separator()
        layout.prop(vlmProps, "process_inserts")
        layout.prop(vlmProps, "insert_size")
        layout.prop(vlmProps, "insert_intensity")
        layout.prop(vlmProps, "use_pf_translucency_map")


class VLM_PT_Camera(bpy.types.Panel):
    bl_label = "VPX Camera"
    bl_category = "VLM"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        vlmProps = context.scene.vlmSettings
        layout.prop(vlmProps, "layback_mode", expand=True)
        layout.prop(vlmProps, "camera_layback")
        layout.prop(vlmProps, "camera_inclination")


class VLM_PT_Lightmapper(bpy.types.Panel):
    bl_label = "VPX Light Mapper"
    bl_category = "VLM"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        vlmProps = context.scene.vlmSettings
        step = 0
        if vlmProps.last_bake_step == 'groups': step = 1
        if vlmProps.last_bake_step == 'renders': step = 2
        if vlmProps.last_bake_step == 'meshes': step = 3
        if vlmProps.last_bake_step == 'packmaps': step = 4
        layout.prop(vlmProps, "tex_size")
        layout.prop(vlmProps, "padding")
        layout.prop(vlmProps, "remove_backface", text='Backface')
        layout.prop(vlmProps, "uv_packer")
        layout.prop(vlmProps, "packmap_tex_factor")
        layout.prop(vlmProps, "bake_packmap_mode")
        layout.prop(vlmProps, "export_image_type")
        layout.prop(vlmProps, "export_mode")
        row = layout.row()
        row.scale_y = 1.5
        row.operator(VLM_OT_compute_render_groups.bl_idname, icon='GROUP_VERTEX', text='Groups')
        row.operator(VLM_OT_render_all_groups.bl_idname, icon='RENDER_RESULT', text='Renders', emboss=step>0)
        row.operator(VLM_OT_create_bake_meshes.bl_idname, icon='MESH_MONKEY', text='Meshes', emboss=step>1)
        row = layout.row()
        row.scale_y = 1.5
        row.operator(VLM_OT_render_packmaps.bl_idname, icon='TEXTURE_DATA', text='Packmaps', emboss=step>2)
        row.operator(VLM_OT_export_vpx.bl_idname, icon='EXPORT', text='Export', emboss=step>3)
        row.operator(VLM_OT_batch_bake.bl_idname)


class VLM_PT_Col_Props(bpy.types.Panel):
    bl_label = "Visual Pinball X Light Mapper"
    bl_category = "VLM"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "collection"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        col = context.collection
        bake_col = vlm_collections.get_collection('BAKE')
        light_col = vlm_collections.get_collection('LIGHTS')
        if col.name in bake_col.children:
            layout.prop(col.vlmSettings, 'bake_mode', expand=True)
            layout.prop(col.vlmSettings, 'is_active_mat', expand=True)
        elif col.name in light_col.children:
            layout.prop(col.vlmSettings, 'light_mode', expand=True)
        else:
            layout.label(text="Select a bake or light group") 


class VLM_PT_3D_Bake_Object(bpy.types.Panel):
    bl_label = "Bake Object"
    bl_category = "VLM"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        root_col = vlm_collections.get_collection('ROOT', create=False)
        light_col = vlm_collections.get_collection('LIGHTS', create=False)
        result_col = vlm_collections.get_collection('BAKE RESULT', create=False)
        
        bake_objects = [obj for obj in context.selected_objects if (root_col is not None and obj.name in root_col.all_objects) and (result_col is None or obj.name not in result_col.all_objects)]
        if bake_objects:
            if len(bake_objects) == 1:
                obj = bake_objects[0]
                layout.label(text="Link to VPX object:")
                layout.prop(obj.vlmSettings, 'vpx_object', text='VPX', expand=True)
                layout.prop(obj.vlmSettings, 'vpx_subpart', text='Subpart', expand=True)
                if light_col and obj.name in light_col.all_objects:
                    layout.prop(obj.vlmSettings, 'is_rgb_led', text='RGB Led', expand=True)
                layout.separator()
            layout.label(text="Import options:")
            row = layout.row(align=True)
            row.scale_y = 1.5
            if all((x.vlmSettings.import_mesh for x in bake_objects)):
                row.operator(VLM_OT_state_import_mesh.bl_idname, text='On', icon='MESH_DATA').enable_import = False
            elif all((not x.vlmSettings.import_mesh for x in bake_objects)):
                row.operator(VLM_OT_state_import_mesh.bl_idname, text='Off', icon='MESH_DATA').enable_import = True
            else:
                row.operator(VLM_OT_state_import_mesh.bl_idname, text='-', icon='MESH_DATA').enable_import = True
            if all((x.vlmSettings.import_transform for x in bake_objects)):
                row.operator(VLM_OT_state_import_transform.bl_idname, text='On', icon='OBJECT_ORIGIN').enable_transform = False
            elif all((not x.vlmSettings.import_transform for x in bake_objects)):
                row.operator(VLM_OT_state_import_transform.bl_idname, text='Off', icon='OBJECT_ORIGIN').enable_transform = True
            else:
                row.operator(VLM_OT_state_import_transform.bl_idname, text='-', icon='MATERIAL').enable_transform = True
            layout.separator()
            layout.label(text="Bake visibility:")
            row = layout.row(align=True)
            row.scale_y = 1.5
            row.operator(VLM_OT_state_hide.bl_idname)
            row.operator(VLM_OT_state_indirect.bl_idname)
            row.operator(VLM_OT_state_bake.bl_idname)
            layout.separator()
            single_group = -1
            for obj in bake_objects:
                if single_group == -1:
                    single_group = obj.vlmSettings.render_group
                elif single_group != obj.vlmSettings.render_group:
                    single_group = -2
            if single_group == -2:
                layout.label(text="Multiple render groups")
            elif single_group == -1:
                layout.label(text="Undefined render groups")
            else:
                layout.label(text=f"Render group #{single_group}")
            row = layout.row(align=True)
            row.operator(VLM_OT_clear_render_group_cache.bl_idname)
            row.operator(VLM_OT_select_render_group.bl_idname)


class VLM_PT_3D_Bake_Result(bpy.types.Panel):
    bl_label = "Bake Result"
    bl_category = "VLM"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        result_col = vlm_collections.get_collection('BAKE RESULT', create=False)
        result_objects = [obj for obj in context.selected_objects if result_col is not None and obj.name in result_col.all_objects]
        if result_objects:
            if len(result_objects) == 1:
                props = result_objects[0].vlmSettings
                layout.prop(props, 'bake_name')
                layout.prop(props, 'bake_objects')
                layout.prop(props, 'bake_light')
                layout.prop(props, 'bake_type')
                layout.prop(props, 'bake_hdr_scale')
                layout.prop(props, 'bake_tex_factor')
                layout.separator()
                layout.prop(props, 'bake_packmap')
                layout.prop(props, 'bake_packmap_width')
                layout.prop(props, 'bake_packmap_height')
                layout.operator(VLM_OT_select_packmap_group.bl_idname)
            layout.separator()
            layout.operator(VLM_OT_load_render_images.bl_idname)


class VLM_PT_3D_Selection(bpy.types.Panel):
    bl_label = "Selection"
    bl_category = "VLM"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.operator(VLM_OT_select_occluded.bl_idname)


class VLM_PT_3D_warning_panel(bpy.types.Panel):
    bl_label = "Visual Pinball X Light Mapper"
    bl_category = "VLM"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"

    @classmethod
    def poll(self, context):
        return not dependencies_installed

    def draw(self, context):
        layout = self.layout
        lines = [f"Please install the missing dependencies",
                 f"for the \"{bl_info.get('name')}\" add-on.",
                 f"1. Open the preferences (Edit > Preferences > Add-ons).",
                 f"2. Search for the \"{bl_info.get('name')}\" add-on.",
                 f"3. Open the details section of the add-on.",
                 f"4. Click on the \"{VLM_OT_install_dependencies.bl_label}\" button.",
                 f"   This will download and install the missing",
                 f"   Python packages, if Blender has the required",
                 f"   permissions. You will need to restart Blender."]
        for line in lines:
            layout.label(text=line)


class VLM_PT_Props_warning_panel(bpy.types.Panel):
    bl_label = "Visual Pinball X Light Mapper"
    bl_category = "VLM"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'

    @classmethod
    def poll(self, context):
        return not dependencies_installed

    def draw(self, context):
        layout = self.layout
        lines = [f"Please install the missing dependencies",
                 f"for the \"{bl_info.get('name')}\" add-on.",
                 f"1. Open the preferences (Edit > Preferences > Add-ons).",
                 f"2. Search for the \"{bl_info.get('name')}\" add-on.",
                 f"3. Open the details section of the add-on.",
                 f"4. Click on the \"{VLM_OT_install_dependencies.bl_label}\" button.",
                 f"   This will download and install the missing",
                 f"   Python packages, if Blender has the required",
                 f"   permissions. You will need to restart Blender."]
        for line in lines:
            layout.label(text=line)


class VLM_OT_install_dependencies(bpy.types.Operator):
    bl_idname = "vlm.install_dependencies"
    bl_label = "Install dependencies"
    bl_description = ("Downloads and installs the required python packages for this add-on. "
                      "Internet connection is required. Blender may have to be started with "
                      "elevated permissions in order to install the package")
    bl_options = {"REGISTER", "INTERNAL"}

    @classmethod
    def poll(self, context):
        return not dependencies_installed

    def execute(self, context):
        try:
            vlm_dependencies.install_dependencies(dependencies)
        except (subprocess.CalledProcessError, ImportError) as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        global dependencies_installed
        dependencies_installed = True
        for cls in classes:
            bpy.utils.register_class(cls)
        return {"FINISHED"}


class VLM_preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    def draw(self, context):
        layout = self.layout
        layout.operator(VLM_OT_install_dependencies.bl_idname, icon="CONSOLE")


classes = (
    VLM_Scene_props,
    VLM_Collection_props,
    VLM_Object_props,
    VLM_PT_Importer,
    VLM_PT_Camera,
    VLM_PT_Lightmapper,
    VLM_PT_Col_Props,
    VLM_PT_3D_Bake_Object,
    VLM_PT_3D_Bake_Result,
    VLM_PT_3D_Selection,
    VLM_OT_new,
    VLM_OT_new_from_vpx,
    VLM_OT_update,
    VLM_OT_compute_render_groups,
    VLM_OT_render_all_groups,
    VLM_OT_create_bake_meshes,
    VLM_OT_render_packmaps,
    VLM_OT_batch_bake,
    VLM_OT_state_hide,
    VLM_OT_state_indirect,
    VLM_OT_state_bake,
    VLM_OT_state_import_mesh,
    VLM_OT_state_import_transform,
    VLM_OT_clear_render_group_cache,
    VLM_OT_select_render_group,
    VLM_OT_select_packmap_group,
    VLM_OT_select_occluded,
    VLM_OT_load_render_images,
    VLM_OT_export_vpx,
    )
preference_classes = (VLM_PT_3D_warning_panel, VLM_PT_Props_warning_panel, VLM_OT_install_dependencies, VLM_preferences)
registered_classes = []


def register():
    global dependencies_installed
    dependencies_installed = False
    for cls in preference_classes:
        bpy.utils.register_class(cls)
        registered_classes.append(cls)
    dependencies_installed = vlm_dependencies.import_dependencies(dependencies)
    if dependencies_installed:
        for cls in classes:
            bpy.utils.register_class(cls)
            registered_classes.append(cls)
        bpy.types.Scene.vlmSettings = PointerProperty(type=VLM_Scene_props)
        bpy.types.Collection.vlmSettings = PointerProperty(type=VLM_Collection_props)
        bpy.types.Object.vlmSettings = PointerProperty(type=VLM_Object_props)
    else:
        print(f"VPX light mapper was not installed due to missing dependencies")


def unregister():
    for cls in registered_classes:
        bpy.utils.unregister_class(cls)
    if dependencies_installed:
        del bpy.types.Scene.vlmSettings
        del bpy.types.Collection.vlmSettings
        del bpy.types.Object.vlmSettings


if __name__ == "__main__":
    register()