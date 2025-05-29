import bpy
import os
import traceback
import math
import mathutils
try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

if USD_AVAILABLE:
    from .material_utils import create_material, get_or_create_instance_material
    from .light_utils import import_lights_from_usd
    from .texture_utils import find_texture_path


class USDStageContext:
    """Context object to hold USD stage data and shared resources."""
    def __init__(self, stage, usd_file_path, scene_scale):
        self.stage = stage
        self.usd_file_path = usd_file_path
        self.scene_scale = scene_scale
        self.time_code = Usd.TimeCode.Default()
        self.xform_cache = UsdGeom.XformCache(self.time_code)
        self.up_axis_is_y = (UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y)
        
        # Data storage
        self.created_objects = set()
        self.created_lights_set = set()
        self.created_cameras_set = set()
        self.blender_materials = {}  # Maps USD material path -> Blender material
        self.material_map = {}  # Maps USD material path -> UsdShade.Material
        self.base_mesh_data = {}  # Map USD mesh path -> Blender mesh data
        self.material_cache = {}  # Map unique material key -> Blender material


class USDImportError(Exception):
    """Custom exception for USD import errors."""
    pass


def find_texture_dir(usd_file_path):
    """Attempt to automatically locate the texture directory."""
    usd_dir = os.path.dirname(usd_file_path)
    mod_dir = os.path.dirname(usd_dir)
    mod_root_dir = os.path.dirname(mod_dir)

    potential_dirs = [
        os.path.join(mod_dir, "captures", "textures"),
        os.path.join(mod_dir, "assets", "textures"),
        os.path.join(mod_dir, "assets"),
        os.path.join(mod_root_dir, "assets", "textures"),
        os.path.join(mod_root_dir, "assets"),
        os.path.join(mod_root_dir, "textures"),
    ]

    for p_dir in potential_dirs:
        if os.path.isdir(p_dir):
            print(f"Auto-detected texture directory: {p_dir}")
            return p_dir

    print("Warning: Could not auto-detect texture directory. Texture paths might not resolve correctly.")
    return None


def setup_texture_directory(usd_file_path):
    """Setup and validate texture directory."""
    texture_dir = None  # Initialize texture_dir variable 
    if not texture_dir or not os.path.isdir(texture_dir):
        print("No valid texture directory override specified, attempting auto-detection...")
        texture_dir = find_texture_dir(usd_file_path)
    
    if texture_dir:
        print(f"Using texture directory: {texture_dir}")
    else:
        print("Warning: No texture directory found or specified. Relative texture paths may fail.")
    
    return texture_dir


def open_usd_stage(usd_file_path):
    """Open and validate USD stage."""
    try:
        print(f"Opening USD stage: {usd_file_path}")
        stage = Usd.Stage.Open(usd_file_path, Usd.Stage.LoadAll)
        if not stage:
            raise USDImportError(f"Failed to open USD stage: {usd_file_path}")
        
        print("USD Stage opened successfully.")
        
        # Print stage info
        up_axis = UsdGeom.GetStageUpAxis(stage)
        meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        print(f"  Stage Info: UpAxis='{up_axis}', MetersPerUnit={meters_per_unit:.4f}")
        if up_axis == UsdGeom.Tokens.y:
            print("  NOTE: Stage is Y-Up. Transforms will be converted to Blender's Z-Up.")
        
        return stage
        
    except Exception as e:
        raise USDImportError(f"Error opening USD stage '{usd_file_path}': {e}")


def setup_blender_collections(context, usd_file_path):
    """Setup Blender collections for organizing imported content."""
    collection_name = bpy.path.clean_name(os.path.basename(usd_file_path).split('.')[0])
    main_collection = context.scene.collection
    
    # Main import collection
    import_collection = bpy.data.collections.get(collection_name)
    if not import_collection:
        import_collection = bpy.data.collections.new(collection_name)
        main_collection.children.link(import_collection)
        print(f"Created import collection: '{collection_name}'")
    else:
        print(f"Using existing import collection: '{collection_name}'")

    # Sub-collections
    sub_collections = {}
    for sub_name in ["Meshes", "Lights", "Cameras"]:
        sub_collection_name = f"{collection_name}_{sub_name}"
        sub_collection = bpy.data.collections.get(sub_collection_name)
        if not sub_collection:
            sub_collection = bpy.data.collections.new(sub_name)
            import_collection.children.link(sub_collection)
            print(f"  Created sub-collection: '{sub_collection.name}' inside '{import_collection.name}'")
        else:
            print(f"  Using existing sub-collection: '{sub_collection.name}'")
        sub_collections[sub_name.lower()] = sub_collection

    # Set active layer collection
    try:
        layer_collection = context.view_layer.layer_collection
        for lc in layer_collection.children:
            if lc.collection == import_collection:
                context.view_layer.active_layer_collection = lc
                break
    except Exception as e:
        print(f"Warning: Could not set active layer collection: {e}")

    return import_collection, sub_collections


