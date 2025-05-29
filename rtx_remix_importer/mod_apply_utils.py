import bpy
import math
import mathutils
from pxr import Usd, Sdf, UsdGeom, UsdShade, Gf, UsdLux
import os
import json
import hashlib

# --- Transform Helper ---
def get_blender_transform_matrix_from_mod(usd_prim_to_transform, current_xform_cache, is_y_up_in_mod, report_fn):
    try:
        local_to_world_gf = current_xform_cache.GetLocalToWorldTransform(usd_prim_to_transform)
        m = local_to_world_gf
        bl_matrix = mathutils.Matrix((
            (m[0][0], m[1][0], m[2][0], m[3][0]),
            (m[0][1], m[1][1], m[2][1], m[3][1]),
            (m[0][2], m[1][2], m[2][2], m[3][2]),
            (m[0][3], m[1][3], m[2][3], m[3][3])
        ))
        if is_y_up_in_mod:
            mat_yup_to_zup = mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')
            bl_matrix = mat_yup_to_zup @ bl_matrix
        return bl_matrix
    except Exception as e:
        report_fn({'WARNING'}, f"Error getting transform for {usd_prim_to_transform.GetPath()}: {e}")
        return mathutils.Matrix() # Return identity as fallback

# --- Mesh Data Helper (for new meshes from foreign mod.usda) ---
def get_mesh_data_from_mod(usd_mesh_prim_param, current_time_code, is_mod_y_up, report_fn):
    mesh_api = UsdGeom.Mesh(usd_mesh_prim_param)
    if not mesh_api: return None
    try:
        points_attr = mesh_api.GetPointsAttr()
        if not points_attr: return None
        verts = points_attr.Get(current_time_code)
        if not verts: return None
        verts = [(v[0], v[1], v[2]) for v in verts]
        if is_mod_y_up:
            verts = [(v[0], -v[2], v[1]) for v in verts]

        counts_attr = mesh_api.GetFaceVertexCountsAttr()
        indices_attr = mesh_api.GetFaceVertexIndicesAttr()
        if not counts_attr or not indices_attr: return None
        counts = counts_attr.Get(current_time_code)
        indices = indices_attr.Get(current_time_code)
        if not counts or not indices: return None

        faces = []
        current_idx = 0
        for count_val in counts:
            if count_val < 3: current_idx += count_val; continue
            face_indices = tuple(indices[current_idx : current_idx + count_val])
            if count_val == 3 or count_val == 4: faces.append(face_indices)
            else: # Triangulate N-gons
                for i in range(1, count_val - 1): faces.append((face_indices[0], face_indices[i], face_indices[i+1]))
            current_idx += count_val
        
        uvs_data_tuple, normals_data_tuple = None, None
        primvars_api = UsdGeom.PrimvarsAPI(mesh_api.GetPrim())
        st_primvar = primvars_api.GetPrimvar("st")
        if st_primvar:
            uv_values = st_primvar.Get(current_time_code)
            uv_indices_list = st_primvar.GetIndices(current_time_code)
            uv_interp = st_primvar.GetInterpolation()
            if uv_values is not None: uvs_data_tuple = (uv_values, uv_indices_list, uv_interp)
        
        normals_primvar = primvars_api.GetPrimvar("normals")
        if normals_primvar:
            norm_values = normals_primvar.Get(current_time_code)
            norm_indices_list = normals_primvar.GetIndices(current_time_code)
            norm_interp = normals_primvar.GetInterpolation()
            if norm_values is not None:
                if is_mod_y_up: norm_values = [Gf.Vec3f(n[0], -n[2], n[1]) for n in norm_values] # Ensure Gf.Vec3f for list comp
                normals_data_tuple = (norm_values, norm_indices_list, norm_interp)
        return verts, faces, uvs_data_tuple, normals_data_tuple
    except Exception as e_mesh_data:
        report_fn({'WARNING'}, f"Error in get_mesh_data_from_mod for <{usd_mesh_prim_param.GetPath()}>: {e_mesh_data}")
        return None

