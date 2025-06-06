import bpy
from .operators.capture_ops import auto_scan_capture_folder
from .capture_list import RemixCaptureListItem

# --- Scene Properties ---

def poll_is_mesh_object(self, object):
    """ Poll function for PointerProperty to allow selecting only MESH objects. """
    return object.type == 'MESH'

def register_properties():
    bpy.types.Scene.remix_mod_file_path = bpy.props.StringProperty(
        name="Remix Mod File",
        description="Path to the main mod.usda file for the Remix project",
        subtype='FILE_PATH',
        default=""
    )
    bpy.types.Scene.remix_active_sublayer_path = bpy.props.StringProperty(
        name="Active Sublayer Path",
        description="Full path to the selected target sublayer",
        default=""
    )
    bpy.types.Scene.remix_project_root_display = bpy.props.StringProperty(
        name="Detected Project Root",
        description="The detected root directory of the Remix project",
        default="(Project not loaded)"
    )
    # Add game name property
    bpy.types.Scene.remix_game_name = bpy.props.StringProperty(
        name="Game Name",
        description="Name of the game for the RTX Remix mod",
        default="Unknown Game"
    )
    # Temporary storage for loaded paths - better approach might be needed
    # bpy.types.Scene._remix_loaded_sublayers = bpy.props.CollectionProperty(type=bpy.types.PropertyGroup)
    bpy.types.Scene.remix_new_sublayer_name = bpy.props.StringProperty(
        name="New Sublayer Name",
        description="Name for the new sublayer file (e.g., my_replacements)",
        default="new_sublayer"
    )
    # Add Anchor Target property to Scene
    bpy.types.Scene.remix_anchor_object_target = bpy.props.PointerProperty(
        name="Remix Anchor Target",
        description="Select an imported mesh object to anchor exported assets to (optional)",
        type=bpy.types.Object,
        poll=poll_is_mesh_object # Reuse the same poll function
    )
    bpy.types.Scene.remix_export_scale = bpy.props.FloatProperty(
        name="Export Scale",
        description="Global scale factor to apply when exporting assets. 0.01 is recommended for most RTX Remix games.",
        default=0.01,
        min=0.001,
        soft_min=0.01,
        soft_max=100.0,
        precision=3,
    )
    
    # Add property to control automatic transform application
    bpy.types.Scene.remix_auto_apply_transforms = bpy.props.BoolProperty(
        name="Auto Apply Transforms",
        description="Automatically apply all transforms (location, rotation, and scale) before exporting meshes (fixes coordinate issues in-game and results in clean identity transforms)",
        default=True,
    )
    
    # Add property to control texture reuse optimization
    bpy.types.Scene.remix_reuse_existing_textures = bpy.props.BoolProperty(
        name="Reuse Existing Textures",
        description="Automatically reuse existing textures for materials with the same base name (ignoring hash suffixes). Disable to force reprocessing of all textures",
        default=True,
    )
    
    # --- New Capture Properties ---
    bpy.types.Scene.remix_capture_folder_path = bpy.props.StringProperty(
        name="Capture Folder",
        description="Path to the RTX Remix capture folder containing .usd files",
        subtype='DIR_PATH',
        default="",
        update=lambda self, context: auto_scan_capture_folder(self, context)
    )
    bpy.types.Scene.remix_capture_scene_scale = bpy.props.FloatProperty(
        name="Capture Scale",
        description="Scale factor to apply when importing capture files. 0.01 is recommended for most RTX Remix games",
        default=0.01,
        min=0.001, soft_min=0.01, soft_max=100.0,
    )
    bpy.types.Scene.remix_capture_import_materials = bpy.props.BoolProperty(
        name="Import Materials",
        description="Import and convert RTX Remix materials from captures",
        default=True,
    )
    bpy.types.Scene.remix_capture_import_lights = bpy.props.BoolProperty(
        name="Import Lights",
        description="Import light sources from captures",
        default=True,
    )

    # --- UIList Properties ---
    bpy.types.Scene.remix_captures = bpy.props.CollectionProperty(type=RemixCaptureListItem)
    bpy.types.Scene.remix_captures_index = bpy.props.IntProperty(name="Capture List Index", default=0)

def unregister_properties():
    del bpy.types.Scene.remix_mod_file_path
    # del bpy.types.Scene.remix_project_dir
    if hasattr(bpy.types.Scene, "remix_active_sublayer_path"):
        del bpy.types.Scene.remix_active_sublayer_path
    if hasattr(bpy.types.Scene, "remix_project_root_display"): 
        del bpy.types.Scene.remix_project_root_display
    if hasattr(bpy.types.Scene, "remix_new_sublayer_name"): 
        del bpy.types.Scene.remix_new_sublayer_name
    if hasattr(bpy.types.Scene, "remix_anchor_object_target"): 
        del bpy.types.Scene.remix_anchor_object_target
    if hasattr(bpy.types.Scene, "remix_game_name"):
        del bpy.types.Scene.remix_game_name
    if hasattr(bpy.types.Scene, "remix_export_scale"):
        del bpy.types.Scene.remix_export_scale
    if hasattr(bpy.types.Scene, "remix_auto_apply_transforms"):
        del bpy.types.Scene.remix_auto_apply_transforms
    if hasattr(bpy.types.Scene, "remix_reuse_existing_textures"):
        del bpy.types.Scene.remix_reuse_existing_textures
    # if hasattr(bpy.types.Scene, "_remix_loaded_sublayers"): # Clean up temp storage
    #     del bpy.types.Scene._remix_loaded_sublayers
    if hasattr(bpy.types.Scene, "_remix_sublayers_ordered"): # Clean up temp storage
         del bpy.types.Scene["_remix_sublayers_ordered"]
    
    # --- Clean up Capture Properties ---
    if hasattr(bpy.types.Scene, "remix_capture_folder_path"):
        del bpy.types.Scene.remix_capture_folder_path
    if hasattr(bpy.types.Scene, "remix_capture_scene_scale"):
        del bpy.types.Scene.remix_capture_scene_scale
    if hasattr(bpy.types.Scene, "remix_capture_import_materials"):
        del bpy.types.Scene.remix_capture_import_materials
    if hasattr(bpy.types.Scene, "remix_capture_import_lights"):
        del bpy.types.Scene.remix_capture_import_lights
    if hasattr(bpy.types.Scene, "_remix_available_captures"):
        del bpy.types.Scene["_remix_available_captures"]
    if hasattr(bpy.types.Scene, "_remix_batch_selected_captures"):
        del bpy.types.Scene["_remix_batch_selected_captures"] 
    
    # --- Clean up UIList Properties ---
    if hasattr(bpy.types.Scene, "remix_captures"):
        del bpy.types.Scene.remix_captures
    if hasattr(bpy.types.Scene, "remix_captures_index"):
        del bpy.types.Scene.remix_captures_index 