def get_mesh_data(usd_mesh_prim, context):
    """Extract vertices, faces, UVs, normals from a UsdGeom.Mesh prim."""
    mesh = UsdGeom.Mesh(usd_mesh_prim)
    if not mesh:
        return None

    try:
        time_code = context.time_code
        up_axis_is_y = context.up_axis_is_y
        
        # Get vertices
        points_attr = mesh.GetPointsAttr()
        if not points_attr:
            print(f"  Warning: No points attribute found on mesh {usd_mesh_prim.GetPath()}")
            return None
        verts = points_attr.Get(time_code)
        if not verts:
            print(f"  Warning: No vertex data found at time {time_code} for mesh {usd_mesh_prim.GetPath()}")
            return None
        verts = [(v[0], v[1], v[2]) for v in verts]

        # Apply Y-up to Z-up correction to vertices if needed
        if up_axis_is_y:
            verts = [(v[0], -v[2], v[1]) for v in verts]

        # Get face vertex counts and indices
        counts_attr = mesh.GetFaceVertexCountsAttr()
        indices_attr = mesh.GetFaceVertexIndicesAttr()
        if not counts_attr or not indices_attr:
            print(f"  Warning: Face definition attributes missing on mesh {usd_mesh_prim.GetPath()}")
            return None
        counts = counts_attr.Get(time_code)
        indices = indices_attr.Get(time_code)
        if not counts or not indices:
            print(f"  Warning: Face data missing at time {time_code} for mesh {usd_mesh_prim.GetPath()}")
            return None

        # Convert to Blender's face format
        faces = []
        current_index = 0
        for count in counts:
            if count < 3:
                print(f"  Warning: Skipping face with less than 3 vertices on {usd_mesh_prim.GetPath()}")
                current_index += count
                continue
            face_indices = tuple(indices[current_index : current_index + count])
            # Triangulate if necessary
            if count == 3:
                faces.append(face_indices)
            elif count == 4:
                faces.append(face_indices)
            else: # Triangulate N-gons using a simple fan
                for i in range(1, count - 1):
                    faces.append((face_indices[0], face_indices[i], face_indices[i+1]))
            current_index += count

        # Get UVs and normals
        uvs_data = extract_uv_data(mesh, time_code, indices, verts, faces)
        normals_data = extract_normals_data(mesh, time_code, indices, verts, up_axis_is_y)

        return verts, faces, uvs_data, normals_data

    except Exception as e:
        print(f"ERROR extracting mesh data from {usd_mesh_prim.GetPath()}: {e}")
        traceback.print_exc()
        return None


def extract_uv_data(mesh, time_code, indices, verts, faces):
    """Extract UV data from USD mesh."""
    uvs_data = None
    primvars_api = UsdGeom.PrimvarsAPI(mesh.GetPrim())
    st_primvar = primvars_api.GetPrimvar("st")
    
    if st_primvar:
        uv_values = st_primvar.Get(time_code)
        uv_indices = st_primvar.GetIndices(time_code)
        uv_interpolation = st_primvar.GetInterpolation()
        
        if uv_values is not None:
            if uv_indices and len(uv_indices) == len(indices):
                uvs_data = (uv_values, uv_indices, uv_interpolation)
            elif len(uv_values) == len(verts) and uv_interpolation == UsdGeom.Tokens.vertex:
                uvs_data = (uv_values, indices, UsdGeom.Tokens.faceVarying)
            elif len(uv_values) == len(faces) and uv_interpolation == UsdGeom.Tokens.uniform:
                uvs_data = (uv_values, None, uv_interpolation)
            elif len(uv_values) == len(indices) and uv_interpolation == UsdGeom.Tokens.faceVarying:
                uvs_data = (uv_values, None, uv_interpolation)
            else:
                print(f"  Warning: UV primvar 'st' on {mesh.GetPrim().GetPath()} has unexpected size or interpolation '{uv_interpolation}'. Skipping UVs.")
        else:
            print(f"  Warning: UV primvar 'st' on {mesh.GetPrim().GetPath()} has no value data.")
    else:
        print(f"  Info: No 'st' UV primvar found on {mesh.GetPrim().GetPath()}")
    
    return uvs_data