# --- Light Creation Helper (for new lights from foreign mod.usda) ---
def create_new_blender_light_from_mod(usd_light_prim, time_code_param, scene_scale_param, report_fn):
    # Note: Transform is applied separately after this function returns the object.
    # current_context, xform_cache_param, is_y_up_in_mod_param removed as transform is external now.
    light_name_usd = usd_light_prim.GetName()
    bl_light_name = bpy.path.clean_name(light_name_usd if light_name_usd else str(usd_light_prim.GetPath()).split("/")[-1]) + "_mod_light"
    bl_light_data_name = bl_light_name + "_data"

    bl_type = 'POINT' # Default
    if usd_light_prim.IsA(UsdLux.SphereLight): bl_type = 'POINT'
    elif usd_light_prim.IsA(UsdLux.RectLight): bl_type = 'AREA'
    elif usd_light_prim.IsA(UsdLux.DistantLight): bl_type = 'SUN'
    elif usd_light_prim.IsA(UsdLux.SpotLight): bl_type = 'SPOT'
    elif usd_light_prim.IsA(UsdLux.DomeLight): 
        report_fn({'INFO'}, f"USD DomeLight <{usd_light_prim.GetPath()}> mapped to Blender SUN light. For IBL, consider environment textures.")
        bl_type = 'SUN'
    elif usd_light_prim.IsA(UsdLux.CylinderLight):
        report_fn({'INFO'}, f"USD CylinderLight <{usd_light_prim.GetPath()}> mapped to Blender AREA light (approximation).")
        bl_type = 'AREA'
    elif usd_light_prim.IsA(UsdLux.PortalLight):
        report_fn({'INFO'}, f"USD PortalLight <{usd_light_prim.GetPath()}> mapped to Blender AREA light.")
        bl_type = 'AREA'
    elif usd_light_prim.IsA(UsdLux.DiskLight):
        report_fn({'INFO'}, f"USD DiskLight <{usd_light_prim.GetPath()}> mapped to Blender POINT light with sphere shape.")
        bl_type = 'POINT'
    
    bl_light_data = bpy.data.lights.new(name=bl_light_data_name, type=bl_type)
    light_api = UsdLux.LightAPI(usd_light_prim)

    if light_api.GetColorAttr().IsDefined(): bl_light_data.color = light_api.GetColorAttr().Get(time_code_param)
    intensity = light_api.GetIntensityAttr().Get(time_code_param) if light_api.GetIntensityAttr().IsDefined() else 1.0
    exposure = light_api.GetExposureAttr().Get(time_code_param) if light_api.GetExposureAttr().IsDefined() else 0.0
    bl_light_data.energy = intensity * pow(2, exposure)

    if light_api.GetEnableColorTemperatureAttr().Get(time_code_param) and light_api.GetColorTemperatureAttr().IsDefined():
        bl_light_data.use_custom_color_temp = True
        bl_light_data.color_temperature = light_api.GetColorTemperatureAttr().Get(time_code_param)
    
    if usd_light_prim.IsA(UsdLux.SphereLight):
        sphere_light_api = UsdLux.SphereLight(usd_light_prim)
        # Set shape only if the attribute exists (newer Blender versions)
        if hasattr(bl_light_data, 'shape'):
            bl_light_data.shape = 'SPHERE'
        if hasattr(bl_light_data, 'size'):
            bl_light_data.size = sphere_light_api.GetRadiusAttr().Get(time_code_param) * 2.0 * scene_scale_param if sphere_light_api.GetRadiusAttr().IsDefined() else 0.1 * scene_scale_param
        if sphere_light_api.GetTreatAsPointAttr().Get(time_code_param) and hasattr(bl_light_data, 'size'):
             bl_light_data.size = 0.0
    elif usd_light_prim.IsA(UsdLux.RectLight):
        rect_light_api = UsdLux.RectLight(usd_light_prim)
        # Set shape only if the attribute exists (newer Blender versions)
        if hasattr(bl_light_data, 'shape'):
            bl_light_data.shape = 'RECTANGLE'
        if hasattr(bl_light_data, 'size'):
            bl_light_data.size = rect_light_api.GetWidthAttr().Get(time_code_param) * scene_scale_param if rect_light_api.GetWidthAttr().IsDefined() else 1.0 * scene_scale_param
        if hasattr(bl_light_data, 'size_y'):
            bl_light_data.size_y = rect_light_api.GetHeightAttr().Get(time_code_param) * scene_scale_param if rect_light_api.GetHeightAttr().IsDefined() else 1.0 * scene_scale_param
    elif usd_light_prim.IsA(UsdLux.SpotLight):
        spot_api = UsdLux.SpotLight(usd_light_prim)
        if hasattr(bl_light_data, 'spot_size'):
            bl_light_data.spot_size = math.radians(spot_api.GetShapingConeAngleAttr().Get(time_code_param)) if spot_api.GetShapingConeAngleAttr().IsDefined() else math.radians(45)
        if hasattr(bl_light_data, 'spot_blend'):
            bl_light_data.spot_blend = spot_api.GetShapingConeSoftnessAttr().Get(time_code_param) if spot_api.GetShapingConeSoftnessAttr().IsDefined() else 0.15
    elif usd_light_prim.IsA(UsdLux.DiskLight):
        disk_api = UsdLux.DiskLight(usd_light_prim)
        # Set shape only if the attribute exists (newer Blender versions)
        if hasattr(bl_light_data, 'shape'):
            bl_light_data.shape = 'DISK'
        if hasattr(bl_light_data, 'size'):
            bl_light_data.size = disk_api.GetRadiusAttr().Get(time_code_param) * 2.0 * scene_scale_param if disk_api.GetRadiusAttr().IsDefined() else 0.1 * scene_scale_param

    new_bl_light_obj = bpy.data.objects.new(name=bl_light_name, object_data=bl_light_data)
    new_bl_light_obj["usd_instance_path"] = str(usd_light_prim.GetPath())
    return new_bl_light_obj

