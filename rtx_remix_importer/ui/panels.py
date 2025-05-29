import bpy
import os
import bpy_extras
import math
import mathutils
import json
import hashlib
from .. import mod_apply_utils

try:
    from pxr import Usd, Sdf, UsdGeom, UsdShade, Vt, UsdLux, Gf 
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False
    Usd = None 
    Sdf = None
    UsdGeom = None 
    UsdShade = None
    Vt = None
    UsdLux = None
    Gf = None

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
    # if hasattr(bpy.types.Scene, "_remix_loaded_sublayers"): # Clean up temp storage
    #     del bpy.types.Scene._remix_loaded_sublayers
    if hasattr(context.scene, "_remix_sublayers_ordered"): # Clean up temp storage
         del context.scene["_remix_sublayers_ordered"]
    
    # --- Clean up Capture Properties ---
    if hasattr(bpy.types.Scene, "remix_capture_folder_path"):
        del bpy.types.Scene.remix_capture_folder_path
    if hasattr(bpy.types.Scene, "remix_capture_texture_dir_override"):
        del bpy.types.Scene.remix_capture_texture_dir_override
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

def auto_scan_capture_folder(self, context):
    """Auto-scan capture folder when path changes"""
    if USD_AVAILABLE and self.remix_capture_folder_path and context:
        # Use the scan operator to do the actual scanning
        try:
            bpy.ops.remix.scan_capture_folder()
        except:
            # If operator fails, just clear the captures list
            if "_remix_available_captures" in context.scene:
                del context.scene["_remix_available_captures"]

# --- Operators ---

class LoadRemixProject(bpy.types.Operator):
    """Loads the specified Remix mod file and populates the sublayer list"""
    bl_idname = "remix.load_project"
    bl_label = "Load Remix Project"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Check module-level USD_AVAILABLE
        return USD_AVAILABLE and context.scene.remix_mod_file_path

    def execute(self, context):
        if not USD_AVAILABLE: # Check module-level variable
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return {'CANCELLED'}
        
        project_dir = os.path.dirname(mod_file_path)
        print(f"Loading Remix project from: {mod_file_path}")
        print(f"Project Directory: {project_dir}")
        # Store the detected project dir for display
        context.scene.remix_project_root_display = project_dir 

        try:
            stage = Usd.Stage.Open(mod_file_path)
            if not stage:
                self.report({'ERROR'}, f"Failed to open USD stage: {mod_file_path}")
                return {'CANCELLED'}

            root_layer = stage.GetRootLayer()
            sublayer_paths_relative = root_layer.subLayerPaths
            
            # Store ordered list of tuples: (full_path, display_name, relative_path)
            ordered_sublayers = []
            if not sublayer_paths_relative:
                 print("No sublayers found in mod file.")
            else:
                for rel_path in sublayer_paths_relative:
                    full_path = root_layer.ComputeAbsolutePath(rel_path) 
                    if not full_path:
                        print(f"  WARNING: Could not resolve sublayer path: {rel_path}")
                        continue
                    display_name = os.path.basename(full_path)
                    ordered_sublayers.append((full_path, display_name, rel_path))
                    print(f"  Found sublayer: {display_name} ({full_path}) - Ref: {rel_path}")

            # Store the list in the scene using an ID property (simple storage)
            context.scene["_remix_sublayers_ordered"] = ordered_sublayers
            # Reset active sublayer path
            context.scene.remix_active_sublayer_path = ""

            self.report({'INFO'}, f"Loaded {len(sublayer_paths_relative)} sublayers from {os.path.basename(mod_file_path)}")

        except Exception as e:
            self.report({'ERROR'}, f"Failed to load project: {e}")
            if "_remix_sublayers_ordered" in context.scene:
                del context.scene["_remix_sublayers_ordered"]
            return {'CANCELLED'}
        
        return {'FINISHED'}

class SetTargetSublayer(bpy.types.Operator):
    """Sets the active sublayer path for export operations"""
    bl_idname = "remix.set_target_sublayer"
    bl_label = "Set Active Target Sublayer"
    bl_options = {'REGISTER', 'UNDO'}

    sublayer_path: bpy.props.StringProperty(
        name="Sublayer Path",
        description="Full path of the sublayer to set as active target"
    )

    def execute(self, context):
        if self.sublayer_path:
            context.scene.remix_active_sublayer_path = self.sublayer_path
            print(f"Set active export target: {self.sublayer_path}")
            # Force UI redraw if necessary (might not be needed depending on context)
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
                        break
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "No sublayer path provided.")
            return {'CANCELLED'}