def extract_normals_data(mesh, time_code, indices, verts, up_axis_is_y):
    """Extract normals data from USD mesh."""
    normals_data = None
    primvars_api = UsdGeom.PrimvarsAPI(mesh.GetPrim())
    normals_primvar = primvars_api.GetPrimvar("normals")
    
    if normals_primvar:
        norm_values = normals_primvar.Get(time_code)
        norm_indices = normals_primvar.GetIndices(time_code)
        norm_interpolation = normals_primvar.GetInterpolation()
        
        if norm_values is not None:
            # Correct Y-Up normals
            if up_axis_is_y:
                norm_values = [Gf.Vec3f(n[0], -n[2], n[1]) for n in norm_values]

            if norm_indices and len(norm_indices) == len(indices) and norm_interpolation == UsdGeom.Tokens.faceVarying:
                normals_data = (norm_values, norm_indices, norm_interpolation)
            elif len(norm_values) == len(verts) and norm_interpolation == UsdGeom.Tokens.vertex:
                normals_data = (norm_values, None, norm_interpolation)
            elif len(norm_values) == len(indices) and norm_interpolation == UsdGeom.Tokens.faceVarying:
                normals_data = (norm_values, None, norm_interpolation)
            else:
                print(f"  Warning: Normals primvar on {mesh.GetPrim().GetPath()} has unexpected size or interpolation '{norm_interpolation}'. Skipping normals.")
    else:
        print(f"  Info: No 'normals' primvar found on {mesh.GetPrim().GetPath()}. Blender will calculate them.")
    
    return normals_data


def get_transform_matrix(usd_prim, context):
    """Get the local-to-world transform matrix for a prim."""
    try:
        # Use the cached transform
        local_to_world_gf = context.xform_cache.GetLocalToWorldTransform(usd_prim)

        # Convert Gf.Matrix4d to Blender's mathutils.Matrix
        m = local_to_world_gf
        bl_matrix = mathutils.Matrix(((m[0][0], m[1][0], m[2][0], m[3][0]),
                                     (m[0][1], m[1][1], m[2][1], m[3][1]),
                                     (m[0][2], m[1][2], m[2][2], m[3][2]),
                                     (m[0][3], m[1][3], m[2][3], m[3][3])))

        # Apply Y-Up to Z-Up conversion if needed
        if context.up_axis_is_y:
            mat_yup_to_zup = mathutils.Matrix.Rotation(math.radians(-90.0), 4, 'X')
            bl_matrix = mat_yup_to_zup @ bl_matrix

        return bl_matrix

    except Exception as e:
        print(f"ERROR getting transform for {usd_prim.GetPath()}: {e}")
        traceback.print_exc()
        return mathutils.Matrix()


def get_remix_metadata(usd_instance_prim):
    """Extract the _remix_metadata dictionary from an instance prim."""
    metadata = {}
    over_mesh = usd_instance_prim.GetPrim().GetChild("mesh")
    if over_mesh:
        for prop in over_mesh.GetAuthoredPropertiesInNamespace("primvars:_remix_metadata"):
            key = prop.GetBaseName()
            value = prop.Get()
            if value is not None:
                metadata[key] = value
        if metadata:
            print(f"  Found _remix_metadata on {usd_instance_prim.GetPath()}: {list(metadata.keys())}")
    return metadata