# --- Material-related helpers ---
# Caches and constants that will be module-level here
_TEXTURE_CACHE_MOD_APPLY = {} # Specific cache for this module\'s texture loading
_APERTURE_OPAQUE_NODE_GROUP_LOADED_MOD_APPLY = False # Specific flag
APERTURE_OPAQUE_NODE_GROUP_NAME_CONST = "Aperture Opaque" # Shared constant name

def resolve_mod_material_asset_path_util(asset_path, texture_resolution_context_path_param, mod_file_path_param, report_fn):
    # texture_resolution_context_path_param is the primary base (e.g. project root for textures)
    # mod_file_path_param is the path of the .usda mod file being processed (for textures relative to it)
    if not asset_path or asset_path.startswith("#") or asset_path.startswith("/"):
        return asset_path
    asset_path = asset_path.strip('@').replace("\\", "/")
    if os.path.isabs(asset_path) and os.path.exists(asset_path): return asset_path

    search_paths = []
    if texture_resolution_context_path_param and os.path.isdir(texture_resolution_context_path_param):
        search_paths.append(texture_resolution_context_path_param)
        search_paths.append(os.path.join(texture_resolution_context_path_param, "assets"))
        search_paths.append(os.path.join(texture_resolution_context_path_param, "textures"))
        search_paths.append(os.path.join(texture_resolution_context_path_param, "assets", "textures"))
    
    mod_file_dir = os.path.dirname(mod_file_path_param)
    if mod_file_dir not in search_paths:
        search_paths.insert(0, mod_file_dir) # Prioritize mod file\'s own directory and its assets/textures
        search_paths.insert(1, os.path.join(mod_file_dir, "assets"))
        search_paths.insert(2, os.path.join(mod_file_dir, "textures"))
        search_paths.insert(3, os.path.join(mod_file_dir, "assets", "textures"))

    for root_dir in search_paths:
        if not root_dir or not os.path.isdir(root_dir): continue
        
        current_test_path = os.path.normpath(os.path.join(root_dir, asset_path))
        if os.path.exists(current_test_path): return current_test_path

        # Heuristic for ../assets/ type paths, try from one level above the root_dir
        if asset_path.startswith("../"):
            higher_root = os.path.dirname(root_dir)
            heuristic_path = os.path.normpath(os.path.join(higher_root, asset_path.lstrip("../")))
            if os.path.exists(heuristic_path): return heuristic_path
        else: # Check common subfolders if not an explicitly relative path like ../
            basename_asset = os.path.basename(asset_path)
            for subfolder in ["textures", "assets/textures", "assets"]:
                sub_test_path = os.path.normpath(os.path.join(root_dir, subfolder, basename_asset))
                if os.path.exists(sub_test_path): return sub_test_path

    # report_fn({'DEBUG'}, f"Texture path not resolved: {asset_path} with primary context {texture_resolution_context_path_param}")
    return asset_path # Return original if not found