class CreateRemixSublayer(bpy.types.Operator):
    """Creates a new sublayer .usda file and adds it to the main mod file"""
    bl_idname = "remix.create_sublayer"
    bl_label = "Create New Sublayer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Check module-level USD_AVAILABLE
        return (USD_AVAILABLE and 
                context.scene.remix_mod_file_path and 
                context.scene.remix_new_sublayer_name)

    def execute(self, context):
        if not USD_AVAILABLE: # Check module-level variable
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        new_name = context.scene.remix_new_sublayer_name
        if not new_name:
            self.report({'ERROR'}, "New sublayer name cannot be empty.")
            return {'CANCELLED'}
        
        # Sanitize name for file usage
        file_name_base = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in new_name)
        if not file_name_base:
             self.report({'ERROR'}, "Invalid characters in new sublayer name.")
             return {'CANCELLED'}
        
        new_file_name = f"{file_name_base}.usda"
        project_dir = os.path.dirname(mod_file_path)
        sublayers_dir = os.path.join(project_dir, "subUSDAs")
        new_sublayer_path = os.path.normpath(os.path.join(sublayers_dir, new_file_name))

        # Create subUSDAs directory if it doesn't exist
        try:
            os.makedirs(sublayers_dir, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Could not create subUSDAs directory: {e}")
            return {'CANCELLED'}

        # Check if file already exists
        if os.path.exists(new_sublayer_path):
             self.report({'WARNING'}, f"Sublayer file already exists: {new_sublayer_path}. Cannot overwrite.")
             # Optionally, offer to just add existing? For now, cancel.
             return {'CANCELLED'}

        # Create the new empty sublayer file
        try:
            new_stage = Usd.Stage.CreateNew(new_sublayer_path)
            if not new_stage:
                 raise RuntimeError("Failed to create new stage object.")
            # Add minimal structure - Use Sdf.Path
            root_prim_path = Sdf.Path("/RootNode")
            # Use OverridePrim instead of DefinePrim to match reference format
            root_prim = new_stage.OverridePrim(root_prim_path) 
            UsdGeom.SetStageUpAxis(new_stage, UsdGeom.Tokens.z)
            
            # Add custom layer data to match reference format
            root_layer = new_stage.GetRootLayer()
            custom_data = {
                'lightspeed_game_name': context.scene.remix_game_name,
                'lightspeed_layer_type': "replacement",
            }
            root_layer.customLayerData = custom_data
            
            # Add other metadata to match reference
            root_layer.startTimeCode = 0 # Reference has startTimeCode = 0
            root_layer.endTimeCode = 100 # Reference has endTimeCode = 100
            root_layer.timeCodesPerSecond = 24 # Reference has timeCodesPerSecond = 24
            root_layer.metersPerUnit = 1 # Reference has metersPerUnit = 1
            # Remove defaultPrim setting if present (not in reference header)
            if root_layer.HasDefaultPrim():
                 root_layer.ClearDefaultPrim()
            
            new_stage.GetRootLayer().Save()
            print(f"Created new sublayer file: {new_sublayer_path}")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create new sublayer file {new_sublayer_path}: {e}")
            return {'CANCELLED'}

        # Add reference to the main mod file
        try:
            mod_stage = Usd.Stage.Open(mod_file_path)
            if not mod_stage:
                 raise RuntimeError(f"Failed to open mod file {mod_file_path} to add sublayer.")
            
            root_layer = mod_stage.GetRootLayer()
            
            # Calculate relative path from mod file to new sublayer
            relative_path = os.path.relpath(new_sublayer_path, start=project_dir).replace('\\', '/')
            # Ensure it starts with ./ if in the same directory or subdirs
            if not relative_path.startswith(".."):
                relative_path = f"./{relative_path}"

            # Check if already present
            current_sublayers = root_layer.subLayerPaths
            if relative_path in current_sublayers:
                self.report({'INFO'}, f"Sublayer '{relative_path}' already exists in {os.path.basename(mod_file_path)}.")
            else:
                root_layer.subLayerPaths.append(relative_path)
                mod_stage.GetRootLayer().Save()
                print(f"Added '{relative_path}' to sublayers in {os.path.basename(mod_file_path)}")
                self.report({'INFO'}, f"Created and added sublayer '{new_file_name}'.")

        except Exception as e:
             self.report({'ERROR'}, f"Failed to add sublayer reference to {mod_file_path}: {e}")
             # Don't cancel here, the file was created, but adding reference failed.

        # Refresh the UI list by calling the load operator
        bpy.ops.remix.load_project()

        return {'FINISHED'}

class AddRemixSublayer(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Selects an existing USD file and adds it as a sublayer to the main mod file"""
    bl_idname = "remix.add_sublayer"
    bl_label = "Add Existing Sublayer"
    bl_options = {'REGISTER', 'UNDO'}

    # ImportHelper properties
    filename_ext = ".usda"
    filter_glob: bpy.props.StringProperty(
        default="*.usda",
        options={'HIDDEN'},
        maxlen=255, 
    )

    @classmethod
    def poll(cls, context):
        # Check module-level USD_AVAILABLE
        return USD_AVAILABLE and context.scene.remix_mod_file_path

    def execute(self, context):
        if not USD_AVAILABLE: # Check module-level variable
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        existing_sublayer_path = bpy.path.abspath(self.filepath) # Path selected by user
        project_dir = os.path.dirname(mod_file_path)

        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return {'CANCELLED'}
        if not os.path.exists(existing_sublayer_path):
             self.report({'ERROR'}, f"Selected sublayer file not found: {existing_sublayer_path}")
             return {'CANCELLED'}
        if not existing_sublayer_path.lower().endswith(".usda"):
             self.report({'ERROR'}, f"Selected file must be a .usda file: {existing_sublayer_path}")
             return {'CANCELLED'}
             
        # Add reference to the main mod file
        try:
            mod_stage = Usd.Stage.Open(mod_file_path)
            if not mod_stage:
                 raise RuntimeError(f"Failed to open mod file {mod_file_path} to add sublayer.")
            
            root_layer = mod_stage.GetRootLayer()
            
            # Calculate relative path from mod file to new sublayer
            relative_path = os.path.relpath(existing_sublayer_path, start=project_dir).replace('\\', '/')
            # Ensure it starts with ./ if in the same directory or subdirs
            if not relative_path.startswith(".."):
                relative_path = f"./{relative_path}"

            # Check if already present
            current_sublayers = root_layer.subLayerPaths
            if relative_path in current_sublayers:
                self.report({'INFO'}, f"Sublayer '{relative_path}' already exists in {os.path.basename(mod_file_path)}. No changes made.")
            else:
                root_layer.subLayerPaths.append(relative_path)
                mod_stage.GetRootLayer().Save()
                print(f"Added '{relative_path}' to sublayers in {os.path.basename(mod_file_path)}")
                self.report({'INFO'}, f"Added existing sublayer '{os.path.basename(existing_sublayer_path)}'.")
                # Refresh the UI list by calling the load operator
                bpy.ops.remix.load_project()

        except Exception as e:
             self.report({'ERROR'}, f"Failed to add sublayer reference to {mod_file_path}: {e}")
             return {'CANCELLED'}
             
        return {'FINISHED'}

class CreateRemixModFile(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    """Creates a new, empty mod.usda file for an RTX Remix project"""
    bl_idname = "remix.create_mod_file"
    bl_label = "Create New Mod File"
    bl_options = {'REGISTER', 'UNDO'}

    # ExportHelper properties
    filename_ext = ".usda"
    filename: bpy.props.StringProperty(default="mod.usda") # Default filename
    filter_glob: bpy.props.StringProperty(
        default="*.usda",
        options={'HIDDEN'},
        maxlen=255, 
    )

    # Check USD availability
    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        new_mod_path = bpy.path.abspath(self.filepath) # Get path from file browser
        
        if not new_mod_path.lower().endswith(".usda"):
            self.report({'ERROR'}, "File must end with .usda")
            return {'CANCELLED'}
            
        # Prevent overwriting existing files
        if os.path.exists(new_mod_path):
            self.report({'ERROR'}, f"File already exists: {new_mod_path}. Cannot overwrite.")
            return {'CANCELLED'}
            
        # Create the new mod file stage
        try:
            stage = Usd.Stage.CreateNew(new_mod_path)
            if not stage:
                raise RuntimeError("Failed to create new stage object.")
            
            # Set basic stage metadata
            UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
            stage.SetMetadata("timeCodesPerSecond", 24) # Common default
            
            # Add standard Remix replacement mod custom layer data
            root_layer = stage.GetRootLayer()
            custom_data = {
                'lightspeed_game_name': context.scene.remix_game_name,
                'lightspeed_layer_type': "replacement",
            }
            root_layer.customLayerData = custom_data
            
            # Add other metadata to match reference
            root_layer.startTimeCode = 0 # Reference has startTimeCode = 0
            root_layer.endTimeCode = 100 # Reference has endTimeCode = 100
            root_layer.timeCodesPerSecond = 24 # Reference has timeCodesPerSecond = 24
            root_layer.metersPerUnit = 1 # Reference has metersPerUnit = 1
            # Remove defaultPrim setting if present (not in reference header)
            if root_layer.HasDefaultPrim():
                 root_layer.ClearDefaultPrim()
            
            # Ensure the file has content before trying to load it
            root_layer.Save()
            print(f"Created new mod file: {new_mod_path}")
            self.report({'INFO'}, f"Created new mod file: {os.path.basename(new_mod_path)}")

            # Set the newly created file as the active project file in the UI
            context.scene.remix_mod_file_path = new_mod_path
            # Automatically load the new (empty) project
            bpy.ops.remix.load_project()

        except Exception as e:
            self.report({'ERROR'}, f"Failed to create new mod file {new_mod_path}: {e}")
            return {'CANCELLED'}
            
        return {'FINISHED'}

class ApplyRemixModChanges(bpy.types.Operator):
    """Applies changes from the loaded Remix mod file and its sublayers to the current scene"""
    bl_idname = "remix.apply_mod_changes"
    bl_label = "Apply Mod File Changes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Only allow if a mod file is loaded and USD is available
        return USD_AVAILABLE and context.scene.remix_mod_file_path

    def _is_usd_light_prim(self, prim):
        """Check if a prim is a USD light type, handling different USD versions gracefully"""
        if not USD_AVAILABLE:
            return False
        
        # List of light types to check, with safe attribute checking
        light_types = [
            'SphereLight', 'RectLight', 'DiskLight', 'DistantLight', 
            'SpotLight', 'CylinderLight', 'GeometryLight'
        ]
        
        for light_type in light_types:
            if hasattr(UsdLux, light_type):
                light_class = getattr(UsdLux, light_type)
                try:
                    if prim.IsA(light_class):
                        return True
                except:
                    # If IsA() fails for any reason, continue checking other types
                    continue
        
        # Fallback: check if it has LightAPI applied
        try:
            if UsdLux.LightAPI(prim):
                return True
        except:
            pass
            
        return False

    def _is_specific_light_type(self, prim, light_type_name):
        """Check if a prim is a specific USD light type, handling different USD versions gracefully"""
        if not USD_AVAILABLE:
            return False
        
        if hasattr(UsdLux, light_type_name):
            light_class = getattr(UsdLux, light_type_name)
            try:
                return prim.IsA(light_class)
            except:
                return False
        return False

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Attempting to apply changes from: {os.path.basename(mod_file_path)}")
        
        try:
            stage = Usd.Stage.Open(mod_file_path, Usd.Stage.LoadAll) # Load all sublayers
            if not stage:
                self.report({'ERROR'}, f"Failed to open USD stage: {mod_file_path}")
                return {'CANCELLED'}

            # --- Step 1: Prim Traversal & Object Matching ---
            self.report({'INFO'}, "Building map of existing Blender objects...")
            blender_object_map = {}
            for obj in bpy.data.objects:
                if "usd_instance_path" in obj:
                    blender_object_map[obj["usd_instance_path"]] = obj
                # Potentially also check for "usd_prim_path" for non-instanced prims (lights, cameras directly)
                # For now, focusing on instance paths as they are common in mod files.

            self.report({'INFO'}, f"Found {len(blender_object_map)} Blender objects with 'usd_instance_path'.")

            num_prims_processed = 0
            num_matched_objects = 0
            num_new_prims = 0

            # Get the XformCache for transform retrieval later
            time_code = Usd.TimeCode.Default()
            xform_cache = UsdGeom.XformCache(time_code) # Requires UsdGeom to be imported

            # --- Helper function for transforms (adapted from import_core.py) ---
            # MOVED to mod_apply_utils.py as get_blender_transform_matrix_from_mod
            # We need up_axis_is_y for this helper. Assume Z-up for mod files, or detect from mod_stage.
            # For simplicity, let's assume the mod stage itself might define its up-axis.
            # If not, it inherits from the main scene it's modifying. Blender is Z-up.
            mod_up_axis = UsdGeom.GetStageUpAxis(stage) # Get up-axis from the mod stage itself
            up_axis_is_y_in_mod = (mod_up_axis == UsdGeom.Tokens.y)
            if up_axis_is_y_in_mod:
                self.report({'INFO'}, "Mod file stage is Y-Up. Transforms will be converted to Z-Up.")

            def get_blender_transform_matrix(usd_prim_to_transform, current_xform_cache):
                try:
                    local_to_world_gf = current_xform_cache.GetLocalToWorldTransform(usd_prim_to_transform)
                    m = local_to_world_gf
                    bl_matrix = mathutils.Matrix((
                        (m[0][0], m[1][0], m[2][0], m[3][0]),
                        (m[0][1], m[1][1], m[2][1], m[3][1]),
                        (m[0][2], m[1][2], m[2][2], m[3][2]),
                        (m[0][3], m[1][3], m[2][3], m[3][3])
                    ))
                    if up_axis_is_y_in_mod:
                        mat_yup_to_zup = mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')
                        bl_matrix = mat_yup_to_zup @ bl_matrix
                    return bl_matrix
                except Exception as e:
                    self.report({'WARNING'}, f"Error getting transform for {usd_prim_to_transform.GetPath()}: {e}")
                    return mathutils.Matrix() # Return identity as fallback
            # --- End Helper --- 

            mod_material_cache = {}
            mod_base_material_node_cache = {} 
            main_project_mod_file = bpy.path.abspath(context.scene.remix_mod_file_path)
            texture_resolution_context_path = os.path.dirname(main_project_mod_file) if main_project_mod_file and os.path.exists(main_project_mod_file) else os.path.dirname(mod_file_path)
            self.report({'INFO'}, f"Texture resolution context for applying materials: {texture_resolution_context_path}")
            mod_apply_utils.clear_mod_apply_caches()
            newly_created_blender_objects = []

            for prim in stage.TraverseAll():
                num_prims_processed +=1
                prim_path = str(prim.GetPath())
                bl_object = blender_object_map.get(prim_path)

                if bl_object:
                    num_matched_objects += 1
                    if prim.IsA(UsdGeom.Xformable):
                        new_transform_matrix = mod_apply_utils.get_blender_transform_matrix_from_mod(prim, xform_cache, up_axis_is_y_in_mod, self.report)
                        new_loc, new_rot, new_scale_vec = new_transform_matrix.decompose()
                        current_scene_scale = context.scene.remix_export_scale
                        bl_object.location = new_loc * current_scene_scale 
                        bl_object.rotation_quaternion = new_rot
                        bl_object.scale = new_scale_vec * current_scene_scale
                        
                        print(f"  Applied transform from <{prim_path}> to '{bl_object.name}'")

                    if prim.IsA(UsdGeom.Imageable):
                        imageable = UsdGeom.Imageable(prim)
                        computed_visibility = imageable.ComputeVisibility(time_code)
                        vis_attr = imageable.GetVisibilityAttr()
                        authored_visibility = vis_attr.Get(time_code) if vis_attr and vis_attr.IsAuthored() else None
                        final_visibility_token = authored_visibility if authored_visibility is not None else computed_visibility

                        if final_visibility_token == UsdGeom.Tokens.invisible:
                            if not bl_object.hide_viewport or not bl_object.hide_render:
                                bl_object.hide_viewport = True
                                bl_object.hide_render = True
                                print(f"  Set '{bl_object.name}' to hidden based on <{prim_path}>")
                        else:
                            if bl_object.hide_viewport or bl_object.hide_render:
                                bl_object.hide_viewport = False
                                bl_object.hide_render = False
                                print(f"  Set '{bl_object.name}' to visible based on <{prim_path}>")

                    if prim.IsA(UsdGeom.Boundable):
                        binding_api = UsdShade.MaterialBindingAPI(prim)
                        binding_rel = binding_api.GetDirectBindingRel()
                        bound_material_prim_path_str = None
                        if binding_rel:
                            targets = binding_rel.GetTargets()
                            if targets:
                                bound_material_prim_path_str = str(targets[0]) # Sdf.Path
                        
                        if bound_material_prim_path_str:
                            # print(f"  USD Prim <{prim_path}> has material binding: <{bound_material_prim_path_str}>")
                            # The material definition itself (shaders, textures) comes from the `stage` (mod file stage)
                            # The context for texture resolution within that material (material_usd_context_dir_param)
                            # should be the directory of the USD that *defines* the material, which is the mod file's dir or a sublayer dir.
                            # get_or_create_mod_instance_material will use its own cache (`mod_material_cache`)
                            
                            # The `instance_prim_for_metadata` is `prim` itself, as it might carry metadata overrides for the material.
                            bl_obj_material = mod_apply_utils.get_or_create_mod_instance_material_util(\
                                base_material_usd_path=bound_material_prim_path_str, \
                                instance_prim_for_metadata=prim, \
                                current_mod_stage=stage, \
                                texture_res_context_path_p=texture_resolution_context_path, \
                                mod_file_path_for_tex_p=mod_file_path, \
                                mod_base_material_node_cache_param=mod_base_material_node_cache, \
                                local_material_cache_param=mod_material_cache, \
                                report_fn=self.report\
                            )

                            if bl_obj_material:
                                if bl_object.data.materials:
                                    if bl_object.data.materials[0] != bl_obj_material:
                                        bl_object.data.materials[0] = bl_obj_material
                                        print(f"  Applied material '{bl_obj_material.name}' to '{bl_object.name}' (replaced existing)")
                                    else:
                                        bl_object.data.materials.append(bl_obj_material)
                                        print(f"  Applied material '{bl_obj_material.name}' to '{bl_object.name}' (appended new)")
                                # else:
                                    # print(f"  Warning: Could not get/create Blender material for <{bound_material_prim_path_str}> on '{bl_object.name}'")
                        # else:
                            # If a prim *had* a material, and the mod unbinds it, we should clear it.
                            # However, GetDirectBindingRel().GetTargets() being empty implies no binding in the mod's context.
                            # We assume if no binding is found here, any existing Blender material should remain, 
                            # unless explicitly told to clear it (which is more complex, like tracking original state).
                            # For now, only apply *new* bindings from the mod.
                            pass # No explicit binding in mod, or binding was cleared by mod.

                    # --- 2.e: Apply Overrides to Existing Lights ---
                    if bl_object.type == 'LIGHT' and self._is_usd_light_prim(prim):
                        bl_light_data = bl_object.data
                        light_api = UsdLux.LightAPI(prim)
                        prim_path_str = str(prim.GetPath())
                        changed_props = []

                        # Check for potential type change - This is complex. Blender light types cannot be changed directly.
                        # For now, we will only update properties if the fundamental type matches an expected mapping.
                        # A full type change would require deleting and recreating the light object.
                        # Simple check: if USD is DistantLight and Blender is not SUN, it's a mismatch we won't handle now.

                        # Common Properties
                        if light_api.GetColorAttr().IsDefined() and light_api.GetColorAttr().IsAuthored():
                            new_color = Gf.Vec3f(light_api.GetColorAttr().Get(time_code))
                            if tuple(bl_light_data.color) != tuple(new_color):
                                bl_light_data.color = new_color
                                changed_props.append("color")
                        
                        new_energy = bl_light_data.energy # Start with current energy
                        intensity_authored = False
                        if light_api.GetIntensityAttr().IsDefined() and light_api.GetIntensityAttr().IsAuthored():
                            intensity = light_api.GetIntensityAttr().Get(time_code)
                            intensity_authored = True
                        else: # If not authored in mod, keep existing base intensity component for exposure combination
                            # This is tricky; if only exposure is modded, we need original intensity.
                            # For simplicity, if intensity not modded, assume base intensity is part of current energy.
                            intensity = bl_light_data.energy / pow(2, bl_light_data.blender_custom_props.get("usd_exposure", 0.0)) if hasattr(bl_light_data, "blender_custom_props") and bl_light_data.blender_custom_props.get("usd_exposure") is not None else bl_light_data.energy

                        exposure_authored = False
                        if light_api.GetExposureAttr().IsDefined() and light_api.GetExposureAttr().IsAuthored():
                            exposure = light_api.GetExposureAttr().Get(time_code)
                            # Store exposure for future calculations if intensity is not authored next time
                            if not hasattr(bl_light_data, "blender_custom_props"): bl_light_data.blender_custom_props = {}
                            bl_light_data.blender_custom_props["usd_exposure"] = exposure 
                            exposure_authored = True
                        else: # If not authored, use previously stored or default exposure
                            exposure = bl_light_data.blender_custom_props.get("usd_exposure", 0.0) if hasattr(bl_light_data, "blender_custom_props") else 0.0
                        
                        if intensity_authored or exposure_authored: # Only update energy if either component was changed by mod
                            new_energy_val = intensity * pow(2, exposure)
                            if abs(bl_light_data.energy - new_energy_val) > 1e-5:
                                bl_light_data.energy = new_energy_val
                                changed_props.append("energy (intensity/exposure)")

                        enable_temp_attr = light_api.GetEnableColorTemperatureAttr()
                        color_temp_attr = light_api.GetColorTemperatureAttr()
                        if enable_temp_attr.IsDefined() and enable_temp_attr.IsAuthored():
                            use_temp = enable_temp_attr.Get(time_code)
                            if bl_light_data.use_custom_color_temp != use_temp:
                                bl_light_data.use_custom_color_temp = use_temp
                                changed_props.append("use_color_temp")
                            if use_temp and color_temp_attr.IsDefined() and color_temp_attr.IsAuthored():
                                new_temp = color_temp_attr.Get(time_code)
                                if bl_light_data.color_temperature != new_temp:
                                    bl_light_data.color_temperature = new_temp
                                    changed_props.append("color_temperature")
                        elif color_temp_attr.IsDefined() and color_temp_attr.IsAuthored() and bl_light_data.use_custom_color_temp:
                            # Only enable_temp not authored, but temp is, and blender light uses temp
                            new_temp = color_temp_attr.Get(time_code)
                            if bl_light_data.color_temperature != new_temp:
                                bl_light_data.color_temperature = new_temp
                                changed_props.append("color_temperature (assuming enabled)")

                        # Type-specific properties (only if type matches)
                        if self._is_specific_light_type(prim, 'SphereLight') and bl_light_data.type == 'POINT':
                            sphere_api = UsdLux.SphereLight(prim)
                            if sphere_api.GetRadiusAttr().IsDefined() and sphere_api.GetRadiusAttr().IsAuthored():
                                new_size = sphere_api.GetRadiusAttr().Get(time_code) * 2.0 * current_scene_scale
                                if sphere_api.GetTreatAsPointAttr().Get(time_code): new_size = 0.0
                                if hasattr(bl_light_data, 'size') and abs(bl_light_data.size - new_size) > 1e-5:
                                    bl_light_data.size = new_size
                                    changed_props.append("size (radius)")
                            if hasattr(bl_light_data, 'shape') and bl_light_data.shape != 'SPHERE': 
                                bl_light_data.shape = 'SPHERE' # Ensure shape is sphere if USD says SphereLight
                                changed_props.append("shape (to SPHERE)")
                        elif self._is_specific_light_type(prim, 'RectLight') and bl_light_data.type == 'AREA':
                            rect_api = UsdLux.RectLight(prim)
                            if rect_api.GetWidthAttr().IsDefined() and rect_api.GetWidthAttr().IsAuthored():
                                new_width = rect_api.GetWidthAttr().Get(time_code) * current_scene_scale
                                if hasattr(bl_light_data, 'size') and abs(bl_light_data.size - new_width) > 1e-5:
                                    bl_light_data.size = new_width
                                    changed_props.append("size (width)")
                            if rect_api.GetHeightAttr().IsDefined() and rect_api.GetHeightAttr().IsAuthored():
                                new_height = rect_api.GetHeightAttr().Get(time_code) * current_scene_scale
                                if hasattr(bl_light_data, 'size_y') and abs(bl_light_data.size_y - new_height) > 1e-5:
                                    bl_light_data.size_y = new_height
                                    changed_props.append("size_y (height)")
                            if hasattr(bl_light_data, 'shape') and bl_light_data.shape != 'RECTANGLE': 
                                bl_light_data.shape = 'RECTANGLE'
                                changed_props.append("shape (to RECTANGLE)")
                        elif self._is_specific_light_type(prim, 'SpotLight') and bl_light_data.type == 'SPOT':
                            spot_api = UsdLux.SpotLight(prim)
                            if spot_api.GetShapingConeAngleAttr().IsDefined() and spot_api.GetShapingConeAngleAttr().IsAuthored():
                                new_angle = math.radians(spot_api.GetShapingConeAngleAttr().Get(time_code))
                                if hasattr(bl_light_data, 'spot_size') and abs(bl_light_data.spot_size - new_angle) > 1e-5:
                                    bl_light_data.spot_size = new_angle
                                    changed_props.append("spot_size")
                            if spot_api.GetShapingConeSoftnessAttr().IsDefined() and spot_api.GetShapingConeSoftnessAttr().IsAuthored():
                                new_blend = spot_api.GetShapingConeSoftnessAttr().Get(time_code)
                                if hasattr(bl_light_data, 'spot_blend') and abs(bl_light_data.spot_blend - new_blend) > 1e-5:
                                    bl_light_data.spot_blend = new_blend
                                    changed_props.append("spot_blend")
                        elif self._is_specific_light_type(prim, 'DiskLight') and bl_light_data.type == 'POINT': # Blender Point can be Disk
                            disk_api = UsdLux.DiskLight(prim)
                            if disk_api.GetRadiusAttr().IsDefined() and disk_api.GetRadiusAttr().IsAuthored():
                                new_size = disk_api.GetRadiusAttr().Get(time_code) * 2.0 * current_scene_scale
                                if hasattr(bl_light_data, 'size') and abs(bl_light_data.size - new_size) > 1e-5:
                                    bl_light_data.size = new_size
                                    changed_props.append("size (radius)")
                            if hasattr(bl_light_data, 'shape') and bl_light_data.shape != 'DISK': 
                                bl_light_data.shape = 'DISK'
                                changed_props.append("shape (to DISK)")
                        # Note: USD DistantLight maps to Blender SUN, which has few properties beyond color/energy.

                        if changed_props:
                            print(f"  Applied overrides to light '{bl_object.name}' from <{prim_path_str}>: {', '.join(changed_props)}")

                else:
                    # This prim doesn't have a direct match in the current Blender scene via usd_instance_path
                    # It could be a new prim, a material, a scope, etc.
                    # We only care about actual object types (Mesh, Light, Camera) for new prim creation.
                    if prim.IsA(UsdGeom.Imageable): # A good filter for things that can become objects
                        num_new_prims += 1
                        new_bl_object = None
                        bl_object_data_name = bpy.path.clean_name(prim.GetName()) + "_mod_created" # Ensure unique name

                        if prim.IsA(UsdGeom.Mesh):
                            mesh_data_from_mod = mod_apply_utils.get_mesh_data_from_mod(prim, time_code, up_axis_is_y_in_mod, self.report)
                            if mesh_data_from_mod:
                                verts, faces, uvs_data, normals_data = mesh_data_from_mod
                                bl_mesh_data = bpy.data.meshes.new(name=bl_object_data_name + "_geom")
                                bl_mesh_data.from_pydata(verts, [], faces) # Create mesh from data
                                bl_mesh_data.update()
                                # --- Apply UVs to new mesh ---
                                if uvs_data:
                                    uv_values, uv_indices_list, uv_interpolation = uvs_data
                                    if uv_values and bl_mesh_data.loops:
                                        uv_layer = bl_mesh_data.uv_layers.new(name="st") # Default USD UV map name or get from primvar
                                        blender_loop_uvs = [(0.0, 0.0)] * len(bl_mesh_data.loops)
                                        uvs_processed_successfully = False

                                        if uv_interpolation == UsdGeom.Tokens.faceVarying:
                                            if uv_indices_list and len(uv_indices_list) == len(bl_mesh_data.loops):
                                                for i, loop in enumerate(bl_mesh_data.loops):
                                                    uv_idx = uv_indices_list[i]
                                                    if 0 <= uv_idx < len(uv_values):
                                                        u, v = uv_values[uv_idx][0], uv_values[uv_idx][1]
                                                        blender_loop_uvs[loop.index] = (u, 1.0 - v) # Flip V for Blender
                                                uvs_processed_successfully = True
                                            elif not uv_indices_list and len(uv_values) == len(bl_mesh_data.loops):
                                                for i, loop in enumerate(bl_mesh_data.loops):
                                                    u, v = uv_values[i][0], uv_values[i][1]
                                                    blender_loop_uvs[loop.index] = (u, 1.0 - v) # Flip V
                                                uvs_processed_successfully = True
                                            else:
                                                self.report({'WARNING'}, f"UV faceVarying data size mismatch for new mesh from <{prim_path}>. Skipping UVs.")
                                        elif uv_interpolation == UsdGeom.Tokens.vertex:
                                            if len(uv_values) == len(bl_mesh_data.vertices):
                                                for loop in bl_mesh_data.loops:
                                                    vert_idx = loop.vertex_index
                                                    u, v = uv_values[vert_idx][0], uv_values[vert_idx][1]
                                                    blender_loop_uvs[loop.index] = (u, 1.0 - v) # Flip V
                                                uvs_processed_successfully = True
                                            else:
                                                self.report({'WARNING'}, f"UV vertex data size mismatch for new mesh from <{prim_path}>. Skipping UVs.")
                                        elif uv_interpolation == UsdGeom.Tokens.uniform: # Per-face
                                            if len(uv_values) == len(bl_mesh_data.polygons):
                                                for i, poly in enumerate(bl_mesh_data.polygons):
                                                    u, v = uv_values[i][0], uv_values[i][1]
                                                    for loop_idx in poly.loop_indices:
                                                        blender_loop_uvs[loop_idx] = (u, 1.0 - v) # Flip V
                                                uvs_processed_successfully = True
                                            else:
                                                self.report({'WARNING'}, f"UV uniform data size mismatch for new mesh from <{prim_path}>. Skipping UVs.")
                                        else:
                                            self.report({'WARNING'}, f"Unhandled UV interpolation '{uv_interpolation}' for new mesh from <{prim_path}>. Skipping UVs.")
                                        
                                        if uvs_processed_successfully:
                                            flattened_uvs = [coord for pair in blender_loop_uvs for coord in pair]
                                            uv_layer.data.foreach_set("uv", flattened_uvs)
                                    else:
                                        if not uv_values: self.report({'DEBUG'}, f"No UV values for new mesh <{prim_path}>")
                                        if not bl_mesh_data.loops: self.report({'DEBUG'}, f"Mesh <{prim_path}> has no loops for UVs.")
                                else:
                                    self.report({'DEBUG'}, f"No uvs_data tuple for new mesh <{prim_path}>")
                                
                                # --- Apply Normals to new mesh ---
                                bl_mesh_data.use_auto_smooth = True # Required for custom normals
                                if normals_data:
                                    norm_values, norm_indices_list, norm_interpolation = normals_data
                                    if norm_values and bl_mesh_data.loops: # Check bl_mesh_data.loops for safety
                                        loop_normals = [(0.0, 0.0, 1.0)] * len(bl_mesh_data.loops) # Default to Z up to avoid issues
                                        normals_processed_successfully = False
                                        if norm_interpolation == UsdGeom.Tokens.vertex:
                                            if len(norm_values) == len(bl_mesh_data.vertices):
                                                for i, loop in enumerate(bl_mesh_data.loops):
                                                    loop_normals[loop.index] = tuple(norm_values[loop.vertex_index])
                                                normals_processed_successfully = True
                                            else:
                                                self.report({'WARNING'}, f"Normal vertex data size mismatch for new mesh <{prim_path}>.")
                                        elif norm_interpolation == UsdGeom.Tokens.faceVarying:
                                            if norm_indices_list and len(norm_indices_list) == len(bl_mesh_data.loops):
                                                for i, loop in enumerate(bl_mesh_data.loops):
                                                    norm_idx = norm_indices_list[i]
                                                    if 0 <= norm_idx < len(norm_values):
                                                        loop_normals[loop.index] = tuple(norm_values[norm_idx])
                                                normals_processed_successfully = True
                                            elif not norm_indices_list and len(norm_values) == len(bl_mesh_data.loops):
                                                for i, loop in enumerate(bl_mesh_data.loops):
                                                    loop_normals[loop.index] = tuple(norm_values[i])
                                                normals_processed_successfully = True
                                            else:
                                                self.report({'WARNING'}, f"Normal faceVarying data size/index mismatch for new mesh <{prim_path}>.")
                                        else:
                                            self.report({'WARNING'}, f"Unhandled Normal interpolation '{norm_interpolation}' for new mesh <{prim_path}>.")
                                        
                                        if normals_processed_successfully:
                                            try:
                                                bl_mesh_data.normals_split_custom_set(loop_normals)
                                            except Exception as e_norm: # Catch potential errors during set
                                                self.report({'ERROR'}, f"Failed to set custom normals for <{prim_path}>: {e_norm}")
                                                bl_mesh_data.calc_normals_split() # Fallback if error
                                        else:
                                            self.report({'DEBUG'}, f"Normals not processed successfully for <{prim_path}>, calculating default.")
                                            bl_mesh_data.calc_normals_split() # Fallback
                                    else:
                                        if not norm_values: self.report({'DEBUG'}, f"No Normal values for new mesh <{prim_path}>")
                                        if not bl_mesh_data.loops: self.report({'DEBUG'}, f"Mesh <{prim_path}> has no loops for Normals.")
                                        bl_mesh_data.calc_normals_split() # Fallback if loops or values missing
                                else:
                                    self.report({'DEBUG'}, f"No normals_data tuple for new mesh <{prim_path}>, calculating default.")
                                    bl_mesh_data.calc_normals_split() # Fallback if no USD normals

                                # Removed the general TODO comment as it is now addressed by the detailed logic above.
                                bl_mesh_data.validate(verbose=False) # Keep verbose=False to avoid console spam for valid meshes
                                if bl_mesh_data.polygons: bl_mesh_data.polygons.foreach_set('use_smooth', [True] * len(bl_mesh_data.polygons))
                                
                                new_bl_object = bpy.data.objects.new(name=bl_object_data_name, object_data=bl_mesh_data)
                                new_bl_object["usd_instance_path"] = prim_path # Tag new object
                                print(f"  Created new MESH object '{new_bl_object.name}' from <{prim_path}>")
                        
                        elif self._is_usd_light_prim(prim):
                            # Pass necessary params to the utility function
                            new_bl_object = mod_apply_utils.create_new_blender_light_from_mod(prim, time_code, current_scene_scale, self.report)
                            if new_bl_object:
                                print(f"  Created new LIGHT object '{new_bl_object.name}' from <{prim_path}>")

                        if new_bl_object:
                            num_new_prims += 1 # Increment if object was actually created
                            # Link to scene collection
                            try:
                                context.collection.objects.link(new_bl_object)
                            except Exception as e_link:
                                self.report({'WARNING'}, f"Could not link new object {new_bl_object.name} to scene collection: {e_link}")
                            
                            # Apply Transform for new object
                            if new_bl_object.type != 'LIGHT': # Lights have their transform set during creation typically
                                if prim.IsA(UsdGeom.Xformable):
                                    new_transform_matrix = mod_apply_utils.get_blender_transform_matrix_from_mod(prim, xform_cache, up_axis_is_y_in_mod, self.report)
                                    new_loc, new_rot, new_scale_vec = new_transform_matrix.decompose()
                                    current_scene_scale = context.scene.remix_export_scale
                                    new_bl_object.location = new_loc * current_scene_scale 
                                    new_bl_object.rotation_quaternion = new_rot
                                    new_bl_object.scale = new_scale_vec * current_scene_scale
                           
                            # Apply Visibility for new object
                            if prim.IsA(UsdGeom.Imageable):
                                imageable = UsdGeom.Imageable(prim)
                                computed_visibility = imageable.ComputeVisibility(time_code)
                                vis_attr = imageable.GetVisibilityAttr()
                                authored_visibility = vis_attr.Get(time_code) if vis_attr and vis_attr.IsAuthored() else None
                                final_visibility_token = authored_visibility if authored_visibility is not None else computed_visibility
                                if final_visibility_token == UsdGeom.Tokens.invisible:
                                    new_bl_object.hide_viewport = True
                                    new_bl_object.hide_render = True
                                else:
                                    new_bl_object.hide_viewport = False
                                    new_bl_object.hide_render = False
                           
                            # Apply Material for new object (if it's not a light already handled by light creation)
                            if new_bl_object.type == 'MESH' and prim.IsA(UsdGeom.Boundable):
                                binding_api = UsdShade.MaterialBindingAPI(prim)
                                binding_rel = binding_api.GetDirectBindingRel()
                                bound_material_prim_path_str = None
                                if binding_rel and binding_rel.GetTargets(): bound_material_prim_path_str = str(binding_rel.GetTargets()[0])
                                if bound_material_prim_path_str:
                                    bl_new_obj_material = mod_apply_utils.get_or_create_mod_instance_material_util(\
                                        base_material_usd_path=bound_material_prim_path_str, \
                                        instance_prim_for_metadata=prim, \
                                        current_mod_stage=stage, \
                                        texture_res_context_path_p=texture_resolution_context_path, \
                                        mod_file_path_for_tex_p=mod_file_path, \
                                        mod_base_material_node_cache_param=mod_base_material_node_cache, \
                                        local_material_cache_param=mod_material_cache, \
                                        report_fn=self.report\
                                    )
                                    if bl_new_obj_material:
                                        new_bl_object.data.materials.append(bl_new_obj_material)
                           
                            newly_created_blender_objects.append(new_bl_object)
            
            if num_prims_processed == 0:
                self.report({'WARNING'}, "No prims found in the mod file stage to process.")
                return {'CANCELLED'}

            self.report({'INFO'}, f"Processed {num_prims_processed} prims. Matched: {num_matched_objects}, New potential: {num_new_prims}.")
            if newly_created_blender_objects:
                self.report({'INFO'}, f"Created {len(newly_created_blender_objects)} new Blender objects.")
            
            # Potentially refresh the viewport or specific objects
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
            return {'FINISHED'}

        except Exception as e: # This is the except for the main stage open and processing
            self.report({'ERROR'}, f"Error applying mod changes: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}

# --- UI Panel ---

class PT_RemixProjectPanel(bpy.types.Panel):
    """Creates a Panel in the Scene properties window for Remix Project Management"""
    bl_label = "RTX Remix Project"
    bl_idname = "SCENE_PT_remix_project"
    # Change space and region for N-Panel (Sidebar)
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    # Add a category name for the tab in the N-Panel
    bl_category = "RTX Remix"
    bl_context = ""

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.label(text="Project Setup", icon='SETTINGS')
        row_file = box.row(align=True)
        row_file.prop(scene, "remix_mod_file_path")
        # Button to create a new mod file (on its own row for clarity)
        box.operator(CreateRemixModFile.bl_idname, icon='FILE_NEW')
        
        # Add game name field
        row_game = box.row(align=True)
        row_game.prop(scene, "remix_game_name")
        
        row_root = box.row()
        row_root.label(text="Project Root:")
        row_root.label(text=scene.remix_project_root_display)
        row_load = box.row(align=True)
        row_load.operator(LoadRemixProject.bl_idname, icon='FILE_REFRESH', text="Load Project")
        
        # --- Button to apply mod file changes ---
        row_apply_changes = box.row(align=True)
        row_apply_changes.operator(ApplyRemixModChanges.bl_idname, icon='FILE_TICK', text="Load mod.usda changes (EXPERIMENTAL)")
        # --- End Button ---
        
        # --- Sublayer Management --- 
        box_sublayers = layout.box()
        box_sublayers.label(text="Sublayer Management", icon='LINENUMBERS_ON')
        col = box_sublayers.column(align=True)
        row_create = col.row(align=True)
        row_create.enabled = bool(scene.remix_mod_file_path) # Enable only if mod file is set
        row_create.prop(scene, "remix_new_sublayer_name", text="")
        row_create.operator(CreateRemixSublayer.bl_idname, icon='ADD', text="Create")
        
        row_add = col.row(align=True)
        row_add.enabled = bool(scene.remix_mod_file_path)
        row_add.operator(AddRemixSublayer.bl_idname, icon='FILEBROWSER', text="Add Existing Sublayer")

        # --- Sublayer List & Export --- 
        if scene.remix_mod_file_path:
            box_export = layout.box()
            col = box_export.column()
            col.label(text="Sublayers (Strongest First)", icon='COLLAPSEMENU')

            # Get the ordered list stored in the scene
            sublayers_ordered = scene.get("_remix_sublayers_ordered", [])

            if not sublayers_ordered:
                col.label(text=" (No sublayers found or project not loaded)", icon='ERROR')
            else:
                active_path = scene.remix_active_sublayer_path
                # Display sublayers in order
                for i, (full_path, display_name, rel_path) in enumerate(sublayers_ordered):
                    row = col.row(align=True)
                    # Add indentation based on index? Or just a fixed indent?
                    row.separator(factor=1.0) # Add some indentation
                    
                    # Show Icon indicating if active
                    icon = 'CHECKBOX_HLT' if full_path == active_path else 'CHECKBOX_DEHLT'
                    
                    # Operator button to set this layer as active
                    op = row.operator(SetTargetSublayer.bl_idname, text=display_name, icon=icon)
                    op.sublayer_path = full_path 
                    
                    # Show relative path as well?
                    # row.label(text=f"({rel_path})") # Maybe too cluttered

            # --- Anchoring & Export --- 
            col.separator() 

        # --- Export Section --- 
        if scene.remix_mod_file_path: # Only show if a project is loaded
            layout.separator()
            
            # --- Material Exports Box ---
            box_material_export = layout.box()
            box_material_export.label(text="Material Exports", icon='MATERIAL')
            
            # Material Replacement Export to mod.usda
            row_material_mod = box_material_export.row()
            row_material_mod.enabled = bool(scene.remix_mod_file_path) # Only enable if project is loaded
            material_mod_op = row_material_mod.operator("export_scene.rtx_remix_mod_file", text="Export Material Replacement to mod.usda", icon='FILE_REFRESH')
            material_mod_op.material_replacement_mode = True
            
            # Material Export to Active Sublayer
            row_material_sublayer = box_material_export.row()
            row_material_sublayer.enabled = bool(scene.remix_active_sublayer_path) 
            material_sublayer_op = row_material_sublayer.operator("export_scene.rtx_remix_asset", text="Export Material Replacement to Active Sublayer", icon='MATERIAL')
            material_sublayer_op.material_replacement_mode = True
            
            # --- Mesh/Light Exports Box ---
            box_mesh_export = layout.box()
            box_mesh_export.label(text="Mesh & Light Exports", icon='MESH_DATA')
            
            # Hotload Export to mod.usda
            row_mesh_mod = box_mesh_export.row()
            row_mesh_mod.enabled = bool(scene.remix_mod_file_path) # Only enable if project is loaded
            hotload_op = row_mesh_mod.operator("export_scene.rtx_remix_mod_file", text="Export Selected to mod.usda (Hotload)", icon='FILE_REFRESH')
            hotload_op.material_replacement_mode = False  # Explicitly set to False to ensure full export
            
            # Mesh/Light Export to Active Sublayer
            row_mesh_sublayer = box_mesh_export.row()
            row_mesh_sublayer.enabled = bool(scene.remix_active_sublayer_path) 
            export_op = row_mesh_sublayer.operator("export_scene.rtx_remix_asset", text="Export Selected to Active Sublayer", icon='EXPORT')

        # --- Export Settings ---
        if scene.remix_mod_file_path: # Only show if a project is loaded
            box_export_settings = layout.box()
            box_export_settings.label(text="Export Settings", icon='EXPORT')
            row_scale = box_export_settings.row()
            row_scale.prop(scene, "remix_export_scale")
            # Add Auto Apply Transforms option
            row_transform = box_export_settings.row()
            row_transform.prop(scene, "remix_auto_apply_transforms")
            # Add Anchor selection to the Export settings
            row_anchor = box_export_settings.row()
            row_anchor.prop(scene, "remix_anchor_object_target")

# --- Asset Processing Panel ---
class PT_RemixAssetProcessingPanel(bpy.types.Panel):
    """Panel for managing processed RTX Remix assets"""
    bl_label = "Exported Asset Processing"
    bl_idname = "SCENE_PT_remix_asset_processing"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_context = ""
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Add Invalidate button for selected objects
        box = layout.box()
        box.label(text="Asset Management", icon='FILE_REFRESH')
        row = box.row()
        row.operator("object.rtx_remix_invalidate_assets", icon='TRASH', text="Invalidate Selected Assets")
        
        # Display processed asset list
        box = layout.box()
        box.label(text="Processed Assets", icon='CHECKMARK')
        
        # Count how many objects are processed
        processed_count = 0
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and "remix_processed" in obj and obj["remix_processed"]:
                processed_count += 1
        
        # Show count at the top
        row = box.row()
        row.label(text=f"Total: {processed_count} assets")
        
        # Show list of processed objects
        if processed_count > 0:
            box.separator()
            col = box.column()
            for obj in bpy.data.objects:
                if obj.type == 'MESH' and "remix_processed" in obj and obj["remix_processed"]:
                    row = col.row(align=True)
                    # Use select icon if selected, otherwise use regular object icon
                    icon = 'RESTRICT_SELECT_OFF' if obj.select_get() else 'OBJECT_DATA'
                    row.label(text=obj.name, icon=icon)
                    
                    # Add button to select this object
                    select_op = row.operator("object.select_by_name", text="", icon='RESTRICT_SELECT_OFF')
                    select_op.object_name = obj.name
                    
                    # Add button to invalidate just this object
                    invalidate_op = row.operator("object.rtx_remix_invalidate_single_asset", text="", icon='TRASH')
                    invalidate_op.object_name = obj.name

# --- Operator to Select an Object by Name ---
class SelectObjectByName(bpy.types.Operator):
    """Select an object by name"""
    bl_idname = "object.select_by_name"
    bl_label = "Select Object"
    bl_options = {'REGISTER', 'UNDO'}
    
    object_name: bpy.props.StringProperty(
        name="Object Name",
        description="Name of the object to select",
    )
    
    def execute(self, context):
        # Deselect all objects first
        for obj in context.selected_objects:
            obj.select_set(False)
        
        # Select the target object
        obj = bpy.data.objects.get(self.object_name)
        if obj:
            obj.select_set(True)
            context.view_layer.objects.active = obj
            self.report({'INFO'}, f"Selected {self.object_name}")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, f"Object '{self.object_name}' not found")
            return {'CANCELLED'}

# --- Operator to Invalidate a Single Asset ---
class InvalidateRemixSingleAsset(bpy.types.Operator):
    """Invalidate a single RTX Remix asset for reprocessing"""
    bl_idname = "object.rtx_remix_invalidate_single_asset"
    bl_label = "Invalidate Single Asset"
    bl_options = {'REGISTER', 'UNDO'}
    
    object_name: bpy.props.StringProperty(
        name="Object Name",
        description="Name of the object to invalidate",
    )
    
    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE
    
    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if not obj:
            self.report({'WARNING'}, f"Object '{self.object_name}' not found")
            return {'CANCELLED'}
            
        if "remix_processed" in obj:
            obj["remix_processed"] = False
            if "remix_material_path" in obj:
                del obj["remix_material_path"]
            self.report({'INFO'}, f"Invalidated asset: {self.object_name}")
            
            # Refresh the UI
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
                    
            return {'FINISHED'}
        else:
            self.report({'INFO'}, f"Object '{self.object_name}' is not a processed asset")
            return {'CANCELLED'}

# --- New Capture Management Operators ---

class ScanCaptureFolder(bpy.types.Operator):
    """Refresh the capture folder scan for available USD files"""
    bl_idname = "remix.scan_capture_folder"
    bl_label = "Refresh Capture Folder"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE and context.scene.remix_capture_folder_path

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        capture_folder = bpy.path.abspath(context.scene.remix_capture_folder_path)
        if not os.path.exists(capture_folder):
            self.report({'ERROR'}, f"Capture folder not found: {capture_folder}")
            return {'CANCELLED'}

        print(f"Scanning capture folder: {capture_folder}")
        
        # Find all USD files in the capture folder (top-level only, not recursive)
        # RTX Remix capture folders typically contain thousands of individual asset USD files
        # in the root directory, so recursive scanning would be extremely slow and overwhelming
        usd_files = []
        supported_extensions = ['.usd', '.usda', '.usdc']
        
        try:
            # Only scan the top-level directory, not subdirectories
            for file in os.listdir(capture_folder):
                if any(file.lower().endswith(ext) for ext in supported_extensions):
                    full_path = os.path.join(capture_folder, file)
                    # Verify it's actually a file (not a directory with USD extension)
                    if os.path.isfile(full_path):
                        # Get file size and modification time for display
                        try:
                            stat = os.stat(full_path)
                            size_mb = stat.st_size / (1024 * 1024)
                            mod_time = stat.st_mtime
                            usd_files.append({
                                'name': file,
                                'full_path': full_path,
                                'rel_path': file,  # Just the filename since we're not going recursive
                                'size_mb': size_mb,
                                'mod_time': mod_time
                            })
                        except OSError:
                            # Skip files we can't stat
                            continue
            
            # Sort by modification time (newest first)
            usd_files.sort(key=lambda x: x['mod_time'], reverse=True)
            
            # Store the list in the scene
            context.scene["_remix_available_captures"] = usd_files
            
            self.report({'INFO'}, f"Found {len(usd_files)} USD files in capture folder")
            print(f"Found USD files: {[f['name'] for f in usd_files[:5]]}{'...' if len(usd_files) > 5 else ''}")
            
        except Exception as e:
            self.report({'ERROR'}, f"Error scanning capture folder: {e}")
            return {'CANCELLED'}
        
        return {'FINISHED'}


class ImportCaptureFile(bpy.types.Operator):
    """Import a specific capture USD file"""
    bl_idname = "remix.import_capture_file"
    bl_label = "Import Capture File"
    bl_options = {'REGISTER', 'UNDO'}

    capture_file_path: bpy.props.StringProperty(
        name="Capture File Path",
        description="Full path to the capture USD file to import"
    )

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        if not self.capture_file_path:
            self.report({'ERROR'}, "No capture file path provided.")
            return {'CANCELLED'}

        filepath = bpy.path.abspath(self.capture_file_path)
        if not os.path.exists(filepath):
            self.report({'ERROR'}, f"Capture file not found: {filepath}")
            return {'CANCELLED'}

        # Import the core functionality
        try:
            from .. import import_core
            
            # Use the scene properties for import settings
            scene = context.scene
            texture_dir_override = scene.remix_capture_texture_dir_override
            import_materials = scene.remix_capture_import_materials
            import_lights = scene.remix_capture_import_lights
            scene_scale = scene.remix_capture_scene_scale

            print(f"Importing capture file: {filepath}")
            print(f"  Settings: materials={import_materials}, lights={import_lights}, scale={scene_scale}")
            
            # Call the core import function
            imported_objects, imported_lights, message = import_core.import_rtx_remix_usd_with_materials(
                context,
                filepath,
                texture_dir_override,
                import_materials,
                import_lights,
                scene_scale
            )

            if imported_objects is not None:
                self.report({'INFO'}, f"Imported capture: {message}")
                print(f"Capture import finished: {message}")
                return {'FINISHED'}
            else:
                self.report({'ERROR'}, f"Capture import failed: {message}")
                print(f"Capture import failed: {message}")
                return {'CANCELLED'}
                
        except ImportError as e:
            self.report({'ERROR'}, f"Import core module not available: {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error importing capture file: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


class ClearCaptureList(bpy.types.Operator):
    """Clear the scanned capture file list"""
    bl_idname = "remix.clear_capture_list"
    bl_label = "Clear Capture List"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if "_remix_available_captures" in context.scene:
            del context.scene["_remix_available_captures"]
        if "_remix_batch_selected_captures" in context.scene:
            del context.scene["_remix_batch_selected_captures"]
        self.report({'INFO'}, "Cleared capture file list")
        return {'FINISHED'}

class ToggleCaptureSelection(bpy.types.Operator):
    """Toggle capture selection for batch import"""
    bl_idname = "remix.toggle_capture_selection"
    bl_label = "Toggle Capture Selection"
    bl_options = {'REGISTER', 'UNDO'}

    capture_file_path: bpy.props.StringProperty(
        name="Capture File Path",
        description="Full path to the capture USD file"
    )

    def execute(self, context):
        # Get or create the batch selection list
        selected_captures = context.scene.get("_remix_batch_selected_captures", [])
        if not isinstance(selected_captures, list):
            selected_captures = []
        
        # Toggle selection
        if self.capture_file_path in selected_captures:
            selected_captures.remove(self.capture_file_path)
        else:
            selected_captures.append(self.capture_file_path)
        
        # Store back in scene
        context.scene["_remix_batch_selected_captures"] = selected_captures
        
        return {'FINISHED'}

# --- Capture Management Panel ---

class PT_RemixCapturePanel(bpy.types.Panel):
    """Panel for managing RTX Remix capture imports"""
    bl_label = "Captures"
    bl_idname = "SCENE_PT_remix_capture"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_context = ""
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Capture Folder Selection
        box = layout.box()
        box.label(text="Capture Folder", icon='FOLDER_REDIRECT')
        row = box.row(align=True)
        row.prop(scene, "remix_capture_folder_path", text="")
        row.operator(ScanCaptureFolder.bl_idname, icon='FILE_REFRESH', text="Refresh")
        
        # Cache Management
        cache_row = box.row(align=True)
        cache_row.operator(ClearMaterialCache.bl_idname, icon='TRASH', text="Clear Cache")
        cache_row.scale_y = 0.8  # Make it smaller since it's a utility function
        
        # Import Settings
        if scene.remix_capture_folder_path:
            settings_box = layout.box()
            settings_box.label(text="Import Settings", icon='SETTINGS')
            
            col = settings_box.column(align=True)
            col.prop(scene, "remix_capture_scene_scale")
            col.prop(scene, "remix_capture_import_materials")
            col.prop(scene, "remix_capture_import_lights")
            
            # Advanced settings (collapsible)
            col.separator()
            col.prop(scene, "remix_capture_texture_dir_override")
        
        # Available Captures List
        captures_box = layout.box()
        captures_box.label(text="Available Captures", icon='FILE_3D')
        
        # Get the scanned captures and selected captures
        available_captures = scene.get("_remix_available_captures", [])
        selected_captures = scene.get("_remix_batch_selected_captures", [])
        if not isinstance(selected_captures, list):
            selected_captures = []
        
        if not available_captures:
            if scene.remix_capture_folder_path:
                captures_box.label(text="No captures found. Folder scanned automatically.", icon='INFO')
            else:
                captures_box.label(text="Select a capture folder first.", icon='ERROR')
        else:
            # Show count and controls
            header_row = captures_box.row(align=True)
            header_row.label(text=f"Found: {len(available_captures)} files")
            header_row.operator(ClearCaptureList.bl_idname, icon='TRASH', text="Clear")
            
            # Batch import controls
            if len(selected_captures) > 0:
                batch_row = captures_box.row()
                batch_row.scale_y = 1.2  # Make it more prominent
                batch_row.operator(BatchImportSelectedCaptures.bl_idname, icon='IMPORT', text=f"Batch Import {len(selected_captures)} Selected")
            
            captures_box.separator()
            
            # List captures (limit to first 20 to avoid UI clutter)
            col = captures_box.column()
            max_display = 20
            
            for i, capture in enumerate(available_captures[:max_display]):
                row = col.row(align=True)
                
                # File info
                file_name = capture['name']
                size_mb = capture['size_mb']
                full_path = capture['full_path']
                
                # Truncate long filenames
                display_name = file_name if len(file_name) <= 25 else file_name[:22] + "..."
                
                # Show file icon based on extension
                if file_name.lower().endswith('.usd'):
                    icon = 'FILE_3D'
                elif file_name.lower().endswith('.usda'):
                    icon = 'FILE_TEXT'
                elif file_name.lower().endswith('.usdc'):
                    icon = 'FILE_CACHE'
                else:
                    icon = 'FILE'
                
                # Checkbox for batch selection
                checkbox_icon = 'CHECKBOX_HLT' if full_path in selected_captures else 'CHECKBOX_DEHLT'
                toggle_op = row.operator(ToggleCaptureSelection.bl_idname, text="", icon=checkbox_icon)
                toggle_op.capture_file_path = full_path
                
                # File name and size
                row.label(text=f"{display_name} ({size_mb:.1f}MB)", icon=icon)
                
                # Import button
                import_op = row.operator(ImportCaptureFile.bl_idname, text="", icon='IMPORT')
                import_op.capture_file_path = full_path
            
            # Show "and X more..." if there are more files
            if len(available_captures) > max_display:
                remaining = len(available_captures) - max_display
                col.label(text=f"... and {remaining} more files", icon='THREE_DOTS')
                col.label(text="(Use 'Clear' and 'Scan' to refresh)", icon='INFO')


class ClearMaterialCache(bpy.types.Operator):
    """Clear material and texture caches to resolve duplicate issues"""
    bl_idname = "remix.clear_material_cache"
    bl_label = "Clear Material Cache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            # Clear material caches
            from .. import material_utils
            material_utils.clear_material_cache()
            
            # Clear texture cache
            from .. import texture_loader
            texture_loader.clear_texture_cache()
            
            # Clean up duplicate textures
            removed_count = texture_loader.cleanup_duplicate_textures()
            
            self.report({'INFO'}, f"Cleared caches and removed {removed_count} duplicate textures")
            print("Material and texture caches cleared")
            
        except Exception as e:
            self.report({'ERROR'}, f"Error clearing caches: {e}")
            return {'CANCELLED'}
        
        return {'FINISHED'}


class BatchImportCaptures(bpy.types.Operator):
    """Import multiple capture files while avoiding duplicates"""
    bl_idname = "remix.batch_import_captures"
    bl_label = "Batch Import Captures"
    bl_options = {'REGISTER', 'UNDO'}

    # Properties for batch import
    skip_duplicates: bpy.props.BoolProperty(
        name="Skip Duplicates",
        description="Skip assets that already exist in the scene based on USD prim path",
        default=True
    )
    
    merge_collections: bpy.props.BoolProperty(
        name="Merge Collections",
        description="Merge imported objects into existing collections instead of creating new ones",
        default=True
    )
    
    duplicate_detection_method: bpy.props.EnumProperty(
        name="Duplicate Detection",
        description="Method to detect duplicate assets",
        items=[
            ('USD_PATH', "USD Prim Path", "Compare USD prim paths (most accurate)"),
            ('OBJECT_NAME', "Object Name", "Compare object names (faster but less accurate)"),
            ('MESH_DATA', "Mesh Data", "Compare mesh vertex count and bounds (slowest but most thorough)")
        ],
        default='USD_PATH'
    )

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE

    def invoke(self, context, event):
        # Show properties dialog
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        
        # Get available captures
        available_captures = context.scene.get("_remix_available_captures", [])
        
        if not available_captures:
            layout.label(text="No captures scanned. Use 'Scan' first.", icon='ERROR')
            return
        
        layout.label(text=f"Found {len(available_captures)} captures to process:")
        
        # Show settings
        box = layout.box()
        box.label(text="Import Settings:", icon='SETTINGS')
        box.prop(self, "skip_duplicates")
        box.prop(self, "merge_collections")
        box.prop(self, "duplicate_detection_method")
        
        # Show capture list preview
        box = layout.box()
        box.label(text="Captures to Import:", icon='FILE_3D')
        
        # Show first few captures
        max_preview = 5
        for i, capture in enumerate(available_captures[:max_preview]):
            row = box.row()
            row.label(text=f"• {capture['name']} ({capture['size_mb']:.1f}MB)", icon='FILE')
        
        if len(available_captures) > max_preview:
            remaining = len(available_captures) - max_preview
            box.label(text=f"... and {remaining} more files")

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        available_captures = context.scene.get("_remix_available_captures", [])
        if not available_captures:
            self.report({'ERROR'}, "No captures available. Scan capture folder first.")
            return {'CANCELLED'}

        scene = context.scene
        
        # Track existing objects for duplicate detection
        existing_objects = self._build_existing_objects_map(context)
        
        # Import statistics
        total_captures = len(available_captures)
        imported_count = 0
        skipped_count = 0
        error_count = 0
        new_objects = []
        
        self.report({'INFO'}, f"Starting batch import of {total_captures} captures...")
        
        try:
            from .. import import_core
            
            for i, capture in enumerate(available_captures):
                capture_name = capture['name']
                capture_path = capture['full_path']
                
                # Update progress
                progress = (i + 1) / total_captures * 100
                print(f"Processing capture {i+1}/{total_captures} ({progress:.1f}%): {capture_name}")
                
                try:
                    # Import the capture
                    imported_objects, imported_lights, message = import_core.import_rtx_remix_usd_with_materials(
                        context,
                        capture_path,
                        scene.remix_capture_texture_dir_override,
                        scene.remix_capture_import_materials,
                        scene.remix_capture_import_lights,
                        scene.remix_capture_scene_scale
                    )
                    
                    if imported_objects is not None:
                        # Process imported objects for duplicates
                        processed_objects = self._process_imported_objects(
                            context, imported_objects, existing_objects, capture_name
                        )
                        
                        new_objects.extend(processed_objects)
                        imported_count += 1
                        
                        print(f"  ✓ Imported {len(processed_objects)} new objects from {capture_name}")
                        
                    else:
                        print(f"  ✗ Failed to import {capture_name}: {message}")
                        error_count += 1
                        
                except Exception as e:
                    print(f"  ✗ Error importing {capture_name}: {e}")
                    error_count += 1
                    continue
            
            # Final cleanup and organization
            if self.merge_collections and new_objects:
                self._organize_imported_objects(context, new_objects)
            
            # Report results
            total_new_objects = len(new_objects)
            self.report({'INFO'}, 
                f"Batch import complete: {imported_count} captures imported, "
                f"{total_new_objects} new objects, {skipped_count} duplicates skipped, "
                f"{error_count} errors"
            )
            
            print(f"Batch import summary:")
            print(f"  Captures processed: {imported_count}/{total_captures}")
            print(f"  New objects: {total_new_objects}")
            print(f"  Duplicates skipped: {skipped_count}")
            print(f"  Errors: {error_count}")
            
            return {'FINISHED'}
            
        except ImportError as e:
            self.report({'ERROR'}, f"Import core module not available: {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error during batch import: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}

    def _build_existing_objects_map(self, context):
        """Build a map of existing objects for duplicate detection."""
        existing_objects = {}
        
        for obj in bpy.data.objects:
            if obj.type not in ['MESH', 'LIGHT', 'CAMERA']:
                continue
                
            # Store different identifiers based on detection method
            if self.duplicate_detection_method == 'USD_PATH':
                if "usd_instance_path" in obj:
                    usd_path = obj["usd_instance_path"]
                    existing_objects[usd_path] = obj
                elif "usd_prim_path" in obj:
                    usd_path = obj["usd_prim_path"]
                    existing_objects[usd_path] = obj
                    
            elif self.duplicate_detection_method == 'OBJECT_NAME':
                # Use base name without suffixes
                base_name = obj.name.split('.')[0]  # Remove .001, .002 etc
                existing_objects[base_name] = obj
                
            elif self.duplicate_detection_method == 'MESH_DATA':
                if obj.type == 'MESH' and obj.data:
                    # Create signature based on vertex count and bounds
                    mesh = obj.data
                    vert_count = len(mesh.vertices)
                    if vert_count > 0:
                        # Get bounding box as a simple signature
                        bounds = [v for v in obj.bound_box[0]] + [v for v in obj.bound_box[6]]
                        signature = f"{vert_count}_{hash(tuple(bounds))}"
                        existing_objects[signature] = obj
        
        print(f"Built existing objects map with {len(existing_objects)} entries using {self.duplicate_detection_method}")
        return existing_objects

    def _process_imported_objects(self, context, imported_objects, existing_objects, capture_name):
        """Process imported objects to remove duplicates and track new ones."""
        new_objects = []
        
        for obj in imported_objects:
            is_duplicate = False
            
            if self.skip_duplicates:
                # Check for duplicates based on selected method
                if self.duplicate_detection_method == 'USD_PATH':
                    usd_path = obj.get("usd_instance_path") or obj.get("usd_prim_path")
                    if usd_path and usd_path in existing_objects:
                        is_duplicate = True
                        
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    base_name = obj.name.split('.')[0]
                    if base_name in existing_objects:
                        is_duplicate = True
                        
                elif self.duplicate_detection_method == 'MESH_DATA':
                    if obj.type == 'MESH' and obj.data:
                        mesh = obj.data
                        vert_count = len(mesh.vertices)
                        if vert_count > 0:
                            bounds = [v for v in obj.bound_box[0]] + [v for v in obj.bound_box[6]]
                            signature = f"{vert_count}_{hash(tuple(bounds))}"
                            if signature in existing_objects:
                                is_duplicate = True
            
            if is_duplicate:
                # Remove duplicate object
                print(f"    Removing duplicate: {obj.name}")
                bpy.data.objects.remove(obj, do_unlink=True)
            else:
                # Keep new object and add to tracking
                new_objects.append(obj)
                
                # Add to existing objects map for future duplicate detection
                if self.duplicate_detection_method == 'USD_PATH':
                    usd_path = obj.get("usd_instance_path") or obj.get("usd_prim_path")
                    if usd_path:
                        existing_objects[usd_path] = obj
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    base_name = obj.name.split('.')[0]
                    existing_objects[base_name] = obj
                elif self.duplicate_detection_method == 'MESH_DATA':
                    if obj.type == 'MESH' and obj.data:
                        mesh = obj.data
                        vert_count = len(mesh.vertices)
                        if vert_count > 0:
                            bounds = [v for v in obj.bound_box[0]] + [v for v in obj.bound_box[6]]
                            signature = f"{vert_count}_{hash(tuple(bounds))}"
                            existing_objects[signature] = obj
                
                # Tag object with source capture
                obj["remix_source_capture"] = capture_name
        
        return new_objects

    def _organize_imported_objects(self, context, new_objects):
        """Organize imported objects into collections."""
        if not new_objects:
            return
            
        # Find or create a "Batch Import" collection
        batch_collection_name = "RTX_Remix_Batch_Import"
        
        if batch_collection_name in bpy.data.collections:
            batch_collection = bpy.data.collections[batch_collection_name]
        else:
            batch_collection = bpy.data.collections.new(batch_collection_name)
            context.scene.collection.children.link(batch_collection)
        
        # Group objects by type
        mesh_objects = [obj for obj in new_objects if obj.type == 'MESH']
        light_objects = [obj for obj in new_objects if obj.type == 'LIGHT']
        camera_objects = [obj for obj in new_objects if obj.type == 'CAMERA']
        
        # Create sub-collections if needed
        for obj_type, objects in [('Meshes', mesh_objects), ('Lights', light_objects), ('Cameras', camera_objects)]:
            if not objects:
                continue
                
            sub_collection_name = f"{batch_collection_name}_{obj_type}"
            
            if sub_collection_name in bpy.data.collections:
                sub_collection = bpy.data.collections[sub_collection_name]
            else:
                sub_collection = bpy.data.collections.new(obj_type)
                batch_collection.children.link(sub_collection)
            
            # Move objects to sub-collection
            for obj in objects:
                # Remove from current collections
                for collection in obj.users_collection:
                    collection.objects.unlink(obj)
                # Add to target collection
                sub_collection.objects.link(obj)
        
        print(f"Organized {len(new_objects)} objects into {batch_collection_name} collection")

class BatchImportSelectedCaptures(bpy.types.Operator):
    """Import only the selected capture files while avoiding duplicates"""
    bl_idname = "remix.batch_import_selected_captures"
    bl_label = "Batch Import Selected Captures"
    bl_options = {'REGISTER', 'UNDO'}

    # Properties for batch import
    skip_duplicates: bpy.props.BoolProperty(
        name="Skip Duplicates",
        description="Skip assets that already exist in the scene based on USD prim path",
        default=True
    )
    
    merge_collections: bpy.props.BoolProperty(
        name="Merge Collections",
        description="Merge imported objects into existing collections instead of creating new ones",
        default=True
    )
    
    duplicate_detection_method: bpy.props.EnumProperty(
        name="Duplicate Detection",
        description="Method to detect duplicate assets",
        items=[
            ('USD_PATH', "USD Prim Path", "Compare USD prim paths (most accurate)"),
            ('OBJECT_NAME', "Object Name", "Compare object names (faster but less accurate)"),
            ('MESH_DATA', "Mesh Data", "Compare mesh vertex count and bounds (slowest but most thorough)")
        ],
        default='USD_PATH'
    )

    @classmethod
    def poll(cls, context):
        selected_captures = context.scene.get("_remix_batch_selected_captures", [])
        return USD_AVAILABLE and len(selected_captures) > 0

    def invoke(self, context, event):
        # Show properties dialog
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        
        # Get selected captures
        selected_captures = context.scene.get("_remix_batch_selected_captures", [])
        available_captures = context.scene.get("_remix_available_captures", [])
        
        # Filter to only selected captures
        selected_capture_data = [cap for cap in available_captures if cap['full_path'] in selected_captures]
        
        if not selected_capture_data:
            layout.label(text="No captures selected for batch import.", icon='ERROR')
            return
        
        layout.label(text=f"Selected {len(selected_capture_data)} captures to process:")
        
        # Show settings
        box = layout.box()
        box.label(text="Import Settings:", icon='SETTINGS')
        box.prop(self, "skip_duplicates")
        box.prop(self, "merge_collections")
        box.prop(self, "duplicate_detection_method")
        
        # Show capture list preview
        box = layout.box()
        box.label(text="Selected Captures:", icon='FILE_3D')
        
        # Show first few captures
        max_preview = 5
        for i, capture in enumerate(selected_capture_data[:max_preview]):
            row = box.row()
            row.label(text=f"• {capture['name']} ({capture['size_mb']:.1f}MB)", icon='FILE')
        
        if len(selected_capture_data) > max_preview:
            remaining = len(selected_capture_data) - max_preview
            box.label(text=f"... and {remaining} more files", icon='THREE_DOTS')

    def execute(self, context):
        # Get selected captures
        selected_captures = context.scene.get("_remix_batch_selected_captures", set())
        available_captures = context.scene.get("_remix_available_captures", [])
        
        # Filter to only selected captures
        selected_capture_data = [cap for cap in available_captures if cap['full_path'] in selected_captures]
        
        if not selected_capture_data:
            self.report({'ERROR'}, "No captures selected for batch import.")
            return {'CANCELLED'}
        
        # Use the same logic as the original batch import but only for selected captures
        return self._execute_batch_import(context, selected_capture_data)
    
    def _execute_batch_import(self, context, captures_to_import):
        """Execute the batch import for the given captures (reused from BatchImportCaptures)"""
        # This method contains the same logic as the original BatchImportCaptures.execute()
        # but operates on the filtered list of selected captures
        
        try:
            from .. import import_core
        except ImportError as e:
            self.report({'ERROR'}, f"Import core module not available: {e}")
            return {'CANCELLED'}

        # Get import settings from scene
        scene = context.scene
        texture_dir_override = scene.remix_capture_texture_dir_override
        import_materials = scene.remix_capture_import_materials
        import_lights = scene.remix_capture_import_lights
        scene_scale = scene.remix_capture_scene_scale

        # Build existing objects map for duplicate detection
        existing_objects = self._build_existing_objects_map(context) if self.skip_duplicates else {}

        # Track statistics
        total_captures = len(captures_to_import)
        imported_count = 0
        skipped_count = 0
        failed_count = 0
        total_new_objects = 0

        print(f"Starting batch import of {total_captures} selected captures...")
        
        for i, capture in enumerate(captures_to_import):
            capture_name = capture['name']
            capture_path = capture['full_path']
            
            print(f"Processing capture {i+1}/{total_captures}: {capture_name}")
            self.report({'INFO'}, f"Processing {i+1}/{total_captures}: {capture_name}")
            
            try:
                # Import the capture
                imported_objects, imported_lights, message = import_core.import_rtx_remix_usd_with_materials(
                    context,
                    capture_path,
                    texture_dir_override,
                    import_materials,
                    import_lights,
                    scene_scale
                )

                if imported_objects is not None:
                    # Process imported objects for duplicates
                    new_objects = self._process_imported_objects(context, imported_objects, existing_objects, capture_name)
                    
                    if new_objects:
                        # Organize objects into collections if requested
                        if self.merge_collections:
                            self._organize_imported_objects(context, new_objects)
                        
                        total_new_objects += len(new_objects)
                        imported_count += 1
                        print(f"  Successfully imported {len(new_objects)} new objects from {capture_name}")
                    else:
                        skipped_count += 1
                        print(f"  Skipped {capture_name} - all objects were duplicates")
                else:
                    failed_count += 1
                    print(f"  Failed to import {capture_name}: {message}")
                    
            except Exception as e:
                failed_count += 1
                print(f"  Error importing {capture_name}: {e}")
                import traceback
                traceback.print_exc()

        # Final report
        summary = f"Batch import complete: {imported_count} imported, {skipped_count} skipped, {failed_count} failed. Total new objects: {total_new_objects}"
        self.report({'INFO'}, summary)
        print(summary)
        
        # Clear selection after successful batch import
        if "_remix_batch_selected_captures" in context.scene:
            del context.scene["_remix_batch_selected_captures"]
        
        return {'FINISHED'}
    
    def _build_existing_objects_map(self, context):
        """Build a map of existing objects for duplicate detection (reused from BatchImportCaptures)"""
        existing_objects = {}
        
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                if self.duplicate_detection_method == 'USD_PATH':
                    # Use USD prim path if available
                    if obj.data and "usd_prim_path" in obj.data:
                        usd_path = obj.data["usd_prim_path"]
                        existing_objects[usd_path] = obj
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    # Use object name
                    existing_objects[obj.name] = obj
                elif self.duplicate_detection_method == 'MESH_DATA':
                    # Use mesh data characteristics
                    if obj.data:
                        vertex_count = len(obj.data.vertices)
                        bounds = tuple(obj.bound_box[0]) + tuple(obj.bound_box[6])  # Min and max corners
                        key = (vertex_count, bounds)
                        existing_objects[key] = obj
        
        return existing_objects
    
    def _process_imported_objects(self, context, imported_objects, existing_objects, capture_name):
        """Process imported objects and filter out duplicates (reused from BatchImportCaptures)"""
        new_objects = []
        
        for obj in imported_objects:
            if obj.type != 'MESH':
                new_objects.append(obj)  # Always keep non-mesh objects
                continue
            
            is_duplicate = False
            
            if self.skip_duplicates:
                if self.duplicate_detection_method == 'USD_PATH':
                    # Check USD prim path
                    if obj.data and "usd_prim_path" in obj.data:
                        usd_path = obj.data["usd_prim_path"]
                        if usd_path in existing_objects:
                            is_duplicate = True
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    # Check object name
                    if obj.name in existing_objects:
                        is_duplicate = True
                elif self.duplicate_detection_method == 'MESH_DATA':
                    # Check mesh data characteristics
                    if obj.data:
                        vertex_count = len(obj.data.vertices)
                        bounds = tuple(obj.bound_box[0]) + tuple(obj.bound_box[6])
                        key = (vertex_count, bounds)
                        if key in existing_objects:
                            is_duplicate = True
            
            if is_duplicate:
                # Remove duplicate object
                bpy.data.objects.remove(obj, do_unlink=True)
                print(f"    Removed duplicate object: {obj.name}")
            else:
                new_objects.append(obj)
                # Add to existing objects map for future duplicate detection
                if self.duplicate_detection_method == 'USD_PATH' and obj.data and "usd_prim_path" in obj.data:
                    existing_objects[obj.data["usd_prim_path"]] = obj
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    existing_objects[obj.name] = obj
                elif self.duplicate_detection_method == 'MESH_DATA' and obj.data:
                    vertex_count = len(obj.data.vertices)
                    bounds = tuple(obj.bound_box[0]) + tuple(obj.bound_box[6])
                    key = (vertex_count, bounds)
                    existing_objects[key] = obj
        
        return new_objects
    
    def _organize_imported_objects(self, context, new_objects):
        """Organize imported objects into collections (reused from BatchImportCaptures)"""
        # Find or create a "Batch Import" collection
        batch_collection_name = "Batch_Import_Captures"
        batch_collection = bpy.data.collections.get(batch_collection_name)
        
        if not batch_collection:
            batch_collection = bpy.data.collections.new(batch_collection_name)
            context.scene.collection.children.link(batch_collection)
        
        # Move objects to the batch collection
        for obj in new_objects:
            # Remove from current collections
            for collection in obj.users_collection:
                collection.objects.unlink(obj)
            
            # Add to batch collection
            batch_collection.objects.link(obj)