def process_materials(context, import_materials):
    """Process and create materials from USD stage."""
    if not import_materials:
        return
        
    print("\n--- Processing Materials ---")
    
    # Find all material prims
    for prim in context.stage.TraverseAll():
        if prim.IsA(UsdShade.Material):
            material = UsdShade.Material(prim)
            material_path = str(prim.GetPath())
            context.material_map[material_path] = material

    print(f"Found {len(context.material_map)} material prims in the stage.")

    # Create Blender materials
    for material_path, usd_material in context.material_map.items():
        try:
            bl_mat = create_material(material_path, context.stage, context.usd_file_path)
            if bl_mat:
                context.blender_materials[material_path] = bl_mat
        except Exception as e:
            print(f"ERROR creating material from path '{material_path}': {e}")
            traceback.print_exc()
    
    print(f"Created {len(context.blender_materials)} Blender materials.")


def process_lights(context, collections, import_lights):
    """Process and import lights from USD stage."""
    if not import_lights:
        return []
        
    print("\n--- Processing Lights ---")
    try:
        imported_light_objects = import_lights_from_usd(
            context.stage, 
            collections['lights'], 
            context.scene_scale
        )
        context.created_lights_set.update(imported_light_objects)
        return imported_light_objects
    except Exception as e:
        print(f"Error during light import: {e}")
        traceback.print_exc()
        return []


def process_cameras(context, collections):
    """Process and import cameras from USD stage."""
    print("\n--- Processing Cameras ---")
    camera_count = 0
    
    for prim in context.stage.TraverseAll():
        if prim.IsA(UsdGeom.Camera):
            try:
                camera_obj = create_camera_from_prim(prim, context)
                if camera_obj:
                    collections['cameras'].objects.link(camera_obj)
                    context.created_cameras_set.add(camera_obj)
                    camera_count += 1
                    print(f"  Created Blender camera: {camera_obj.name}")
            except Exception as e:
                print(f"ERROR creating camera from prim {prim.GetPath()}: {e}")
                traceback.print_exc()

    print(f"Processed {camera_count} cameras.")


def create_camera_from_prim(cam_prim, context):
    """Create a Blender camera from a USD camera prim."""
    cam_path_str = str(cam_prim.GetPath())
    cam_name = bpy.path.clean_name(cam_prim.GetName())
    print(f"Processing camera: {cam_name} ({cam_path_str})")

    usd_camera = UsdGeom.Camera(cam_prim)
    bl_cam_data = bpy.data.cameras.new(name=cam_name)

    # Get camera properties
    focal_length = usd_camera.GetFocalLengthAttr().Get(context.time_code)
    h_aperture = usd_camera.GetHorizontalApertureAttr().Get(context.time_code)
    clipping_range = usd_camera.GetClippingRangeAttr().Get(context.time_code)

    if focal_length is not None:
        bl_cam_data.lens = focal_length
    if h_aperture is not None:
        bl_cam_data.sensor_width = h_aperture
        bl_cam_data.sensor_fit = 'HORIZONTAL'
    if clipping_range is not None:
        bl_cam_data.clip_start = clipping_range[0]
        bl_cam_data.clip_end = clipping_range[1]

    # Create camera object
    bl_cam_obj = bpy.data.objects.new(cam_name, bl_cam_data)

    # Set transform
    transform_matrix_bl = get_transform_matrix(cam_prim, context)
    loc, rot, scale = transform_matrix_bl.decompose()
    scaled_loc = loc * context.scene_scale

    bl_cam_obj.location = scaled_loc
    bl_cam_obj.rotation_mode = 'QUATERNION'
    bl_cam_obj.rotation_quaternion = rot
    bl_cam_obj.scale = scale

    return bl_cam_obj


def process_meshes_and_instances(context, collections, import_materials):
    """Process base meshes and instances."""
    # Process base meshes first
    process_base_meshes(context)
    
    # Then process instances that reference the base meshes
    process_instances(context, collections, import_materials)