def load_mod_texture_util(image_path, is_normal=False, is_non_color=False, report_fn=print):
    global _TEXTURE_CACHE_MOD_APPLY
    abs_image_path = bpy.path.abspath(image_path)
    if abs_image_path in _TEXTURE_CACHE_MOD_APPLY:
        return _TEXTURE_CACHE_MOD_APPLY[abs_image_path]
    try:
        img = bpy.data.images.load(abs_image_path, check_existing=True)
        img.colorspace_settings.name = 'Non-Color' if (is_normal or is_non_color) else 'sRGB'
        _TEXTURE_CACHE_MOD_APPLY[abs_image_path] = img
        return img
    except RuntimeError as e:
        report_fn({'WARNING'}, f"Error loading texture '{abs_image_path}': {e}. Stub image will be used.")
        return None

def append_mod_aperture_opaque_node_group_util(report_fn):
    global _APERTURE_OPAQUE_NODE_GROUP_LOADED_MOD_APPLY
    if APERTURE_OPAQUE_NODE_GROUP_NAME_CONST in bpy.data.node_groups:
        return bpy.data.node_groups[APERTURE_OPAQUE_NODE_GROUP_NAME_CONST]
    if _APERTURE_OPAQUE_NODE_GROUP_LOADED_MOD_APPLY: return None
    
    _APERTURE_OPAQUE_NODE_GROUP_LOADED_MOD_APPLY = True
    addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    blend_file_path = os.path.join(addon_root, "nodes", "ApertureOpaque.blend")
    if not os.path.exists(blend_file_path):
        report_fn({'ERROR'}, f"ApertureOpaque.blend not found at {blend_file_path}")
        return None
    try:
        with bpy.data.libraries.load(blend_file_path, link=False) as (data_from, data_to):
            if APERTURE_OPAQUE_NODE_GROUP_NAME_CONST in data_from.node_groups:
                data_to.node_groups = [APERTURE_OPAQUE_NODE_GROUP_NAME_CONST]
            else: return None
    except Exception as e:
        report_fn({'ERROR'}, f"Failed to load Aperture Opaque node group: {e}")
        return None
    return bpy.data.node_groups.get(APERTURE_OPAQUE_NODE_GROUP_NAME_CONST)

def create_mod_default_blender_material_util(name, report_fn):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (300, 0)
    aperture_group = append_mod_aperture_opaque_node_group_util(report_fn)
    if not aperture_group:
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0,0); links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])
        return mat, bsdf
    group_node = nodes.new(type='ShaderNodeGroup'); group_node.node_tree = aperture_group
    group_node.name = APERTURE_OPAQUE_NODE_GROUP_NAME_CONST; group_node.location = (0, 0)
    if 'BSDF' in group_node.outputs: links.new(group_node.outputs['BSDF'], output_node.inputs['Surface'])
    if 'Displacement' in group_node.outputs: links.new(group_node.outputs['Displacement'], output_node.inputs['Displacement'])
    return mat, group_node

