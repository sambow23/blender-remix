import bpy
import os
import math
import mathutils
from ... import mod_apply_utils
from ...core_utils import (
    calc_normals_split_compatible, 
    set_mesh_auto_smooth_compatible, 
    set_custom_normals_compatible
)

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
                                set_mesh_auto_smooth_compatible(bl_mesh_data, True) # Required for custom normals
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
                                                set_custom_normals_compatible(bl_mesh_data, loop_normals)
                                            except Exception as e_norm: # Catch potential errors during set
                                                self.report({'ERROR'}, f"Failed to set custom normals for <{prim_path}>: {e_norm}")
                                                calc_normals_split_compatible(bl_mesh_data) # Fallback if error
                                        else:
                                            self.report({'DEBUG'}, f"Normals not processed successfully for <{prim_path}>, calculating default.")
                                            calc_normals_split_compatible(bl_mesh_data) # Fallback
                                    else:
                                        if not norm_values: self.report({'DEBUG'}, f"No Normal values for new mesh <{prim_path}>")
                                        if not bl_mesh_data.loops: self.report({'DEBUG'}, f"Mesh <{prim_path}> has no loops for Normals.")
                                        calc_normals_split_compatible(bl_mesh_data) # Fallback if loops or values missing
                                else:
                                    self.report({'DEBUG'}, f"No normals_data tuple for new mesh <{prim_path}>, calculating default.")
                                    calc_normals_split_compatible(bl_mesh_data) # Fallback if no USD normals

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