def process_base_meshes(context):
    """Pre-process base meshes and create Blender mesh data."""
    print("\n--- Pre-processing Base Meshes ---")
    meshes_root = context.stage.GetPrimAtPath("/RootNode/meshes")
    if not meshes_root:
        print("No /RootNode/meshes found in stage")
        return
        
    for child_prim in meshes_root.GetChildren():
        mesh_prim_to_process = find_mesh_prim(child_prim)
        
        if mesh_prim_to_process:
            mesh_key_path_str = str(child_prim.GetPath())
            
            if mesh_key_path_str not in context.base_mesh_data:
                print(f"  Processing base mesh for key: {mesh_key_path_str} (using data from {mesh_prim_to_process.GetPath()})")
                mesh_geom = get_mesh_data(mesh_prim_to_process, context)
                
                if mesh_geom:
                    bl_mesh = create_blender_mesh_from_data(mesh_geom, child_prim, mesh_key_path_str)
                    if bl_mesh:
                        context.base_mesh_data[mesh_key_path_str] = bl_mesh
                        print(f"    Created Blender mesh data: {bl_mesh.name} for key {mesh_key_path_str}")
                else:
                    print(f"    Warning: Could not extract geometry from {mesh_prim_to_process.GetPath()}")
    
    print(f"Processed {len(context.base_mesh_data)} base mesh data blocks.")


def find_mesh_prim(child_prim):
    """Find the actual UsdGeom.Mesh prim within a container."""
    if child_prim.IsA(UsdGeom.Mesh):
        return child_prim
    elif child_prim.IsA(UsdGeom.Xformable):
        # Look for a Mesh child
        for grandchild in child_prim.GetChildren():
            if grandchild.IsA(UsdGeom.Mesh):
                return grandchild
        
        # Check if the Xform itself has mesh data due to composition
        if child_prim.GetAttribute('points').IsValid():
            if UsdGeom.Mesh(child_prim):
                return child_prim
            else:
                print(f"  Info: Prim {child_prim.GetPath()} has points but isn't a UsdGeom.Mesh?")
    
    return None


def create_blender_mesh_from_data(mesh_geom, child_prim, mesh_key_path_str):
    """Create a Blender mesh from extracted USD mesh data."""
    verts, faces, uvs_data, normals_data = mesh_geom
    bl_mesh_name = bpy.path.clean_name(child_prim.GetName()) + "_data"
    bl_mesh = bpy.data.meshes.new(name=bl_mesh_name)
    
    # Store original USD prim path
    bl_mesh["usd_prim_path"] = mesh_key_path_str
    print(f"    Stored 'usd_prim_path' = \"{mesh_key_path_str}\" on mesh data '{bl_mesh.name}'")

    # Create mesh from data
    bl_mesh.from_pydata(verts, [], faces)
    bl_mesh.update()

    # Set UVs
    apply_uv_data(bl_mesh, uvs_data, mesh_key_path_str)
    
    # Set normals
    apply_normals_data(bl_mesh, normals_data, mesh_key_path_str)

    # Validate and finalize mesh
    bl_mesh.validate(verbose=True)
    if bl_mesh.polygons:
        bl_mesh.polygons.foreach_set('use_smooth', [True] * len(bl_mesh.polygons))

    return bl_mesh


def apply_uv_data(bl_mesh, uvs_data, mesh_key_path_str):
    """Apply UV data to Blender mesh."""
    if not uvs_data:
        print(f"    No valid UV data found for {mesh_key_path_str}")
        return
        
    uv_values, uv_indices, uv_interpolation = uvs_data
    uv_layer = bl_mesh.uv_layers.new(name="st")
    
    if uv_interpolation == UsdGeom.Tokens.faceVarying:
        apply_face_varying_uvs(bl_mesh, uv_layer, uv_values, uv_indices, mesh_key_path_str)
    elif uv_interpolation == UsdGeom.Tokens.vertex:
        apply_vertex_uvs(bl_mesh, uv_layer, uv_values)
    elif uv_interpolation == UsdGeom.Tokens.uniform and uv_indices is None:
        apply_uniform_uvs(bl_mesh, uv_layer, uv_values, mesh_key_path_str)
    else:
        print(f"    Warning: Unhandled UV interpolation '{uv_interpolation}' for {mesh_key_path_str}. Skipping UVs.")