def get_mod_input_value_util(shader_prim_instance, input_name_str):
    shader_input = shader_prim_instance.GetInput(input_name_str)
    if not shader_input or not shader_input.IsDefined() or not shader_input.HasValue(): return None
    return shader_input.Get()

def process_mod_input_util(usd_input_val, input_type_name, nodes, links, target_node, target_socket_name, 
                           texture_res_context_path, mod_file_path_for_tex, # For resolve_mod_material_asset_path_util
                           node_pos=(-400,0), is_normal=False, is_non_color=False, report_fn=print):
    target_socket = target_node.inputs.get(target_socket_name)
    if not target_socket or usd_input_val is None: return None
    created_node = None
    texture_node_x, normal_map_node_x = target_node.location.x - 400, target_node.location.x - 150
    if isinstance(usd_input_val, (str, Sdf.AssetPath)):
        path_str = str(usd_input_val).strip('@')
        is_likely_texture_path = '../' in path_str or 'assets/' in path_str or \
                                any(path_str.lower().endswith(ext) for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tiff'])
        if is_likely_texture_path:
            resolved_path = resolve_mod_material_asset_path_util(path_str, texture_res_context_path, mod_file_path_for_tex, report_fn)
            if resolved_path and os.path.exists(resolved_path):
                image = load_mod_texture_util(resolved_path, is_normal, is_non_color, report_fn)
                if image:
                    tex_node = nodes.new(type='ShaderNodeTexImage'); tex_node.image = image
                    tex_node.label = f"{input_type_name.replace('_', ' ').title()} Texture"; tex_node.location = (texture_node_x, node_pos[1])
                    out_sock_name = 'Alpha' if (is_non_color or is_normal) and target_socket.type=='VALUE' and 'Alpha' in tex_node.outputs else 'Color'
                    if is_normal:
                        nm = nodes.new(type='ShaderNodeNormalMap'); nm.location = (normal_map_node_x, node_pos[1])
                        links.new(tex_node.outputs[out_sock_name], nm.inputs['Color']); links.new(nm.outputs['Normal'], target_socket)
                        created_node = nm
                    else:
                        links.new(tex_node.outputs[out_sock_name], target_socket); created_node = tex_node
    elif isinstance(usd_input_val, Gf.Vec3f) and target_socket.type == 'RGBA': target_socket.default_value = (usd_input_val[0], usd_input_val[1], usd_input_val[2], 1.0)
    elif isinstance(usd_input_val, Gf.Vec4f) and target_socket.type == 'RGBA': target_socket.default_value = tuple(usd_input_val)
    elif isinstance(usd_input_val, (int, float)) and target_socket.type == 'VALUE': target_socket.default_value = float(usd_input_val)
    elif isinstance(usd_input_val, bool) and target_socket.type == 'VALUE': target_socket.default_value = 1.0 if usd_input_val else 0.0
    return created_node

def process_mod_pbr_util(shader_prim_instance, bl_mat_instance, main_shader_node_instance, 
                                    texture_res_context_path_p, mod_file_path_for_tex_p, report_fn):
    nodes, links = bl_mat_instance.node_tree.nodes, bl_mat_instance.node_tree.links
    input_map = {
        "Albedo Color": ["inputs:diffuse_texture", "diffuse_texture", "diffuse_color_constant"],
        "Opacity": ["inputs:opacity_texture", "opacity_texture", "opacity_constant", "inputs:opacity", "opacity"],
        "Roughness": ["inputs:reflectionroughness_texture", "reflectionroughness_texture", "reflection_roughness_constant"],
        "Metallic": ["inputs:metallic_texture", "metallic_texture", "metallic_constant"],
        "Normal Map": ["inputs:normalmap_texture", "normalmap_texture"],
        "Height Map": ["inputs:height_texture", "height_texture", "height_constant"],
        "Enable Emission": ["inputs:enable_emission"],
        "Emissive Color": ["inputs:emissive_mask_texture", "emissive_mask_texture", "emissive_color_constant"],
        "Emissive Intensity": ["inputs:emissive_intensity", "emissive_intensity"],
    }
    base_y, y_off, spacing = main_shader_node_instance.location.y, 200, 250
    material_usd_def_dir = os.path.dirname(shader_prim_instance.GetPrim().GetStage().GetRootLayer().realPath) # Dir of USD defining this shader\'s material
    for grp_sock, usd_names in input_map.items():
        if not main_shader_node_instance.inputs.get(grp_sock): continue
        val, name_fnd = None, None
        for name in usd_names: 
            val = get_mod_input_value_util(shader_prim_instance, name)
            if val is not None: 
                name_fnd = name
                break
        if val is not None:
            is_n, is_nc = (grp_sock == "Normal Map"), grp_sock in ["Metallic", "Roughness", "Opacity", "Height Map", "Emissive Intensity"]
            if process_mod_input_util(val, name_fnd, nodes, links, main_shader_node_instance, grp_sock, 
                                      texture_res_context_path_p, mod_file_path_for_tex_p, 
                                      (-400, base_y + y_off), is_n, is_nc, report_fn):
                y_off -= spacing
    op_s, alb_s = main_shader_node_instance.inputs.get("Opacity"), main_shader_node_instance.inputs.get("Albedo Color")
    if op_s and not op_s.is_linked and alb_s and alb_s.is_linked:
        alb_n = alb_s.links[0].from_node
        if alb_n.type == 'TEX_IMAGE' and 'Alpha' in alb_n.outputs: links.new(alb_n.outputs['Alpha'], op_s)
    em_c, em_i = main_shader_node_instance.inputs.get("Emissive Color"), main_shader_node_instance.inputs.get("Emissive Intensity")
    en_em = main_shader_node_instance.inputs.get("Enable Emission")
    usd_en_em = get_mod_input_value_util(shader_prim_instance, "inputs:enable_emission")
    expl_dis = isinstance(usd_en_em, bool) and not usd_en_em
    if em_c and em_i and em_c.is_linked and not em_i.is_linked and not expl_dis:
        if not (en_em and isinstance(en_em.default_value, (float, int, bool)) and not en_em.default_value) and em_i.default_value == 0.0: 
            em_i.default_value = 1.0

def create_mod_material_nodes_util(material_usd_path_str, current_mod_stage, 
                                   texture_res_context_path_p, mod_file_path_for_tex_p, report_fn):
    mat_prim = current_mod_stage.GetPrimAtPath(material_usd_path_str)
    if not mat_prim or not mat_prim.IsA(UsdShade.Material): return None, None
    mat_name = bpy.path.clean_name(mat_prim.GetName() or os.path.basename(material_usd_path_str))
    bl_mat, main_node = create_mod_default_blender_material_util(f"{mat_name}_mod_override", report_fn)
    surf_out = UsdShade.Material(mat_prim).GetSurfaceOutput()
    if surf_out and surf_out.HasConnectedSource():
        src_path = surf_out.GetConnectedSource()[0].GetPrim().GetPath()
        shader_prim = UsdShade.Shader(current_mod_stage.GetPrimAtPath(src_path))
        if shader_prim:
            process_mod_pbr_util(shader_prim, bl_mat, main_node, 
                                            texture_res_context_path_p, mod_file_path_for_tex_p, report_fn)
    return bl_mat, main_node

def get_or_create_mod_instance_material_util(base_material_usd_path, instance_prim_for_metadata, current_mod_stage, 
                                             texture_res_context_path_p, mod_file_path_for_tex_p, 
                                             mod_base_material_node_cache_param, # Specific cache for (mat, node) tuples
                                             local_material_cache_param, # Cache for final Blender materials
                                             report_fn):
    instance_metadata = {}
    if instance_prim_for_metadata:
        over_mesh_in_mod = instance_prim_for_metadata.GetChild("mesh")
        if over_mesh_in_mod:
            for prop in over_mesh_in_mod.GetAuthoredPropertiesInNamespace("primvars:_remix_metadata"): 
                instance_metadata[prop.GetBaseName()] = prop.Get()
    meta_hash = hashlib.md5(json.dumps(instance_metadata, sort_keys=True).encode('utf-8')).hexdigest()[:8] if instance_metadata else ""
    unique_key = f"{base_material_usd_path}_{meta_hash}" if meta_hash else base_material_usd_path
    if unique_key in local_material_cache_param: return local_material_cache_param[unique_key]

    bl_mat_base_tuple = mod_base_material_node_cache_param.get(base_material_usd_path)
    if not bl_mat_base_tuple:
        bl_mat_base_tuple = create_mod_material_nodes_util(base_material_usd_path, current_mod_stage, 
                                                             texture_res_context_path_p, mod_file_path_for_tex_p, report_fn)
        if not bl_mat_base_tuple or not bl_mat_base_tuple[0]:
             report_fn({'ERROR'}, f"Failed to create base material structure for mod override: {base_material_usd_path}")
             return None
        mod_base_material_node_cache_param[base_material_usd_path] = bl_mat_base_tuple
    
    base_bl_mat, _ = bl_mat_base_tuple # Don't need main_shader_node here
    final_bl_material = base_bl_mat
    if instance_metadata:
        final_name = f"{base_bl_mat.name}_{meta_hash[:4]}"
        existing_copy = bpy.data.materials.get(final_name)
        if existing_copy: final_bl_material = existing_copy
        else: 
            final_bl_material = base_bl_mat.copy(); final_bl_material.name = final_name
        
        # Apply actual metadata overrides to the shader group inputs
        shader_group_node = None
        if final_bl_material.node_tree:
            for node in final_bl_material.node_tree.nodes:
                if node.type == 'GROUP' and node.node_tree and node.node_tree.name == APERTURE_OPAQUE_NODE_GROUP_NAME_CONST:
                    shader_group_node = node
                    break
        
        if shader_group_node:
            metadata_to_socket_map = {
                # Standard PBR
                "albedo_color_constant": "Albedo Color",
                "diffuse_color_constant": "Albedo Color", 
                "opacity_constant": "Opacity",
                "roughness_constant": "Roughness",
                "metallic_constant": "Metallic",
                # Normal Map (texture handled by process_mod_input_util, this would be for a constant vector if ever used)
                # "normal_map_constant": "Normal Map", 

                # Iridescence
                "enable_iridescence": "Enable Iridescence",
                # "iridescence_thickness_constant": "Iridescence Thickness", # If you have these inputs
                # "iridescence_ior_constant": "Iridescence IOR",
                
                # Opacity/Thickness Link
                "use_opacity_as_thickness": "Use Opacity As Thickness",
                "thickness_constant": "Thickness", 

                # Emission
                "enable_emission": "Enable Emission",
                "emissive_color_constant": "Emissive Color",
                "emissive_intensity": "Emissive Intensity",

                # Animation - Sprite Sheets
                "sprite_sheet_fps": "Sprite Sheet FPS",
                "sprite_sheet_cols": "Sprite Sheet Columns",
                "sprite_sheet_rows": "Sprite Sheet Rows",

                # Remix Flags
                "preload_textures": "Preload Textures",
                "ignore_material": "Ignore Material",

                # Alpha Blending
                "use_legacy_alpha_blend": "Use Legacy Alpha Blend",
                "blend_enabled": "Blend Enabled", # Or maps to specific alpha mode enable
                # "blend_type": "Blend Type", # If an enum, needs careful handling
                "alpha_test_threshold": "Alpha Test Threshold",

                # Displacement
                "height_map_scale": "Height Map Scale", # Assuming "Height M" is a scale for a map
                "height_constant": "Height Map Scale", # Or if "Height M" is a direct constant height value
                "inwards_displacement": "Inwards Displacement",
                "outwards_displacement": "Outwards Displacement",
            }
            applied_any_metadata = False
            for meta_key, meta_value in instance_metadata.items():
                socket_name = metadata_to_socket_map.get(meta_key)
                if not socket_name:
                    # report_fn({'DEBUG'}, f"Metadata key '{meta_key}' not mapped for material '{final_bl_material.name}'")
                    continue

                if socket_name in shader_group_node.inputs:
                    socket = shader_group_node.inputs[socket_name]
                    try:
                        current_val = socket.default_value
                        val_changed = False
                        if socket.type == 'RGBA' and isinstance(meta_value, (Gf.Vec3f, Gf.Vec4f, tuple, list)) and len(meta_value) >= 3:
                            new_val = (meta_value[0], meta_value[1], meta_value[2], meta_value[3] if len(meta_value) == 4 else 1.0)
                            # Compare component-wise for colors due to potential float precision issues with direct tuple comparison
                            if not all(abs(a-b) < 1e-5 for a,b in zip(list(current_val), list(new_val))):
                                socket.default_value = new_val
                                val_changed = True
                        elif socket.type == 'VALUE' and isinstance(meta_value, (float, int, bool)):
                            new_float_val = float(meta_value)
                            if isinstance(current_val, float) and abs(current_val - new_float_val) > 1e-5 or current_val != new_float_val:
                                socket.default_value = new_float_val
                                val_changed = True
                        elif socket.type == 'VECTOR' and isinstance(meta_value, (Gf.Vec3f, tuple, list)) and len(meta_value) == 3:
                            new_vec_val = tuple(meta_value)
                            if not all(abs(a-b) < 1e-5 for a,b in zip(list(current_val), new_vec_val)):
                                socket.default_value = new_vec_val
                                val_changed = True
                        # Add more type checks (e.g., for BOOLEAN if socket.type == 'BOOLEAN')
                        # elif socket.type == 'BOOLEAN' and isinstance(meta_value, bool):
                        #    if socket.default_value != meta_value: socket.default_value = meta_value; val_changed = True
                        else:
                            report_fn({'WARNING'}, f"Metadata key '{meta_key}' type '{type(meta_value).__name__}' unhandled or mismatch for socket '{socket_name}' (type: {socket.type}) in mat '{final_bl_material.name}'.")
                        
                        if val_changed:
                            report_fn({'INFO'}, f"  Applied metadata: Mat '{final_bl_material.name}', Socket '{socket_name}' = {socket.default_value} (from {meta_key})")
                            applied_any_metadata = True

                    except Exception as e_meta:
                        report_fn({'ERROR'}, f"Error applying metadata '{meta_key}' to '{socket_name}' for mat '{final_bl_material.name}': {e_meta}")
                # else:
                    # report_fn({'DEBUG'}, f"Socket '{socket_name}' (for meta '{meta_key}') not found in group '{shader_group_node.name}' in mat '{final_bl_material.name}'.")
            
            if applied_any_metadata:
                 pass # Already reported per-socket change
        else:
            report_fn({'WARNING'}, f"Could not find '{APERTURE_OPAQUE_NODE_GROUP_NAME_CONST}' in mat '{final_bl_material.name}' to apply metadata.")
        # report_fn({'DEBUG'}, f"Material '{final_bl_material.name}' for instance with metadata.") # This was the old line, replaced by per-socket logs

    local_material_cache_param[unique_key] = final_bl_material
    return final_bl_material

# --- Utility to clear module-level caches if needed (e.g., before a new operator run) ---
def clear_mod_apply_caches():
    global _TEXTURE_CACHE_MOD_APPLY, _APERTURE_OPAQUE_NODE_GROUP_LOADED_MOD_APPLY
    _TEXTURE_CACHE_MOD_APPLY.clear()
    _APERTURE_OPAQUE_NODE_GROUP_LOADED_MOD_APPLY = False
    # _mod_base_material_node_cache is managed per-operator run by passing it as arg, so not cleared here.
    print("Cleared mod apply utility caches (texture, node group loaded state).") 