def apply_face_varying_uvs(bl_mesh, uv_layer, uv_values, uv_indices, mesh_key_path_str):
    """Apply face-varying UV data."""
    if uv_indices:
        # Indexed Face Varying
        loops_uv = [(0,0)] * len(bl_mesh.loops)
        for i, loop in enumerate(bl_mesh.loops):
            uv_idx = uv_indices[i]
            if 0 <= uv_idx < len(uv_values):
                u, v = uv_values[uv_idx][0], uv_values[uv_idx][1]
                loops_uv[loop.index] = (u, v)
    elif len(uv_values) == len(bl_mesh.loops):
        # Non-indexed Face Varying
        loops_uv = [(uv[0], uv[1]) for uv in uv_values]
    else:
        print(f"    Warning: UV faceVarying data size mismatch for {mesh_key_path_str}. Skipping UVs.")
        return
    
    if loops_uv:
        uv_layer.data.foreach_set("uv", [uv for pair in loops_uv for uv in pair])


def apply_vertex_uvs(bl_mesh, uv_layer, uv_values):
    """Apply vertex UV data."""
    loops_uv = [(0,0)] * len(bl_mesh.loops)
    for loop in bl_mesh.loops:
        vert_idx = loop.vertex_index
        if 0 <= vert_idx < len(uv_values):
            u, v = uv_values[vert_idx][0], uv_values[vert_idx][1]
            loops_uv[loop.index] = (u, v)
    uv_layer.data.foreach_set("uv", [uv for pair in loops_uv for uv in pair])


def apply_uniform_uvs(bl_mesh, uv_layer, uv_values, mesh_key_path_str):
    """Apply uniform (per-face) UV data."""
    if len(uv_values) == len(bl_mesh.polygons):
        loops_uv = [(0,0)] * len(bl_mesh.loops)
        for poly_idx, poly in enumerate(bl_mesh.polygons):
            u, v = uv_values[poly_idx][0], uv_values[poly_idx][1]
            for loop_idx in poly.loop_indices:
                loops_uv[loop_idx] = (u, v)
        uv_layer.data.foreach_set("uv", [uv for pair in loops_uv for uv in pair])
    else:
        print(f"    Warning: UV uniform data size mismatch for {mesh_key_path_str}. Skipping UVs.")


def apply_normals_data(bl_mesh, normals_data, mesh_key_path_str):
    """Apply normals data to Blender mesh."""
    if not normals_data:
        bl_mesh.calc_normals_split()
        return
        
    norm_values, norm_indices, norm_interpolation = normals_data
    bl_mesh.use_auto_smooth = True
    
    if norm_interpolation == UsdGeom.Tokens.vertex:
        apply_vertex_normals(bl_mesh, norm_values, mesh_key_path_str)
    elif norm_interpolation == UsdGeom.Tokens.faceVarying:
        apply_face_varying_normals(bl_mesh, norm_values, norm_indices, mesh_key_path_str)
    else:
        print(f"    Warning: Unhandled Normal interpolation '{norm_interpolation}' for {mesh_key_path_str}. Using auto-calculated normals.")
        bl_mesh.calc_normals_split()


def apply_vertex_normals(bl_mesh, norm_values, mesh_key_path_str):
    """Apply vertex normals."""
    if len(norm_values) == len(bl_mesh.vertices):
        bl_mesh.normals_split_custom_set_from_vertices([n for v in norm_values for n in v])
    else:
        print(f"    Warning: Normal vertex data size mismatch for {mesh_key_path_str}. Using auto-calculated normals.")
        bl_mesh.calc_normals_split()


def apply_face_varying_normals(bl_mesh, norm_values, norm_indices, mesh_key_path_str):
    """Apply face-varying normals."""
    if norm_indices:
        # Indexed Face Varying
        if len(norm_indices) == len(bl_mesh.loops):
            loop_normals = [(0,0,0)] * len(bl_mesh.loops)
            for i, loop in enumerate(bl_mesh.loops):
                norm_idx = norm_indices[i]
                if 0 <= norm_idx < len(norm_values):
                    loop_normals[loop.index] = tuple(norm_values[norm_idx])
            bl_mesh.normals_split_custom_set(loop_normals)
        else:
            print(f"    Warning: Normal faceVarying index size mismatch for {mesh_key_path_str}. Using auto-calculated normals.")
            bl_mesh.calc_normals_split()
    elif len(norm_values) == len(bl_mesh.loops):
        # Non-indexed Face Varying
        bl_mesh.normals_split_custom_set([tuple(n) for n in norm_values])
    else:
        print(f"    Warning: Normal faceVarying data size mismatch for {mesh_key_path_str}. Using auto-calculated normals.")
        bl_mesh.calc_normals_split()


def process_instances(context, collections, import_materials):
    """Process instances and create Blender objects."""
    print("\n--- Processing Instances ---")
    instances_root = context.stage.GetPrimAtPath("/RootNode/instances")
    if not instances_root:
        print("No /RootNode/instances found in stage")
        return
        
    instance_count = 0
    
    for instance_prim in instances_root.GetChildren():
        if not instance_prim.IsA(UsdGeom.Xformable):
            continue

        instance_path_str = str(instance_prim.GetPath())
        instance_name = bpy.path.clean_name(instance_prim.GetName())
        print(f"Processing instance: {instance_name} ({instance_path_str})")
        instance_count += 1

        try:
            bl_object = create_instance_object(instance_prim, context, collections, import_materials)
            if bl_object:
                context.created_objects.add(bl_object)
        except Exception as e:
            print(f"ERROR creating instance {instance_name}: {e}")
            traceback.print_exc()

    print(f"Processed {instance_count} instances.")


def create_instance_object(instance_prim, context, collections, import_materials):
    """Create a Blender object from a USD instance prim."""
    instance_path_str = str(instance_prim.GetPath())
    instance_name = bpy.path.clean_name(instance_prim.GetName())
    
    # Get base mesh reference
    base_mesh_ref = get_base_mesh_reference(instance_prim)
    if not base_mesh_ref:
        print(f"  Warning: Cannot find base mesh reference for instance {instance_name}")
        return None

    # Check if we have the Blender mesh data for this reference
    if base_mesh_ref not in context.base_mesh_data:
        print(f"  Warning: Base mesh data not found for reference '{base_mesh_ref}' used by instance {instance_name}")
        return None
    
    bl_mesh_data = context.base_mesh_data[base_mesh_ref]

    # Create Blender object
    bl_object = bpy.data.objects.new(instance_name, bl_mesh_data)

    # Store USD paths
    bl_object["usd_prim_path"] = base_mesh_ref
    bl_object["usd_instance_path"] = instance_path_str
    print(f"  Stored 'usd_prim_path' = \"{base_mesh_ref}\" on instance object '{bl_object.name}'")
    print(f"  Stored 'usd_instance_path' = \"{instance_path_str}\" on instance object '{bl_object.name}'")

    # Set transform
    apply_instance_transform(bl_object, instance_prim, context)

    # Link to collection
    collections['meshes'].objects.link(bl_object)

    # Assign material
    if import_materials:
        assign_instance_material(bl_object, instance_prim, context, instance_name)

    return bl_object


def get_base_mesh_reference(instance_prim):
    """Get the base mesh reference from an instance prim."""
    base_mesh_ref = None
    
    # Check payloads first
    if instance_prim.HasPayload():
        payloads = instance_prim.GetPayloads().GetPayloads()
        if payloads:
            base_mesh_ref = str(payloads[0].assetPath)
    
    # Check references if no payload
    if not base_mesh_ref:
        references_list_op = instance_prim.GetMetadata('references')
        if references_list_op:
            ref_items = references_list_op.GetAddedOrExplicitItems()
            if ref_items:
                base_mesh_ref = str(ref_items[0].primPath)
    
    return base_mesh_ref


def apply_instance_transform(bl_object, instance_prim, context):
    """Apply transform to instance object."""
    transform_matrix_bl = get_transform_matrix(instance_prim, context)
    loc, rot, original_scale = transform_matrix_bl.decompose()

    # Scale location and object scale
    scaled_loc = loc * context.scene_scale
    scaled_s = original_scale * context.scene_scale

    bl_object.location = scaled_loc
    bl_object.rotation_mode = 'QUATERNION'
    bl_object.rotation_quaternion = rot
    bl_object.scale = scaled_s


def assign_instance_material(bl_object, instance_prim, context, instance_name):
    """Assign material to instance object."""
    # Get material binding
    material_binding_api = UsdShade.MaterialBindingAPI(instance_prim)
    binding_rel = material_binding_api.GetDirectBindingRel()
    bound_material_path = None
    
    if binding_rel:
        targets = binding_rel.GetTargets()
        if targets:
            bound_material_path = str(targets[0])
            print(f"  Found material binding: {bound_material_path}")
        else:
            print(f"  Warning: Instance {instance_name} has binding relationship with no target.")
    else:
        print(f"  Warning: Instance {instance_name} has no direct material binding relationship.")

    if bound_material_path:
        try:
            # Get metadata for material overrides
            metadata = get_remix_metadata(instance_prim)
            
            # Create or get material with overrides
            bl_material = get_or_create_instance_material(
                base_material_path=bound_material_path,
                instance_metadata=metadata,
                usd_stage=context.stage,
                usd_file_path_context=context.usd_file_path,
                material_cache=context.material_cache
            )

            if bl_material:
                if bl_object.data.materials:
                    bl_object.data.materials[0] = bl_material
                else:
                    bl_object.data.materials.append(bl_material)
                print(f"  Assigned material '{bl_material.name}' to instance {instance_name}")
            else:
                print(f"  Warning: Failed to get or create material for instance {instance_name}")
        except Exception as e:
            print(f"ERROR assigning material to instance '{instance_name}': {e}")
            traceback.print_exc()
    else:
        print(f"  Warning: No material binding found for instance {instance_name}. Assigning default or none.")


def finalize_import(blender_context, usd_context):
    """Finalize the import by selecting objects and setting active object."""
    # Deselect all and select imported objects
    if bpy.ops.object.select_all.poll():
        bpy.ops.object.select_all(action='DESELECT')

    all_imported = usd_context.created_objects.union(usd_context.created_lights_set).union(usd_context.created_cameras_set)
    active_object_set = False
    
    for obj in all_imported:
        if obj and obj.name in bpy.data.objects:
            try:
                obj.select_set(True)
                if not active_object_set and obj.type == 'MESH':
                    blender_context.view_layer.objects.active = obj
                    active_object_set = True
            except (ReferenceError, Exception) as e:
                print(f"Warning: Could not select object '{obj.name}': {e}")

    # Set first valid object as active if no mesh was set
    if not active_object_set and all_imported:
        first_valid_obj = next((obj for obj in all_imported if obj and obj.name in bpy.data.objects), None)
        if first_valid_obj:
            blender_context.view_layer.objects.active = first_valid_obj
            print(f"Set '{first_valid_obj.name}' as active object.")


def create_success_message(usd_file_path, context):
    """Create success message with import statistics."""
    return (f"Successfully imported '{os.path.basename(usd_file_path)}'. "
            f"Created: {len(context.created_objects)} objects, "
            f"{len(context.created_lights_set)} lights, "
            f"{len(context.created_cameras_set)} cameras. "
            f"Processed: {len(context.material_cache)} materials. "
            f"Applied Scale: {context.scene_scale:.3f}")


def import_rtx_remix_usd_with_materials(context, usd_file_path, import_materials, import_lights, scene_scale):
    """
    Core import logic for RTX Remix USD files.
    
    This is the main entry point that orchestrates the import process.
    """
    if not USD_AVAILABLE:
        return None, None, "USD Python libraries (pxr) not found. Please install them in Blender's Python environment."

    print(f"Starting RTX Remix USD Import: {usd_file_path}")
    print(f" Options: Import Materials={import_materials}, Import Lights={import_lights}")

    try:
        # Setup texture directory
        texture_dir = setup_texture_directory(usd_file_path)
        
        # Open USD stage
        stage = open_usd_stage(usd_file_path)
        
        # Create context object
        usd_context = USDStageContext(stage, usd_file_path, scene_scale)
        
        # Setup Blender collections
        import_collection, collections = setup_blender_collections(context, usd_file_path)
        
        # Process different types of content
        process_materials(usd_context, import_materials)
        process_lights(usd_context, collections, import_lights)
        process_cameras(usd_context, collections)
        process_meshes_and_instances(usd_context, collections, import_materials)
        
        # Finalize import
        finalize_import(context, usd_context)
        
        success_message = create_success_message(usd_file_path, usd_context)
        print(success_message)
        
        return usd_context.created_objects, usd_context.created_lights_set, success_message
        
    except USDImportError as e:
        return None, None, str(e)
    except Exception as e:
        error_msg = f"Unexpected error during import: {e}"
        print(error_msg)
        traceback.print_exc()
        return None, None, error_msg 