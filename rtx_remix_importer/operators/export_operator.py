import bpy
import os
import shutil
import tempfile
import subprocess
import bmesh
import mathutils
import math
import traceback
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy.types import Operator

try:
    from pxr import Usd, UsdGeom, UsdShade, UsdLux, Sdf, Vt, Gf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

# Import constants for addon directory access
from .. import constants

# --- Helper Functions ---

def get_relative_path(from_path, to_path):
    """Calculates the relative path from one file to another."""
    # Use the unified implementation from core_utils
    from .. import core_utils
    return core_utils.get_relative_path(from_path, to_path)

def generate_uuid_name(name, prefix="ref_"):
    """Generates a UUID-style name based on the input name for RTX Remix compatibility."""
    # Use the unified implementation from core_utils
    from .. import core_utils
    return core_utils.generate_uuid_name(name, prefix)

def sanitize_prim_name(name):
    """Replaces invalid characters for USD prim names."""
    # Use the unified implementation from core_utils
    from .. import core_utils
    return core_utils.sanitize_prim_name(name)

def extract_base_material_name(material_name):
    """Extract base material name by removing hash suffixes.
    
    Example: 'mat_90ABF9B7573AA175_ed309fea_c0e78a85' -> 'mat_90ABF9B7573AA175'
    """
    # Split by underscore and look for hash patterns
    parts = material_name.split('_')
    
    # Hash suffixes are typically 8 characters of hex
    # Keep parts until we find what looks like a hash suffix
    base_parts = []
    for part in parts:
        # If this part looks like a hash (8 hex chars), stop here
        if len(part) == 8 and all(c in '0123456789abcdefABCDEF' for c in part):
            break
        base_parts.append(part)
    
    # If we didn't find any hash patterns, return the original name
    if len(base_parts) == len(parts):
        return material_name
    
    return '_'.join(base_parts)

def find_existing_texture_for_base_material(base_material_name, texture_type, textures_dir, texture_processor):
    """Find existing texture for base material name, ignoring hash suffixes.
    
    Args:
        base_material_name: Base material name without hash suffixes
        texture_type: Type of texture (e.g., 'base color', 'normal')
        textures_dir: Directory to search for textures
        texture_processor: TextureProcessor instance for suffix lookup
        
    Returns:
        Path to existing texture file if found, None otherwise
    """
    if not os.path.exists(textures_dir):
        return None
    
    # Get the expected suffix for this texture type
    type_suffix = texture_processor.get_texture_suffix(texture_type)
    
    # Look for files that match the base material pattern
    # The texture name is typically the material name without the 'mat_' prefix
    if base_material_name.startswith('mat_'):
        texture_base = base_material_name[4:]  # Remove 'mat_' prefix
    else:
        texture_base = base_material_name
    
    # Look for existing texture files with this base name
    expected_filename = f"{texture_base}{type_suffix}.dds"
    expected_path = os.path.join(textures_dir, expected_filename)
    
    if os.path.exists(expected_path):
        return expected_path
    
    # Also check for files that start with the base name (in case of variations)
    try:
        for filename in os.listdir(textures_dir):
            if filename.startswith(texture_base) and filename.endswith(f"{type_suffix}.dds"):
                return os.path.join(textures_dir, filename)
    except OSError:
        pass
    
    return None

def ensure_mdl_files(project_root):
    """Copies MDL shader files from the addon to the project directory."""
    # Source MDL directory in the addon
    addon_dir = os.path.dirname(os.path.dirname(__file__))
    mdl_source_dir = os.path.join(addon_dir, "materials")
    
    # Target directory in the project
    mdl_target_dir = os.path.join(project_root, "assets", "remix_materials")
    
    # Create target directory if it doesn't exist
    os.makedirs(mdl_target_dir, exist_ok=True)
    
    # Copy all MDL files
    copied_files = []
    for mdl_file in os.listdir(mdl_source_dir):
        if mdl_file.endswith(".mdl"):
            source_file = os.path.join(mdl_source_dir, mdl_file)
            target_file = os.path.join(mdl_target_dir, mdl_file)
            
            # Only copy if the file doesn't exist or has been modified
            if not os.path.exists(target_file) or os.path.getmtime(source_file) > os.path.getmtime(target_file):
                shutil.copy2(source_file, target_file)
                copied_files.append(mdl_file)
    
    if copied_files:
        print(f"Copied MDL files to {mdl_target_dir}: {', '.join(copied_files)}")
    else:
        print(f"MDL files already up to date in {mdl_target_dir}")
    
    return mdl_target_dir

# --- Material Export Helper ---

def export_material(blender_material, sublayer_stage, project_root, sublayer_path, parent_mesh_path=None, obj=None):
    """Exports a Blender material to the sublayer stage."""
    if not blender_material:
        return None

    print(f"Exporting Material: {blender_material.name}")

    # Ensure MDL files are copied to the project
    mdl_source_dir = os.path.join(constants.ADDON_DIR, "materials")
    mdl_target_dir = os.path.join(project_root, "assets", "remix_materials")
    
    os.makedirs(mdl_target_dir, exist_ok=True)
    
    copied_files = []
    if os.path.exists(mdl_source_dir):
        import shutil
        for mdl_file in os.listdir(mdl_source_dir):
            if mdl_file.endswith(".mdl"):
                source_file = os.path.join(mdl_source_dir, mdl_file)
                target_file = os.path.join(mdl_target_dir, mdl_file)
                
                if not os.path.exists(target_file) or os.path.getmtime(source_file) > os.path.getmtime(target_file):
                    shutil.copy2(source_file, target_file)
                    copied_files.append(mdl_file)
    
    if copied_files:
        print(f"Copied MDL files to {mdl_target_dir}: {', '.join(copied_files)}")

    # Create material path
    if parent_mesh_path and sublayer_stage.GetPrimAtPath(parent_mesh_path):
        # NVIDIA Remix style - material under mesh
        xforms_path = parent_mesh_path.AppendPath("XForms")
        mesh_name = sanitize_prim_name(obj.name) if obj else "Suzanne"
        mesh_path = xforms_path.AppendPath(mesh_name)
        
        # Apply MaterialBindingAPI to mesh
        mesh_prim = sublayer_stage.OverridePrim(mesh_path)
        schemas = mesh_prim.GetAppliedSchemas()
        if "MaterialBindingAPI" not in schemas:
            sublayer_stage.OverridePrim(mesh_path, {"apiSchemas": ["MaterialBindingAPI"]})
        
        # Create material and shader paths
        looks_path = mesh_path.AppendPath("Looks")
        material_prim = sublayer_stage.DefinePrim(looks_path, "Material")
        
        shader_path = looks_path.AppendPath("Shader")
        shader_prim = sublayer_stage.DefinePrim(shader_path, "Shader")
        
        # Add material binding
        binding_rel = mesh_prim.CreateRelationship("material:binding")
        binding_rel.SetTargets([looks_path])
        mesh_prim.CreateAttribute("material:binding:bindMaterialAs", Sdf.ValueTypeNames.Token).Set("strongerThanDescendants")
        
        mat_path = looks_path
    else:
        # Standard material path
        looks_path = Sdf.Path("/RootNode/Looks")
        sublayer_stage.OverridePrim(looks_path)
        
        # For material replacement, use base material name without hash suffixes
        # This ensures we replace the correct original material
        if parent_mesh_path:  # Material replacement mode
            base_material_name = extract_base_material_name(blender_material.name)
            print(f"  Using base material name for replacement: {base_material_name}")
            mat_base_name = sanitize_prim_name(base_material_name)
            # For material replacement, use the exact base name without UUID generation
            mat_name_sanitized = mat_base_name
        else:  # New material export
            mat_base_name = sanitize_prim_name(blender_material.name)
            # For new materials, generate UUID to avoid conflicts
            mat_name_sanitized = generate_uuid_name(mat_base_name, prefix="mat_")
            
        mat_path = looks_path.AppendPath(mat_name_sanitized)
        
        material_prim = sublayer_stage.OverridePrim(mat_path)
        material_prim.SetTypeName("Material")
        
        shader_name_sanitized = generate_uuid_name(f"shader_{mat_base_name}", prefix="shader_")
        shader_path = mat_path.AppendPath(shader_name_sanitized)
        shader_prim = sublayer_stage.OverridePrim(shader_path)
        shader_prim.SetTypeName("Shader")

    # Set up material connections
    material_api = UsdShade.Material(material_prim)
    shader_output = UsdShade.Shader(shader_prim).CreateOutput("out", Sdf.ValueTypeNames.Token)
    shader_output.SetRenderType("material")
    
    material_api.CreateSurfaceOutput("mdl:surface").ConnectToSource(shader_output)
    material_api.CreateDisplacementOutput("mdl:displacement").ConnectToSource(shader_output)
    material_api.CreateVolumeOutput("mdl:volume").ConnectToSource(shader_output)

    # Set shader attributes
    shader_prim.CreateAttribute("info:implementationSource", Sdf.ValueTypeNames.Token).Set("sourceAsset")
    shader_prim.CreateAttribute("info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("AperturePBR_Opacity.mdl"))
    shader_prim.CreateAttribute("info:mdl:sourceAsset:subIdentifier", Sdf.ValueTypeNames.Token).Set("AperturePBR_Opacity")

    # Find Principled BSDF or group node
    principled_node = None
    if blender_material.use_nodes and blender_material.node_tree:
        for node in blender_material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                principled_node = node
                break
            elif node.type == 'GROUP' and node.node_tree:
                # Check if this is an Aperture group or similar
                group_name = node.node_tree.name.lower()
                if any(keyword in group_name for keyword in ['aperture', 'pbr', 'remix']):
                    principled_node = node
                    break
                    
                # Also check for common PBR input sockets
                expected_inputs = ['Base Color', 'Albedo Color', 'Metallic', 'Roughness']
                if any(input_name in [inp.name for inp in node.inputs] for input_name in expected_inputs):
                    principled_node = node
                    break
                    
                # Check for common output sockets
                expected_outputs = ['BSDF', 'Shader', 'Surface']
                for output in node.outputs:
                    if output.name in expected_outputs and output.is_linked:
                        # Check if connected to material output
                        for link in output.links:
                            if link.to_node.type == 'OUTPUT_MATERIAL':
                                principled_node = node
                                break
                    
                    if principled_node:
                        break

    if not principled_node:
        print(f"  WARNING: Material '{blender_material.name}' has no Principled BSDF node. Exporting default shader.")
        return mat_path # Return material path even if parameters aren't set

    # Collect all textures for parallel processing
    texture_tasks = []
    texture_mappings = []
    
    # Use unified TextureProcessor
    from .. import core_utils
    texture_processor = core_utils.get_texture_processor()
    
    if not texture_processor.is_available():
        print(f"  Skipping texture export: texconv.exe not found.")
        # Continue with constant values only
    else:
        # Ensure textures directory exists
        textures_dir = os.path.join(project_root, "rtx-remix", "textures")
        try:
            os.makedirs(textures_dir, exist_ok=True)
        except OSError as e:
            print(f"  ERROR: Could not create textures directory: {textures_dir} - {e}")
            texture_processor = None  # Disable texture processing

    def find_texture_for_socket(socket_name, dds_format='BC7_UNORM_SRGB'):
        """Find texture connected to a socket and add to processing queue."""
        # Handle different socket names based on node type
        is_group = principled_node.type == 'GROUP'
        socket = None
        
        if is_group:
            # For Aperture Opaque and similar groups, map standard names to group input names
            socket_name_mapping = {
                'Base Color': 'Albedo Color',      # Aperture Opaque uses "Albedo Color"
                'Metallic': 'Metallic',            # Same name
                'Roughness': 'Roughness',          # Same name  
                'Normal': 'Normal Map',            # Aperture Opaque uses "Normal Map"
                'Emission': 'Emissive Color',      # Aperture Opaque uses "Emissive Color"
                'Emissive Color': 'Emissive Color' # Direct mapping
            }
            
            # Get the mapped socket name
            mapped_name = socket_name_mapping.get(socket_name, socket_name)
            socket = principled_node.inputs.get(mapped_name)
            
            if not socket:
                # Try finding by partial match if exact match fails
                for input_socket in principled_node.inputs:
                    if socket_name.lower() in input_socket.name.lower():
                        socket = input_socket
                        print(f"    Found socket by partial match: '{input_socket.name}' for '{socket_name}'")
                        break
        else:
            # Standard Principled BSDF
            socket = principled_node.inputs.get(socket_name)
            
        if not socket:
            print(f"    Socket '{socket_name}' not found on node")
            return None, None
            
        image_node = None
        if socket and socket.is_linked:
            link = socket.links[0]
            # Follow links upstream to find the TEX_IMAGE node
            node = link.from_node
            while node and node.type != 'TEX_IMAGE':
                # Handle common intermediate nodes like Normal Map, Bump, etc.
                input_found = False
                for input_socket in node.inputs:
                    if input_socket.is_linked:
                        node = input_socket.links[0].from_node
                        input_found = True
                        break
                if not input_found:
                    node = None # Stop if no upstream link found
                    
            if node and node.type == 'TEX_IMAGE':
                image_node = node

        if image_node and image_node.image and texture_processor:
            bl_image = image_node.image
            print(f"  Found texture '{bl_image.name}' for {socket_name}. Preparing for background processing...")
            
            # Get texture type for suffix and format
            texture_type = socket_name.lower()
            if texture_type in ['emissive color', 'emission']:
                texture_type = 'emission'
            elif texture_type == 'base color':
                texture_type = 'base color'
            
            # Get appropriate suffix and use recommended format if not specified
            type_suffix = texture_processor.get_texture_suffix(texture_type)
            if dds_format == 'BC7_UNORM_SRGB':  # Default format, use recommendation
                dds_format = texture_processor.get_recommended_format(texture_type)
            
            base_name, _ = os.path.splitext(bl_image.name)
            dds_file_name = f"{base_name}{type_suffix}.dds"
            absolute_dds_path = os.path.normpath(os.path.join(textures_dir, dds_file_name))
            
            # Check if this exact texture already exists
            if os.path.exists(absolute_dds_path):
                relative_texture_path = get_relative_path(sublayer_path, absolute_dds_path)
                print(f"    Using existing texture: {relative_texture_path}")
                return bl_image, relative_texture_path
            
            # Check for existing texture based on base material name (only if reuse is enabled)
            from bpy import context as bpy_context
            if bpy_context.scene.remix_reuse_existing_textures:
                base_material_name = extract_base_material_name(blender_material.name)
                existing_texture_path = find_existing_texture_for_base_material(
                    base_material_name, texture_type, textures_dir, texture_processor
                )
                
                if existing_texture_path:
                    # Found existing texture for base material - reuse it
                    relative_texture_path = get_relative_path(sublayer_path, existing_texture_path)
                    print(f"    Reusing existing texture from base material '{base_material_name}': {relative_texture_path}")
                    print(f"    Skipping processing for '{bl_image.name}' (already processed for base material)")
                    return bl_image, relative_texture_path
            else:
                print(f"    Texture reuse disabled - will process '{bl_image.name}' even if similar textures exist")
            
            # No existing texture found - add to processing queue
            texture_tasks.append((bl_image, absolute_dds_path, texture_type, dds_format))
            
            # Calculate relative path for later use
            relative_texture_path = get_relative_path(sublayer_path, absolute_dds_path)
            print(f"    Will process texture: {relative_texture_path}")
            
            return bl_image, relative_texture_path

        return None, None

    # Collect all textures for parallel processing
    diffuse_image, diffuse_rel_path = find_texture_for_socket('Base Color', 'BC7_UNORM_SRGB')
    metallic_image, metallic_rel_path = find_texture_for_socket('Metallic', 'BC4_UNORM')
    roughness_image, roughness_rel_path = find_texture_for_socket('Roughness', 'BC4_UNORM')
    normal_image, normal_rel_path = find_texture_for_socket('Normal', 'BC5_UNORM')
    
    # Check for emission and collect emissive textures
    enable_emission = False
    emissive_image, emissive_rel_path = None, None
    
    # Check emission settings - handle both standard Principled BSDF and Aperture Opaque node groups
    emissive_socket_name = 'Emissive Color' if 'Emissive Color' in principled_node.inputs else 'Emission'
    emissive_socket = principled_node.inputs.get(emissive_socket_name)
    
    # For Aperture Opaque node groups, check the "Enable Emission" boolean input first
    if principled_node.type == 'GROUP':
        enable_emission_socket = principled_node.inputs.get('Enable Emission')
        if enable_emission_socket and enable_emission_socket.default_value:
            enable_emission = True
            print(f"    Found Aperture Opaque 'Enable Emission' = {enable_emission_socket.default_value}")
    
    # If emission is enabled (either by Enable Emission checkbox or other means), check for textures/colors
    if enable_emission or (emissive_socket and (emissive_socket.is_linked or 
                          (len(emissive_socket.default_value) >= 3 and any(c > 0.001 for c in emissive_socket.default_value[:3])))):
        if not enable_emission:  # Set to True if not already set by Enable Emission checkbox
            enable_emission = True
            
        if emissive_socket and emissive_socket.is_linked:
            emissive_image, emissive_rel_path = find_texture_for_socket(emissive_socket_name, 'BC7_UNORM_SRGB')

    # Check for opacity/alpha
    opacity_image, opacity_rel_path = None, None
    opacity_socket = principled_node.inputs.get('Alpha')
    if opacity_socket and opacity_socket.is_linked:
        opacity_image, opacity_rel_path = find_texture_for_socket('Alpha', 'BC4_UNORM')

    # Check for specular
    specular_image, specular_rel_path = None, None
    specular_socket = principled_node.inputs.get('Specular')
    if specular_socket and specular_socket.is_linked:
        specular_image, specular_rel_path = find_texture_for_socket('Specular', 'BC4_UNORM')

    # Process all textures in parallel if any were found
    if texture_tasks and texture_processor:
        print(f"  Found {len(texture_tasks)} textures for background processing...")
        
        # Check how many already exist
        existing_count = 0
        for bl_image, output_path, texture_type, dds_format in texture_tasks:
            if os.path.exists(output_path):
                existing_count += 1
        
        new_textures_count = len(texture_tasks) - existing_count
        
        if existing_count > 0:
            print(f"    {existing_count} textures already exist and will be reused")
        if new_textures_count > 0:
            print(f"    {new_textures_count} textures will be processed in background")
        
        # Use background processing to avoid blocking Blender's main thread
        from .. import core_utils
        background_processor = core_utils.get_background_processor()
        
        # Create progress callback
        def progress_callback(msg):
            print(f"    {msg}")
        
        # Create completion callback that will be called when processing is done
        def completion_callback(job_id, job_info):
            if job_info['status'] == 'completed':
                successful_count = sum(1 for success in job_info['results'] if success)
                print(f"    Background texture processing completed: {successful_count}/{len(texture_tasks)} successful")
            elif job_info['status'] == 'failed':
                print(f"    Background texture processing failed: {job_info.get('error', 'Unknown error')}")
            else:
                print(f"    Background texture processing {job_info['status']}")
        
        # Start background job only if there are new textures to process
        if new_textures_count > 0:
            job_id = background_processor.start_background_job(
                texture_tasks,
                progress_callback=progress_callback,
                completion_callback=completion_callback
            )
            
            print(f"    Started background job: {job_id}")
        else:
            print(f"    All textures already exist - no background processing needed")
        
        print(f"    Material export continuing with existing/queued texture paths...")
        
        # The export continues immediately using the texture paths (existing or future)

    # Set material attributes based on processing results
    # Diffuse / Albedo (BC7 SRGB)
    if diffuse_rel_path:
        shader_prim.CreateAttribute("inputs:diffuse_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(diffuse_rel_path))
        print(f"    Set diffuse_texture: {diffuse_rel_path}")
    else:
        # Get base color value based on node type
        if principled_node.type == 'GROUP':
            # For Aperture Opaque, the input is named "Albedo Color"
            base_color_socket = principled_node.inputs.get('Albedo Color') or principled_node.inputs.get('Base Color')
            base_color = base_color_socket.default_value if base_color_socket else (0.8, 0.8, 0.8, 1.0)
        else:
            base_color = principled_node.inputs['Base Color'].default_value
        # Create a constant color attribute
        shader_prim.CreateAttribute("inputs:diffuse_color_constant", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(base_color[0], base_color[1], base_color[2]))
        print(f"    Set diffuse_color_constant: {base_color[:3]}")

    # Metallic (BC4 Unorm - single channel)
    if metallic_rel_path:
        shader_prim.CreateAttribute("inputs:metallic_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(metallic_rel_path))
        print(f"    Set metallic_texture: {metallic_rel_path}")
    else:
        # Get metallic value - same socket name for both node types
        metallic_socket = principled_node.inputs.get('Metallic')
        metallic_val = metallic_socket.default_value if metallic_socket else 0.0
        # Create a constant value attribute
        shader_prim.CreateAttribute("inputs:metallic_constant", Sdf.ValueTypeNames.Float).Set(metallic_val)
        print(f"    Set metallic_constant: {metallic_val}")

    # Roughness (BC4 Unorm - single channel)
    if roughness_rel_path:
        shader_prim.CreateAttribute("inputs:reflectionroughness_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(roughness_rel_path))
        print(f"    Set reflectionroughness_texture: {roughness_rel_path}")
    else:
        # Get roughness value - same socket name for both node types
        roughness_socket = principled_node.inputs.get('Roughness')
        roughness_val = roughness_socket.default_value if roughness_socket else 0.5
        # Create a constant value attribute
        shader_prim.CreateAttribute("inputs:reflection_roughness_constant", Sdf.ValueTypeNames.Float).Set(roughness_val)
        print(f"    Set reflection_roughness_constant: {roughness_val}")

    # Normal Map (BC5 Unorm - two channel)
    if normal_rel_path:
        shader_prim.CreateAttribute("inputs:normalmap_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(normal_rel_path))
        shader_prim.CreateAttribute("inputs:encoding", Sdf.ValueTypeNames.Int).Set(2)  # Tangent space normal
        print(f"    Set normalmap_texture: {normal_rel_path}")

    # Emission handling
    if enable_emission:
        shader_prim.CreateAttribute("inputs:enable_emission", Sdf.ValueTypeNames.Bool).Set(True)
        print(f"    Set enable_emission: True")
        
        # Set emissive intensity
        emissive_intensity = 1.0
        
        # Try to get emission strength/intensity from the material
        if principled_node.type == 'GROUP':
            # Aperture Opaque node groups have "Emissive Intensity" input
            emissive_intensity_socket = principled_node.inputs.get('Emissive Intensity')
            if emissive_intensity_socket:
                emissive_intensity = emissive_intensity_socket.default_value
                print(f"    Found Aperture Opaque 'Emissive Intensity' = {emissive_intensity}")
        else:
            # Standard Principled BSDF has Emission Strength
            emission_strength_socket = principled_node.inputs.get('Emission Strength')
            if emission_strength_socket:
                emissive_intensity = emission_strength_socket.default_value
                print(f"    Found Principled BSDF 'Emission Strength' = {emissive_intensity}")
        
        # Export emissive mask texture or color
        if emissive_rel_path:
            shader_prim.CreateAttribute("inputs:emissive_mask_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(emissive_rel_path))
            print(f"    Set emissive_mask_texture: {emissive_rel_path}")
        else:
            # Set emissive color constant if no texture
            if emissive_socket:
                emissive_color = emissive_socket.default_value
                if len(emissive_color) >= 3:
                    shader_prim.CreateAttribute("inputs:emissive_color_constant", Sdf.ValueTypeNames.Color3f).Set(
                        Gf.Vec3f(emissive_color[0], emissive_color[1], emissive_color[2])
                    )
                    print(f"    Set emissive_color_constant: {emissive_color[:3]}")
        
        # Set emissive intensity
        shader_prim.CreateAttribute("inputs:emissive_intensity", Sdf.ValueTypeNames.Float).Set(emissive_intensity)
        print(f"    Set emissive_intensity: {emissive_intensity}")

    # Opacity/Alpha handling
    if opacity_rel_path:
        shader_prim.CreateAttribute("inputs:opacity_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(opacity_rel_path))
        print(f"    Set opacity_texture: {opacity_rel_path}")
    else:
        # Check for alpha value
        alpha_socket = principled_node.inputs.get('Alpha')
        if alpha_socket:
            alpha_val = alpha_socket.default_value
            if alpha_val < 1.0:  # Only set if not fully opaque
                shader_prim.CreateAttribute("inputs:opacity_constant", Sdf.ValueTypeNames.Float).Set(alpha_val)
                print(f"    Set opacity_constant: {alpha_val}")

    # Specular handling
    if specular_rel_path:
        shader_prim.CreateAttribute("inputs:specular_texture", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(specular_rel_path))
        print(f"    Set specular_texture: {specular_rel_path}")
    else:
        # Check for specular value
        specular_socket = principled_node.inputs.get('Specular')
        if specular_socket:
            specular_val = specular_socket.default_value
            if specular_val != 0.5:  # Only set if not default value
                shader_prim.CreateAttribute("inputs:specular_constant", Sdf.ValueTypeNames.Float).Set(specular_val)
                print(f"    Set specular_constant: {specular_val}")

    print(f"  Finished exporting Material: {blender_material.name} to {mat_path}")
    return mat_path

# --- Light Export Helper ---

def export_light(operator, context, obj, sublayer_stage, project_root, target_sublayer_path):
    """Exports a Blender light object to the sublayer stage."""
    if not obj or obj.type != 'LIGHT' or not USD_AVAILABLE:
        return False

    print(f"\n--- Exporting Light: {obj.name} ---")
    bl_light = obj.data

    # --- Get Global Export Scale --- 
    remix_export_scale = context.scene.remix_export_scale
    print(f"  Using global export scale for light transform: {remix_export_scale}")

    # --- Determine USD Light Type and Common Attributes --- 
    usd_light_type = None
    light_attrs = {}
    shaping_attrs = {}
    extent = None # Optional extent for area lights

    intensity_scale = 1.0 # This may need to be adjusted to match Blender energy to USD intensity.

    # Use strings for standard attribute names, often with "inputs:" prefix
    light_attrs["inputs:color"] = Gf.Vec3f(bl_light.color[:])
    light_attrs["inputs:intensity"] = float(bl_light.energy * intensity_scale)

    if bl_light.type == 'POINT':
        usd_light_type = "SphereLight"
        radius = bl_light.shadow_soft_size if bl_light.shadow_soft_size > 0 else 1.0
        light_attrs["inputs:radius"] = float(radius)
        # Add default shaping for sphere lights
        shaping_attrs["shaping:cone:angle"] = 180.0
        shaping_attrs["shaping:cone:softness"] = 0.0
        shaping_attrs["shaping:focus"] = 0.0
        # Add default extent for sphere lights
        default_extent = 5.0 # Example size
        extent = Vt.Vec3fArray([Gf.Vec3f(-default_extent), Gf.Vec3f(default_extent)])

    elif bl_light.type == 'SUN':
        usd_light_type = "DistantLight"
        light_attrs["inputs:angle"] = float(bl_light.angle * 0.5 * 180.0 / 3.14159265)
    elif bl_light.type == 'SPOT':
        usd_light_type = "SphereLight"
        radius = bl_light.shadow_soft_size if bl_light.shadow_soft_size > 0 else 1.0
        light_attrs["inputs:radius"] = float(radius)
        # Use strings for shaping attributes (namespace already included)
        shaping_attrs["shaping:cone:angle"] = float(bl_light.spot_size * 0.5 * 180.0 / 3.14159265)
        shaping_attrs["shaping:cone:softness"] = float(bl_light.spot_blend)
        # Add default extent for sphere lights (used for spot export)
        default_extent = 5.0 # Example size
        extent = Vt.Vec3fArray([Gf.Vec3f(-default_extent), Gf.Vec3f(default_extent)])
    elif bl_light.type == 'AREA':
        if bl_light.shape == 'SQUARE' or bl_light.shape == 'RECTANGLE':
            usd_light_type = "RectLight"
            light_attrs["inputs:width"] = float(bl_light.size)
            light_attrs["inputs:height"] = float(bl_light.size_y if bl_light.shape == 'RECTANGLE' else bl_light.size)
            half_w, half_h = light_attrs["inputs:width"]/2.0, light_attrs["inputs:height"]/2.0
            extent = Vt.Vec3fArray([Gf.Vec3f(-half_w, -half_h, 0), Gf.Vec3f(half_w, half_h, 0)])
        elif bl_light.shape == 'DISK' or bl_light.shape == 'ELLIPSE':
            usd_light_type = "DiskLight"
            light_attrs["inputs:radius"] = float(bl_light.size * 0.5)
            r = light_attrs["inputs:radius"]
            extent = Vt.Vec3fArray([Gf.Vec3f(-r, -r, 0), Gf.Vec3f(r, r, 0)])
        else:
            operator.report({'WARNING'}, f"Unsupported AREA light shape '{bl_light.shape}' for {obj.name}. Skipping.")
            return False
    else:
        operator.report({'WARNING'}, f"Unsupported light type '{bl_light.type}' for {obj.name}. Skipping.")
        return False

    # Always export color and disable color temperature for now
    light_attrs["inputs:enableColorTemperature"] = False
    if "inputs:colorTemperature" in light_attrs: # Remove if somehow added previously
        del light_attrs["inputs:colorTemperature"]
    if "inputs:color" not in light_attrs: # Ensure color is present
        light_attrs["inputs:color"] = Gf.Vec3f(bl_light.color[:])
        print("  Exporting RGB color, color temperature disabled.")

    # --- Determine Parent Prim Path (Anchoring) --- 
    light_base_name = sanitize_prim_name(obj.name)
    light_name_sanitized = generate_uuid_name(light_base_name, prefix="light_")
    anchor_obj = context.scene.remix_anchor_object_target # Read anchor from Scene property
    parent_prim_path = None
    if anchor_obj:
        # Get the stored original USD prim path from the anchor object
        anchor_path_str = anchor_obj.get("usd_prim_path", None) 
        if anchor_path_str and isinstance(anchor_path_str, str) and anchor_path_str.startswith('/'):
             try:
                anchor_path = Sdf.Path(anchor_path_str)
                sublayer_stage.OverridePrim(anchor_path)
                parent_prim_path = anchor_path
                print(f"  Anchoring light to '{anchor_obj.name}' using stored prim path: {anchor_path_str}")
             except Exception as e:
                operator.report({'WARNING'}, f"Invalid stored anchor path '{anchor_path_str}' on '{anchor_obj.name}': {e}. Using default path.")
                parent_prim_path = None # Fallback to default
        else:
            operator.report({'WARNING'}, f"Anchor object '{anchor_obj.name}' selected, but it lacks a valid 'usd_prim_path' custom property. Using default path.")
            parent_prim_path = None # Fallback to default
    
    if not parent_prim_path:
        # Default parent path if no valid anchor
        remix_assets_path = Sdf.Path("/RootNode/remix_assets")
        sublayer_stage.DefinePrim(remix_assets_path, "Xform")
        parent_prim_path = remix_assets_path
        print(f"  No valid anchor object or path found. Using default path: {parent_prim_path}")

    # --- Define Light Prim --- 
    light_prim_path = parent_prim_path.AppendPath(light_name_sanitized)

    # Define prim without specifier args for API schema - use OverridePrim instead of DefinePrim
    light_prim = sublayer_stage.OverridePrim(light_prim_path)
    if not light_prim:
        operator.report({'ERROR'}, f"Failed to define light prim {light_prim_path}")
        return False

    print(f"  Defined {usd_light_type} prim at {light_prim_path}")
    
    # Explicitly set the prim's type to match the desired light type
    if not UsdGeom.Xform(light_prim).GetPrim().IsDefined():
        UsdGeom.Xform.Define(sublayer_stage, light_prim_path)
    light_prim.SetTypeName(usd_light_type)

    # --- Apply API Schemas (Shaping) --- 
    # Apply ShapingAPI explicitly IF shaping attrs exist OR if it's a SphereLight 
    # (redundant with DefinePrim args, but ensures API object is available)
    if shaping_attrs or usd_light_type == "SphereLight":
        UsdLux.ShapingAPI.Apply(light_prim)

    # --- Set Attributes --- 
    # Use the string keys directly from the dictionaries
    for attr_name, value in light_attrs.items():
        try:
            # Determine value type name explicitly
            value_type_name = None
            if isinstance(value, Gf.Vec3f):
                value_type_name = Sdf.ValueTypeNames.Float3
            elif isinstance(value, float):
                value_type_name = Sdf.ValueTypeNames.Float
            elif isinstance(value, int):
                 value_type_name = Sdf.ValueTypeNames.Int
            # Add other types as needed (e.g., Bool, String, Asset)
            
            if not value_type_name:
                print(f"    WARNING: Could not determine USD type for attribute {attr_name}, type {type(value)}. Skipping.")
                continue 
            
            usd_attr = light_prim.CreateAttribute(attr_name, value_type_name)
            if usd_attr:
                usd_attr.Set(value)
                print(f"    Set {attr_name}: {value}")
            else:
                 print(f"    WARNING: Could not create attribute {attr_name}")
        except Exception as e:
            print(f"    ERROR setting attribute {attr_name} with value {value}: {e}")

    if shaping_attrs:
        for attr_name, value in shaping_attrs.items(): # attr_name already includes namespace
             try:
                # Determine value type name explicitly
                value_type_name = None
                if isinstance(value, float):
                    value_type_name = Sdf.ValueTypeNames.Float
                elif isinstance(value, int):
                    value_type_name = Sdf.ValueTypeNames.Int
                # Add other types as needed
                
                if not value_type_name:
                    print(f"    WARNING: Could not determine USD type for shaping attribute {attr_name}. Skipping.")
                    continue
                    
                usd_attr = light_prim.CreateAttribute(attr_name, value_type_name)
                if usd_attr:
                    usd_attr.Set(value)
                    print(f"    Set {attr_name}: {value}")
                else:
                    print(f"    WARNING: Could not create shaping attribute {attr_name}")
             except Exception as e:
                print(f"    ERROR setting shaping attribute {attr_name} with value {value}: {e}")
                
    # Set Extent for Area Lights
    if extent: # Extent is already a Vt.Vec3fArray
        try:
            # Extent attribute name is just "extent"
            extent_attr = light_prim.CreateAttribute("extent", Sdf.ValueTypeNames.Float3Array)
            if extent_attr:
                extent_attr.Set(extent)
                print(f"    Set extent: {extent}")
            else:
                print("    WARNING: Could not create extent attribute")
        except Exception as e:
             print(f"    ERROR setting extent: {e}")

    # --- Apply Transform --- 
    # Calculate transform relative to anchor if one exists
    if anchor_obj:
        try:
            anchor_matrix_world = anchor_obj.matrix_world
            obj_matrix_world = obj.matrix_world
            relative_matrix = anchor_matrix_world.inverted() @ obj_matrix_world
            loc, rot, scale_vec = relative_matrix.decompose()
            print(f"  Applying relative transform to anchor '{anchor_obj.name}'")
        except Exception as e:
            print(f"  WARNING: Could not calculate relative transform for light {obj.name}: {e}. Applying world transform.")
            # Fallback to world transform if calculation fails
            loc, rot, scale_vec = obj.matrix_world.decompose()
    else:
        # If no anchor, use world transform directly
        loc, rot, scale_vec = obj.matrix_world.decompose()
        print("  Applying world transform (no anchor).")

    # Apply global export scale to translation and scale for the light (NO coordinate swap)
    # Hardcode scale to 1.0 for lights to maintain exact positioning as it worked at 1.00 scale
    effective_translate = Gf.Vec3d(
        loc[0],  # Use original position without export scale factor
        loc[1],  # Use original position without export scale factor
        loc[2]   # Use original position without export scale factor
    )
    # Don't scale the light's transform scale - keep original scale to preserve light properties
    effective_scale = Gf.Vec3f(
        scale_vec[0],  # Use original scale without export scale factor
        scale_vec[1],  # Use original scale without export scale factor
        scale_vec[2]   # Use original scale without export scale factor
    )

    xform_api = UsdGeom.XformCommonAPI(light_prim)
    # Use the calculated loc, rot, scale (either relative or world)
    # Explicitly unpack vector components for Gf types
    xform_api.SetTranslate(effective_translate)
    rotation_degrees_xyz = tuple(math.degrees(a) for a in rot.to_euler('XYZ'))
    # Ensure SetRotate and SetScale use Gf.Vec3f as required by the API
    xform_api.SetRotate(Gf.Vec3f(rotation_degrees_xyz[0], rotation_degrees_xyz[1], rotation_degrees_xyz[2]), UsdGeom.XformCommonAPI.RotationOrderXYZ) 
    xform_api.SetScale(effective_scale)
    print(f"  Set transform: T={effective_translate[:]}, R={rotation_degrees_xyz}, S={effective_scale[:]} (original S={scale_vec[:]}), using 1.0 scale for lights\n")

    # Bind material if exported
    if obj.material_slots:
        bl_mat = obj.material_slots[0].material # Use first material slot
        if bl_mat:
            # Pass the light_prim_path as the parent_mesh_path to use the NVIDIA Remix style
            material_path = export_material(bl_mat, sublayer_stage, project_root, target_sublayer_path, parent_mesh_path=light_prim_path, obj=obj)
            if material_path:
                # Add binding strength
                UsdShade.MaterialBindingAPI(light_prim).Bind(UsdShade.Material(sublayer_stage.GetPrimAtPath(material_path)), bindingStrength=UsdShade.Tokens.strongerThanDescendants)
                print(f"  Bound material: {material_path} with strength")

    print(f"--- Finished Exporting Light: {obj.name} ---")
    return True

# --- Core Export Logic Helper ---

def _export_remix_objects(operator, context, target_filepath, selected_objects, material_replacement_mode=False):
    """Internal helper to export selected objects to a target USD file."""
    if not USD_AVAILABLE:
        operator.report({'ERROR'}, "USD Python libraries (pxr) not available.")
        return False, 0, 0 # Indicate failure, success count, fail count

    if not selected_objects:
        operator.report({'WARNING'}, "No objects selected for export.")
        return False, 0, 0

    mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
    if not mod_file_path or not os.path.exists(os.path.dirname(mod_file_path)):
        operator.report({'ERROR'}, "Project path (from mod file) not valid.")
        return False, 0, 0

    project_root_dir = os.path.dirname(mod_file_path)

    # Ensure MDL files are copied to the project
    ensure_mdl_files(project_root_dir)

    # Ensure target path is absolute
    target_filepath_abs = os.path.abspath(target_filepath)
    print(f"\n--- Starting Export ---")
    print(f"Target File: {target_filepath_abs}")
    print(f"Project Root: {project_root_dir}")
    print(f"Objects Selected: {len(selected_objects)}")
    print(f"Material Replacement Mode: {material_replacement_mode}")
    
    # Use the get_or_create_stage method from the calling operator instance
    # This requires the helper function to be called from within an operator method
    # or for the operator instance to be passed in.
    stage = operator.get_or_create_stage(target_filepath_abs)
    if not stage:
        # Error reported in get_or_create_stage
        return False, 0, 0

    success_count = 0
    fail_count = 0

    # --- Loop and Export ---
    for obj in selected_objects:
        export_successful = False
        
        if material_replacement_mode:
            # Only handle meshes in material replacement mode
            if obj.type == 'MESH':
                export_successful = export_material_replacement(operator, context, obj, stage, project_root_dir, target_filepath_abs)
            else:
                print(f"Skipping non-mesh object in material replacement mode: {obj.name} ({obj.type})")
                continue  # Don't count as failure
        else:
            # Normal export mode
            if obj.type == 'MESH':
                # Pass project_root_dir and the actual target_filepath_abs
                export_successful = operator.export_mesh(context, obj, stage, project_root_dir, target_filepath_abs)
            elif obj.type == 'LIGHT':
                # Pass project_root_dir and the actual target_filepath_abs
                export_successful = export_light(operator, context, obj, stage, project_root_dir, target_filepath_abs)
            else:
                print(f"Skipping unsupported object type: {obj.name} ({obj.type})")
                # Don't count unsupported as failure, just skip
                continue 

        if export_successful:
            success_count += 1
        else:
            fail_count += 1
            # Error should have been reported by export_mesh/export_light

    # --- Save Stage ---
    if stage.GetRootLayer().dirty:
        try:
            stage.GetRootLayer().Save()
            print(f"Saved changes to: {target_filepath_abs}")
        except Exception as e:
            operator.report({'ERROR'}, f"Failed to save stage {target_filepath_abs}: {e}")
            # Return failure if save fails, even if exports were successful
            return False, success_count, fail_count

    print(f"--- Export Finished: {success_count} succeeded, {fail_count} failed ---")
    return True, success_count, fail_count

# --- Export Operator (Sublayer) ---

class ExportRemixAsset(Operator):
    """Export selected Blender object(s) as RTX Remix assets to a USD sublayer"""
    bl_idname = "export_scene.rtx_remix_asset"
    bl_label = "Export Remix Asset"
    bl_options = {'REGISTER', 'UNDO'}

    material_replacement_mode: bpy.props.BoolProperty(
        name="Material Replacement Mode",
        description="Only replace materials on existing meshes, without exporting mesh data",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        # Check if USD is available, a project mod file is loaded,
        # and a valid sublayer is selected in the scene properties.
        if not USD_AVAILABLE:
            cls.poll_message_set("USD libraries (pxr) not found.")
            return False
            
        # Resolve the mod file path before checking existence
        mod_file_path_raw = context.scene.remix_mod_file_path
        mod_file_path_abs = bpy.path.abspath(mod_file_path_raw) if mod_file_path_raw else ""

        if not mod_file_path_abs or not os.path.exists(mod_file_path_abs):
            cls.poll_message_set("Load a Remix project mod file first (ensure path is valid).")
            return False
        # Corrected Check: Only verify remix_active_sublayer_path is set
        if not context.scene.remix_active_sublayer_path:
            cls.poll_message_set("Select a target sublayer in the Remix Project panel.")
            return False
        if not context.selected_objects:
            cls.poll_message_set("No objects selected for export.")
            return False
        return True

    def get_or_create_stage(self, file_path):
        """Opens an existing USD stage or creates a new one."""
        try:
            if os.path.exists(file_path):
                stage = Usd.Stage.Open(file_path)
                if not stage:
                    self.report({'ERROR'}, f"Failed to open existing stage: {file_path}")
                    return None
                print(f"Opened existing stage: {file_path}")
                # If opening existing, just return the stage
                return stage 
            else:
                # If creating new, THEN add structure and metadata
                stage = Usd.Stage.CreateNew(file_path)
                if not stage:
                    self.report({'ERROR'}, f"Failed to create new stage: {file_path}")
                    return None
                print(f"Created new stage: {file_path}")

                # Set initial stage/layer metadata immediately after creation
                root_layer = stage.GetRootLayer()
                UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
                root_layer.comment = "Generated by RTX Remix Toolkit"
                UsdGeom.SetStageMetersPerUnit(stage, 1.0)
                root_layer.startTimeCode = 0
                root_layer.endTimeCode = 100
                root_layer.timeCodesPerSecond = 24
                print("  Set initial stage/layer metadata (comment, metersPerUnit, timeCodes, upAxis)")
                
                # Define default prim and axis for new stages
                # Determine if it's a mesh data file or a sublayer based on path
                is_mesh_data_file = "assets/ingested" in file_path.replace('\\', '/')
                if is_mesh_data_file:
                    # Create structure for mesh data files
                    ref_target_path = Sdf.Path("/ReferenceTarget")
                    ref_target_prim = stage.DefinePrim(ref_target_path, "Xform") # Use DefinePrim for NEW structure
                    # Set kind for ReferenceTarget using SetMetadata and string value
                    ref_target_prim.SetMetadata(Sdf.PrimSpec.KindKey, "group")
                    print(f"  Set kind=group on {ref_target_path}")
                    # Set defaultPrim FOR NEW STAGE
                    stage.SetDefaultPrim(ref_target_prim)
                    print(f"  Set stage defaultPrim to {ref_target_path}")
                    # Set metadata specific to mesh data files AFTER stage creation
                    stage.GetRootLayer().comment = "Generated by RTX Remix Toolkit"
                    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
                    print("  Set comment and metersPerUnit for new mesh data file")
                    # Set time codes for new mesh data files
                    root_layer.startTimeCode = 0
                    root_layer.endTimeCode = 100
                    print("  Set startTimeCode and endTimeCode")

                else:
                    # Create basic structure for new sublayers
                    root_prim_path = Sdf.Path("/RootNode") 
                    root_prim = stage.OverridePrim(root_prim_path)
                    # Don't set default prim for sublayers
                    # stage.SetDefaultPrim(root_prim) 

                UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
                
                # Add custom layer data 
                layer_type = "data" if is_mesh_data_file else "replacement"
                custom_data = {
                    'lightspeed_game_name': bpy.context.scene.remix_game_name,
                    'lightspeed_layer_type': layer_type,
                    # Add renderSettings dictionary like in toolkit export
                    'renderSettings': {},
                    # Removed other lightspeed keys
                }
                root_layer.customLayerData = custom_data
                
                # Add other metadata (timeCodesPerSecond is often useful)
                root_layer.timeCodesPerSecond = 24
                # Remove defaultPrim from layer metadata if it exists
                # IMPORTANT: Only clear defaultPrim for NON-mesh data files (sublayers)
                # It should be kept for mesh data files where we set it earlier.
                if not is_mesh_data_file and root_layer.HasDefaultPrim():
                    root_layer.ClearDefaultPrim()
                
                # Add comment to mesh data file like toolkit export
                root_layer.comment = "Generated by RTX Remix Toolkit"
                
                return stage
        except Exception as e:
            self.report({'ERROR'}, f"Error opening/creating stage {file_path}: {e}")
            return None

    def export_mesh(self, context, obj, sublayer_stage, project_root, target_sublayer_path):
        """Exports a single Blender mesh object to its own USD file and references it."""
        print(f"\n--- Exporting Mesh: {obj.name} ---")

        # --- Get Global Export Scale --- 
        remix_export_scale = context.scene.remix_export_scale
        print(f"  Using global export scale: {remix_export_scale}")

        # --- Apply All Transforms (Location, Rotation, Scale) ---
        # This fixes coordinate issues in-game by ensuring the mesh data has transforms baked in
        # and results in clean transform values as shown in the user's image
        print(f"  DEBUG: remix_auto_apply_transforms setting: {context.scene.remix_auto_apply_transforms}")
        print(f"  DEBUG: Object type: {obj.type}")
        
        if obj.type == 'MESH' and context.scene.remix_auto_apply_transforms:
            # Store original selection and active object
            original_selection = context.selected_objects[:]
            original_active = context.view_layer.objects.active
            
            # Show current transform values before applying
            print(f"  DEBUG: BEFORE transform application:")
            print(f"    Location: {obj.location[:]}")
            print(f"    Rotation (Euler): {obj.rotation_euler[:]}")
            print(f"    Rotation (Quaternion): {obj.rotation_quaternion[:]}")
            print(f"    Scale: {obj.scale[:]}")
            
            try:
                # Clear selection and make this object active
                # This is necessary because Blender doesn't support applying transforms 
                # to multiple objects simultaneously
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                context.view_layer.objects.active = obj
                
                # Check if any transforms need to be applied
                needs_location_apply = any(abs(l) > 0.001 for l in obj.location)
                needs_rotation_apply = any(abs(r) > 0.001 for r in obj.rotation_euler) or \
                                     any(abs(r) > 0.001 for r in obj.rotation_quaternion[1:]) or \
                                     abs(obj.rotation_quaternion[0] - 1.0) > 0.001
                needs_scale_apply = any(abs(s - 1.0) > 0.001 for s in obj.scale)
                
                print(f"  DEBUG: Transform analysis:")
                print(f"    Needs location apply: {needs_location_apply}")
                print(f"    Needs rotation apply: {needs_rotation_apply}")
                print(f"    Needs scale apply: {needs_scale_apply}")
                
                if needs_location_apply or needs_rotation_apply or needs_scale_apply:
                    print(f"  Applying all transforms - Location: {needs_location_apply}, Rotation: {needs_rotation_apply}, Scale: {needs_scale_apply}")
                    
                    # Check if mesh data is shared with other objects (multi-user)
                    if obj.data.users > 1:
                        print(f"  Mesh data '{obj.data.name}' is shared by {obj.data.users} objects - making unique copy")
                        # Make the mesh data unique to this object so we can apply transforms
                        obj.data = obj.data.copy()
                        print(f"  Created unique mesh data copy: '{obj.data.name}'")
                    
                    # Apply ALL transforms (location, rotation, and scale)
                    # This will result in clean transform values: Location=(0,0,0), Rotation=(0,0,0), Scale=(1,1,1)
                    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
                    
                    # Show transform values after applying
                    print(f"  DEBUG: AFTER transform application:")
                    print(f"    Location: {obj.location[:]}")
                    print(f"    Rotation (Euler): {obj.rotation_euler[:]}")
                    print(f"    Rotation (Quaternion): {obj.rotation_quaternion[:]}")
                    print(f"    Scale: {obj.scale[:]}")
                    
                    # Mark that the object needs reprocessing since its mesh data changed
                    if "remix_processed" in obj:
                        del obj["remix_processed"]
                    if "remix_mesh_file_path" in obj:
                        # Keep the path but mark for reprocessing
                        pass
                    
                    print(f"    Applied all transforms to mesh - object now has identity transforms")
                else:
                    print(f"  No transform application needed (all transforms are already identity)")
                    
            except Exception as e:
                print(f"  WARNING: Failed to apply transforms: {e}")
                import traceback
                traceback.print_exc()
                # Continue with export even if transform application fails
                
            finally:
                # Restore original selection and active object
                bpy.ops.object.select_all(action='DESELECT')
                for selected_obj in original_selection:
                    if selected_obj.name in bpy.data.objects:
                        bpy.data.objects[selected_obj.name].select_set(True)
                if original_active and original_active.name in bpy.data.objects:
                    context.view_layer.objects.active = bpy.data.objects[original_active.name]
        elif obj.type == 'MESH':
            print(f"  Skipping transform application: remix_auto_apply_transforms is disabled")
        else:
            print(f"  Skipping transform application: object is not a mesh (type: {obj.type})")

        # --- Check if asset has been processed before ---
        needs_asset_reprocessing = True
        mesh_file_path = None
        material_path = None
        
        # Check for the processed flag and stored file path
        if "remix_processed" in obj and "remix_mesh_file_path" in obj:
            stored_mesh_path = obj["remix_mesh_file_path"]
            # If the object is marked as processed and the file exists, we can skip asset reprocessing
            if obj["remix_processed"] and os.path.exists(stored_mesh_path):
                needs_asset_reprocessing = False
                mesh_file_path = stored_mesh_path
                # Also get stored material path if available
                if "remix_material_path" in obj:
                    stored_material_path = obj["remix_material_path"]
                    # Convert string path to Sdf.Path object
                    if isinstance(stored_material_path, str) and stored_material_path:
                        material_path = Sdf.Path(stored_material_path)
                    print(f"  Asset already processed, using existing file: {mesh_file_path}")

        # --- Ensure Directories ---
        remix_dir = os.path.join(project_root, "rtx-remix")
        # Change meshes directory to assets/ingested to match reference format
        ingested_dir = os.path.join(project_root, "assets", "ingested")
        textures_dir = os.path.join(remix_dir, "textures") # Ensure textures dir exists too
        os.makedirs(ingested_dir, exist_ok=True)
        os.makedirs(textures_dir, exist_ok=True) # Create textures dir if needed

        # --- Mesh File Path and Stage ---
        mesh_name_sanitized = sanitize_prim_name(obj.name)
        # Change extension to .usda for easier debugging
        mesh_file_name = f"{mesh_name_sanitized}.usda"
        
        if mesh_file_path is None:
            mesh_file_path = os.path.normpath(os.path.join(ingested_dir, mesh_file_name))
        
        # Skip mesh processing if it has already been processed and hasn't changed
        if needs_asset_reprocessing:
            # Create or open the stage for the mesh data file
            mesh_stage = self.get_or_create_stage(mesh_file_path)
            if not mesh_stage:
                return False
            # Only print this when we're actually creating a new stage, not opening an existing one
            if not os.path.exists(mesh_file_path):
                print(f"Created new mesh data stage: {mesh_file_path}")
            else:
                print(f"Updated existing mesh data stage: {mesh_file_path}")

            # --- Define Structure expected by Remix Toolkit ingest --- 
            ref_target_path = Sdf.Path("/ReferenceTarget")
            xforms_path = ref_target_path.AppendPath("XForms")
            mesh_prim_path = xforms_path.AppendPath(mesh_name_sanitized) 

            ref_target_prim = mesh_stage.DefinePrim(ref_target_path, "Xform")
            # Set kind on ReferenceTarget
            ref_target_prim.SetMetadata(Sdf.PrimSpec.KindKey, "group")
            print(f"  Ensured kind=group on {ref_target_path}")

            xforms_prim = mesh_stage.DefinePrim(xforms_path, "Xform")
            # Set kind on XForms
            xforms_prim.SetMetadata(Sdf.PrimSpec.KindKey, "group")
            print(f"  Ensured kind=group on {xforms_path}")

            mesh_prim = mesh_stage.DefinePrim(mesh_prim_path, "Mesh")

            if not mesh_prim:
                 self.report({'ERROR'}, f"Failed to define mesh prim {mesh_prim_path} in {mesh_file_path}")
                 # mesh_stage.GetRootLayer().Clear() # Don't clear if stage creation worked
                 return False

            # Add identity transform ops to /ReferenceTarget to match toolkit export
            ref_target_xform_api = UsdGeom.XformCommonAPI(ref_target_prim)
            ref_target_xform_api.SetTranslate(Gf.Vec3d(0,0,0), Usd.TimeCode.Default())
            ref_target_xform_api.SetRotate(Gf.Vec3f(0,0,0), UsdGeom.XformCommonAPI.RotationOrderXYZ, Usd.TimeCode.Default())
            ref_target_xform_api.SetScale(Gf.Vec3f(1,1,1), Usd.TimeCode.Default())
            # Set xformOpOrder via CreateAttribute on the prim
            ref_target_prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"])
            print(f"  Added identity xformOps to {ref_target_path}")

            # Set default prim to ReferenceTarget if not already set
            # Default prim setting moved to get_or_create_stage for new files.
            # We shouldn't change it here if the file already existed.

            # Apply MaterialBindingAPI schema to the mesh prim
            UsdShade.MaterialBindingAPI.Apply(mesh_prim)
            print(f"  Applied MaterialBindingAPI to {mesh_prim_path}")
            
            # Add custom layer data to match reference format for mesh data file
            root_layer = mesh_stage.GetRootLayer()
            # Ensure context is available or fetch if needed
            if not context: 
                context = bpy.context
            
            custom_data = {
                'lightspeed_game_name': context.scene.remix_game_name,
                'lightspeed_layer_type': "data",
                # Removed other lightspeed keys
            }
            root_layer.customLayerData = custom_data
            
            # Add other metadata
            root_layer.startTimeCode = 0 
            root_layer.endTimeCode = 100 
            root_layer.timeCodesPerSecond = 24 
             
            # Define explicit transform ops to match toolkit export
            # Create attributes WITHOUT specifying custom/variability
            xforms_prim.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(0,0,0))
            xforms_prim.CreateAttribute("xformOp:rotateXYZ", Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(0,0,0))
            xforms_prim.CreateAttribute("xformOp:scale", Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(100, 100, 100)) # Example scale
            xforms_prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"])
            print(f"  Added standard xformOps to {xforms_path}")

            # --- Get Blender Mesh Data ---
            try:
                depsgraph = context.evaluated_depsgraph_get()
                eval_obj = obj.evaluated_get(depsgraph)
                mesh_data = eval_obj.to_mesh()

            except Exception as e:
                self.report({'ERROR'}, f"Failed to get evaluated mesh for {obj.name}: {e}")
                # mesh_stage.GetRootLayer().Clear() # Don't clear stage
                return False

            if not mesh_data.vertices or not mesh_data.polygons:
                self.report({'WARNING'}, f"Skipping mesh {obj.name}: No vertices or polygons.")
                eval_obj.to_mesh_clear() # Use original cleanup
                # mesh_stage.GetRootLayer().Clear()
                return False

            # --- Export Mesh Attributes (Points, Faces, UVs, Normals) ---
            points = [v.co[:] for v in mesh_data.vertices]
            mesh_prim.CreateAttribute("points", Sdf.ValueTypeNames.Point3fArray).Set(Vt.Vec3fArray(points))

            face_vertex_counts = [len(p.vertices) for p in mesh_data.polygons]
            face_vertex_indices = [v for p in mesh_data.polygons for v in p.vertices]
            mesh_prim.CreateAttribute("faceVertexCounts", Sdf.ValueTypeNames.IntArray).Set(Vt.IntArray(face_vertex_counts))
            mesh_prim.CreateAttribute("faceVertexIndices", Sdf.ValueTypeNames.IntArray).Set(Vt.IntArray(face_vertex_indices))

            mesh_prim.CreateAttribute("orientation", Sdf.ValueTypeNames.Token).Set(UsdGeom.Tokens.rightHanded)

            if mesh_data.uv_layers:
                active_uv_layer = mesh_data.uv_layers.active
                if active_uv_layer:
                    # USD expects UVs in face-varying order, matching faceVertexIndices
                    uv_data_flat = [(0.0, 0.0)] * len(mesh_data.loops) # Initialize
                    for poly in mesh_data.polygons:
                        for loop_index in poly.loop_indices:
                            uv_data_flat[loop_index] = active_uv_layer.data[loop_index].uv[:]
                    
                    if uv_data_flat:
                        uv_attr = UsdGeom.PrimvarsAPI(mesh_prim).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray)
                        uv_attr.Set(Vt.Vec2fArray(uv_data_flat))
                        uv_attr.SetInterpolation(UsdGeom.Tokens.faceVarying)
                        print(f"  Exported UVs (st) from layer '{active_uv_layer.name}'")
                    else:
                        print(f"  Active UV layer '{active_uv_layer.name}' has no data.")
                else:
                    print("  No active UV layer found.")
            else:
                print("  Mesh has no UV layers.")

            try:
                mesh_data.calc_normals_split() # Use original normals
                normals = [l.normal[:] for l in mesh_data.loops]
                if normals:
                    normals_attr = mesh_prim.CreateAttribute("normals", Sdf.ValueTypeNames.Normal3fArray)
                    normals_attr.Set(Vt.Vec3fArray(normals))
                    # Correct metadata key for interpolation on standard attributes
                    normals_attr.SetMetadata("interpolation", UsdGeom.Tokens.faceVarying)
                    print("  Exported face-varying normals.")
                else:
                    print("  Could not retrieve loop normals.")
            except Exception as e:
                print(f"  Could not calculate or export normals: {e}")

            # --- Define Default Material Structure within Mesh File --- 
            asset_importer_scope_path = xforms_path.AppendPath("AssetImporter")
            looks_scope_path = asset_importer_scope_path.AppendPath("Looks")
            mat_name_mesh_file = f"{mesh_name_sanitized}__" # Toolkit uses double underscore
            mat_path_mesh_file = looks_scope_path.AppendPath(mat_name_mesh_file)
            shader_path_mesh_file = mat_path_mesh_file.AppendPath("Shader")

            mesh_stage.DefinePrim(asset_importer_scope_path, "Scope")
            mesh_stage.DefinePrim(looks_scope_path, "Scope")
            default_mat_prim = mesh_stage.DefinePrim(mat_path_mesh_file, "Material")
            default_shader_prim = mesh_stage.DefinePrim(shader_path_mesh_file, "Shader")

            if default_mat_prim and default_shader_prim:
                # Set basic shader info (similar to toolkit export)
                default_shader_prim.CreateAttribute("info:implementationSource", Sdf.ValueTypeNames.Token).Set("sourceAsset")
                default_shader_prim.CreateAttribute("info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("AperturePBR_Opacity.mdl")) # Example MDL
                default_shader_prim.CreateAttribute("info:mdl:sourceAsset:subIdentifier", Sdf.ValueTypeNames.Token).Set("AperturePBR_Opacity") # Example Identifier
                
                # Basic shader output
                shader_api = UsdShade.Shader(default_shader_prim)
                shader_output = shader_api.CreateOutput("out", Sdf.ValueTypeNames.Token)
                shader_output.SetRenderType("material")

                # Basic material outputs pointing to shader
                material_api = UsdShade.Material(default_mat_prim)
                material_api.CreateSurfaceOutput("mdl:surface").ConnectToSource(shader_output)
                material_api.CreateDisplacementOutput("mdl:displacement").ConnectToSource(shader_output)
                material_api.CreateVolumeOutput("mdl:volume").ConnectToSource(shader_output)
                
                # Bind the Mesh prim to this default material WITH BINDING STRENGTH
                UsdShade.MaterialBindingAPI(mesh_prim).Bind(material_api, bindingStrength=UsdShade.Tokens.strongerThanDescendants)
                print(f"  Defined and bound default material structure: {mat_path_mesh_file} with strength")
            else:
                 print("  WARNING: Failed to define default material structure in mesh file.")

            # --- Save Mesh Stage ---
            # Metadata (comment, metersPerUnit) is set in get_or_create_stage or should persist if file existed

            try:
                mesh_stage.GetRootLayer().Save()
                print(f"  Saved mesh data to: {mesh_file_path}")
                
                # Mark the object as processed and store the file path
                obj["remix_processed"] = True
                obj["remix_mesh_file_path"] = mesh_file_path
                print(f"  Marked object as processed")
                
            except Exception as e:
                self.report({'ERROR'}, f"Failed to save mesh stage {mesh_file_path}: {e}")
                eval_obj.to_mesh_clear() # Use original cleanup
                return False

            # --- Cleanup Blender Mesh ---
            # Use original cleanup
            eval_obj.to_mesh_clear()

        # --- Export Material (in Sublayer) --- 
        # Store bl_mat for later
        bl_mat = None
        if material_path is None and obj.material_slots:
            bl_mat = obj.material_slots[0].material # Use first material slot
            if not bl_mat:
                print(f"  Mesh {obj.name} has an empty material slot.")
        elif material_path:
            print(f"  Using existing material: {material_path}")
        else:
            print(f"  Mesh {obj.name} has no material slots.")

        # --- Determine Target Path and Create Reference/Override --- 
        anchor_obj = context.scene.remix_anchor_object_target # Read anchor from Scene property
        
        if anchor_obj:
            # --- ANCHORED EXPORT (DEFINE NEW INSTANCE UNDER ANCHOR PATH, POSITIONED BY ANCHOR, WITH LOCAL OFFSET) ---
            anchor_path_str = anchor_obj.get("usd_prim_path", None) 
            if anchor_path_str and isinstance(anchor_path_str, str) and anchor_path_str.startswith('/'):
                try:
                    parent_group_path = Sdf.Path(anchor_path_str) 
                    over_parent_group = sublayer_stage.OverridePrim(parent_group_path)
                    if not over_parent_group:
                        self.report({'ERROR'}, f"Failed to override parent group at {parent_group_path}.")
                        return False
                    print(f"  Overriding parent group: {parent_group_path}")

                    new_instance_name = generate_uuid_name(sanitize_prim_name(obj.name), prefix="ref_") 
                    new_instance_path = parent_group_path.AppendPath(new_instance_name)
                    new_instance_xform_prim = sublayer_stage.DefinePrim(new_instance_path, "Xform")
                    if not new_instance_xform_prim:
                        self.report({'ERROR'}, f"Failed to define new instance Xform at {new_instance_path}.")
                        return False
                    print(f"  Defined new instance Xform: {new_instance_path}")

                    relative_mesh_data_path = get_relative_path(target_sublayer_path, mesh_file_path)
                    if not relative_mesh_data_path.startswith( ("./", "../") ):
                        relative_mesh_data_path = "./" + relative_mesh_data_path
                    new_instance_xform_prim.GetReferences().AddReference(assetPath=relative_mesh_data_path)
                    new_instance_xform_prim.CreateAttribute("IsRemixRef", Sdf.ValueTypeNames.Bool, custom=True).Set(True)
                    print(f"  Set reference and isRemixReference on {new_instance_path}")

                    if bl_mat:
                        # For anchored export, use the new_instance_path
                        material_path = export_material(bl_mat, sublayer_stage, project_root, target_sublayer_path, parent_mesh_path=new_instance_path, obj=obj)
                        # Store the material path for future use
                        if material_path:
                            obj["remix_material_path"] = str(material_path)

                    # 1. Outer instance transform uses ANCHOR object's world location, 
                    #    with direct coordinates (no coordinate system conversion) to match light export behavior.
                    anchor_loc, anchor_rot, anchor_scale_vec = anchor_obj.matrix_world.decompose() # Get exported object's world state
                    print(f"    DEBUG Anchor Object Decomposed: Loc={anchor_loc[:]}, QuatRot={anchor_rot[:]}, EulerRotXYZ={tuple(math.degrees(a) for a in anchor_rot.to_euler('XYZ'))}, Scale={anchor_scale_vec[:]}")

                    anchor_blender_x = anchor_loc[0]
                    anchor_blender_y = anchor_loc[1]
                    anchor_blender_z = anchor_loc[2]

                    # Use direct coordinates like lights (no coordinate system conversion)
                    instance_translate = Gf.Vec3d(
                        anchor_blender_x * remix_export_scale,
                        anchor_blender_y * remix_export_scale,
                        anchor_blender_z * remix_export_scale
                    )
                    instance_scale = Gf.Vec3f(1.0, 1.0, 1.0)
                    instance_rotation_deg = Gf.Vec3f(0.0, 0.0, 0.0) # Identity rotation for outer instance

                    new_instance_xform_prim.CreateAttribute(UsdGeom.Tokens.xformOpOrder, Sdf.ValueTypeNames.TokenArray).Set(["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"])
                    new_instance_xform_prim.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(instance_translate)
                    new_instance_xform_prim.CreateAttribute("xformOp:rotateXYZ", Sdf.ValueTypeNames.Float3).Set(instance_rotation_deg)
                    new_instance_xform_prim.CreateAttribute("xformOp:scale", Sdf.ValueTypeNames.Float3).Set(instance_scale)
                    print(f"  Set transform on NEW INSTANCE {new_instance_path} from ANCHOR OBJECT's world: T={instance_translate[:]}, R={instance_rotation_deg[:]}, S={instance_scale[:]}")

                    # 2. Inner override uses the RELATIVE transform between anchor and object, with direct relative coordinates (no coordinate system conversion).
                    internal_xforms_group_path = new_instance_path.AppendPath("XForms")
                    internal_mesh_prim_name = sanitize_prim_name(obj.name) # Name of prim inside assets/ingested/...usda
                    local_offset_prim_path = internal_xforms_group_path.AppendPath(internal_mesh_prim_name)

                    over_internal_xforms_group = sublayer_stage.OverridePrim(internal_xforms_group_path)
                    if not over_internal_xforms_group: 
                        self.report({'ERROR'}, f"Failed to override internal XForms group at {internal_xforms_group_path}.")
                        return False # Critical error
                    over_for_local_offset = sublayer_stage.OverridePrim(local_offset_prim_path)
                    if not over_for_local_offset: 
                        self.report({'ERROR'}, f"Failed to override local offset prim at {local_offset_prim_path}.")
                        return False # Critical error
                    print(f"  Overriding for local offset at: {local_offset_prim_path}")

                    relative_matrix = anchor_obj.matrix_world.inverted() @ obj.matrix_world
                    rel_loc, rel_rot, rel_scale_vec = relative_matrix.decompose()
                    print(f"    DEBUG Relative Decomposed (Blender Space): RelLoc={rel_loc[:]}, RelEulerRotXYZ={tuple(math.degrees(a) for a in rel_rot.to_euler('XYZ'))}, RelScale={rel_scale_vec[:]}")

                    # For the inner override:
                    # Translation: Use relative Blender coords, scaled by remix_export_scale (consistent with light export)
                    local_offset_translate = Gf.Vec3d(
                        rel_loc[0] * remix_export_scale, # Apply consistent export scale factor
                        rel_loc[1] * remix_export_scale, # Apply consistent export scale factor
                        rel_loc[2] * remix_export_scale  # Apply consistent export scale factor
                    )
                    
                    # Rotation: Use direct relative rotation
                    local_offset_rotation_deg = Gf.Vec3f(tuple(math.degrees(a) for a in rel_rot.to_euler('XYZ'))) # Use relative rotation
                    
                    # Scale: Should be (1,1,1) as the scaling is now handled in the translation factor above.
                    local_offset_scale = Gf.Vec3f(1.0, 1.0, 1.0) # Use relative scale

                    over_for_local_offset.CreateAttribute(UsdGeom.Tokens.xformOpOrder, Sdf.ValueTypeNames.TokenArray).Set(["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"])
                    over_for_local_offset.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(local_offset_translate)
                    over_for_local_offset.CreateAttribute("xformOp:rotateXYZ", Sdf.ValueTypeNames.Float3).Set(local_offset_rotation_deg)
                    over_for_local_offset.CreateAttribute("xformOp:scale", Sdf.ValueTypeNames.Float3).Set(local_offset_scale)
                    print(f"    Set LOCAL OFFSET on {local_offset_prim_path}: RelT={local_offset_translate[:]}, RelR={local_offset_rotation_deg[:]}, RelS={local_offset_scale[:]}")

                    # Material Binding: Bind to the NEWLY CREATED INSTANCE PRIM
                    if material_path and new_instance_xform_prim: # Check new_instance_xform_prim
                        # We don't need to set explicit material binding for RTX Remix compatibility
                        # The material path is already set correctly for the mesh in its hierarchy
                        print(f"    Material path {material_path} is set for reference {new_instance_path}")
                        
                except Exception as e:
                    self.report({'WARNING'}, f"Error processing anchor override for '{anchor_obj.name}' (path: {anchor_path_str}): {e}. Falling back to default export.")
                    traceback.print_exc() # Add traceback for debugging
                    anchor_obj = None # Force fallback to default non-anchored export
            else:
                self.report({'WARNING'}, f"Anchor object '{anchor_obj.name}' selected, but lacks a valid 'usd_prim_path' custom property. Using default export path.")
                anchor_obj = None # Force fallback

        if not anchor_obj:
            # --- DEFAULT EXPORT (NO ANCHOR / FALLBACK) --- 
            # This is not fully implemented yet, may explode your house or something.
            # Define under /RootNode/remix_assets
            remix_assets_path = Sdf.Path("/RootNode/remix_assets") 
            # Use OverridePrim instead of DefinePrim for "over" statements
            sublayer_stage.OverridePrim(remix_assets_path)
            parent_prim_path = remix_assets_path
            print(f"  Exporting as new asset (no valid anchor). Parent path: {parent_prim_path}")
            
            # Create UUID-style name for RTX Remix compatibility
            uuid_style_name = generate_uuid_name(mesh_name_sanitized, prefix="ref_")
            
            # Create Xform prim under the default path
            xform_prim_path = parent_prim_path.AppendPath(uuid_style_name) 
            # Use OverridePrim instead of DefinePrim
            xform_prim = sublayer_stage.OverridePrim(xform_prim_path)
            if not xform_prim:
                self.report({'ERROR'}, f"Failed to override Xform prim {xform_prim_path} in sublayer")
                return False

            # --- Now let's export the material if we have one ---
            if bl_mat:
                # For standard export, use the xform_prim_path
                material_path = export_material(bl_mat, sublayer_stage, project_root, target_sublayer_path, parent_mesh_path=xform_prim_path, obj=obj)
                # Store the material path for future use
                if material_path:
                    obj["remix_material_path"] = str(material_path)

            # Add the custom Remix reference flag (might still be useful for non-replacement assets?)
            try:
                 # Match reference: camelCase name, no explicit value set
                 # Ensure this *is* marked as custom
                is_remix_ref_attr = xform_prim.CreateAttribute("IsRemixRef", Sdf.ValueTypeNames.Bool, custom=True)
                # is_remix_ref_attr.Set(True) # Don't set value explicitly
                print(f"    Declared custom bool isRemixReference")
            except Exception as e:
                print(f"    WARNING: Could not declare isRemixReference custom attribute: {e}")

            # Add reference to the mesh data file
            relative_mesh_path = get_relative_path(target_sublayer_path, mesh_file_path)
            # Ensure relative path starts with ./ for same dir or subdirs
            if not relative_mesh_path.startswith(".."):
                 relative_mesh_path = f"./{relative_mesh_path}"
                 
            # Explicitly reference the correct prim path within the mesh file
            # Update: Reference should ONLY contain assetPath for sublayer references
            # mesh_data_prim_path = Sdf.Path(f"/ReferenceTarget/XForms/{mesh_name_sanitized}")
            xform_prim.GetReferences().AddReference(assetPath=relative_mesh_path)
            print(f"  Added reference {relative_mesh_path}")

            # Add Transform (World Transform)
            xform_api = UsdGeom.XformCommonAPI(xform_prim) # Use xform_prim, not instance_xform_prim
            loc, rot, scale_vec = obj.matrix_world.decompose()

            # Apply the global export scale to translation and scale (NO coordinate swap for default export)
            effective_translate = Gf.Vec3d(
                loc[0] * remix_export_scale,
                loc[1] * remix_export_scale,
                loc[2] * remix_export_scale
            )
            effective_scale = Gf.Vec3f(1.0, 1.0, 1.0) # Target Scale is 1
            rotation_degrees_xyz = Gf.Vec3f(tuple(math.degrees(a) for a in rot.to_euler('XYZ'))) # Should be near (0,0,0)

            # Use xform_prim here as this is the default export path
            xform_prim.CreateAttribute(UsdGeom.Tokens.xformOpOrder, Sdf.ValueTypeNames.TokenArray).Set(["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"])
            xform_prim.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(effective_translate)
            xform_prim.CreateAttribute("xformOp:rotateXYZ", Sdf.ValueTypeNames.Float3).Set(rotation_degrees_xyz)
            xform_prim.CreateAttribute("xformOp:scale", Sdf.ValueTypeNames.Float3).Set(effective_scale)
            print(f"  Set world transform on Xform {xform_prim_path}: T={effective_translate[:]}, R={rotation_degrees_xyz[:]}, S={effective_scale[:]}")

            # Apply an identity override transform INSIDE the instance xform (matching anchored setup)
            # This part should also use xform_prim for consistency if it's being applied to the same prim
            internal_xforms_group_path_default = xform_prim_path.AppendPath("XForms") # Use xform_prim_path
            internal_mesh_prim_name_default = sanitize_prim_name(obj.name)
            local_offset_prim_path_default = internal_xforms_group_path_default.AppendPath(internal_mesh_prim_name_default)

            over_internal_xforms_group_default = sublayer_stage.OverridePrim(internal_xforms_group_path_default)
            over_for_local_offset_default = sublayer_stage.OverridePrim(local_offset_prim_path_default)
            if over_internal_xforms_group_default and over_for_local_offset_default:
                over_for_local_offset_default.CreateAttribute(UsdGeom.Tokens.xformOpOrder, Sdf.ValueTypeNames.TokenArray).Set(["xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"])
                over_for_local_offset_default.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(0,0,0))
                over_for_local_offset_default.CreateAttribute("xformOp:rotateXYZ", Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(0,0,0))
                over_for_local_offset_default.CreateAttribute("xformOp:scale", Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(1,1,1))
                print(f"    Set identity LOCAL OFFSET on {local_offset_prim_path_default}")
            else:
                 print(f"  WARNING: Failed to create override prims for identity local offset in default export.")

            # Bind material if exported
            if material_path:
                # We don't need to set explicit material binding for RTX Remix compatibility
                # The material path is already set correctly for the mesh in its hierarchy
                print(f"  Material path {material_path} is set for reference {xform_prim_path}")

        print(f"--- Finished Exporting Mesh: {obj.name} ---")
        return True

    def execute(self, context):
        # Initial checks already done by poll

        selected_objects = context.selected_objects # Get selection once

        # --- Get Target Path from Scene Properties ---
        target_sublayer_path = context.scene.remix_active_sublayer_path
        if not target_sublayer_path: # Should be caught by poll, but double-check
             self.report({'ERROR'}, "No active sublayer path set.")
             return {'CANCELLED'}

        # Ensure path is absolute (though it likely is already if set via UI)
        target_sublayer_path_abs = os.path.abspath(target_sublayer_path)

        # --- Call the core export logic ---
        success, success_count, fail_count = _export_remix_objects(
            self, 
            context, 
            target_sublayer_path_abs, 
            selected_objects,
            material_replacement_mode=self.material_replacement_mode
        )

        # --- Report final status ---
        if not success and fail_count == 0 and success_count == 0:
             # Indicates a setup error before export loop (e.g., stage creation failed)
             # Error message already reported by _export_remix_objects or get_or_create_stage
             return {'CANCELLED'} 
        elif fail_count > 0:
            self.report({'WARNING'}, f"Export to sublayer finished with {success_count} successes and {fail_count} failures.")
            return {'FINISHED'}
        elif success_count > 0:
             self.report({'INFO'}, f"Successfully exported {success_count} objects to sublayer.")
             return {'FINISHED'}
        else: # success is True, but success_count is 0 (e.g., only unsupported types selected)
             self.report({'WARNING'}, "No supported objects were exported to sublayer.")
             return {'CANCELLED'}

# --- Export Operator (Directly to mod.usda File for Hotloading) ---

class ExportRemixModFile(Operator):
    """Export selected Blender object(s) directly to the main mod.usda file (for potential hotloading)"""
    bl_idname = "export_scene.rtx_remix_mod_file"
    bl_label = "Export Selected to mod.usda (Hotload)"
    bl_options = {'REGISTER', 'UNDO'}

    material_replacement_mode: bpy.props.BoolProperty(
        name="Material Replacement Mode",
        description="Only replace materials on existing meshes, without exporting mesh data",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        # Check if USD is available and a project mod file is loaded.
        if not USD_AVAILABLE:
            cls.poll_message_set("USD libraries (pxr) not found.")
            return False
            
        mod_file_path_raw = context.scene.remix_mod_file_path
        mod_file_path_abs = bpy.path.abspath(mod_file_path_raw) if mod_file_path_raw else ""

        if not mod_file_path_abs or not os.path.exists(mod_file_path_abs):
            cls.poll_message_set("Load a Remix project mod file first.")
            return False
        if not context.selected_objects:
            cls.poll_message_set("No objects selected for export.")
            return False
        return True

    # Draw method to show operator properties in the UI
    def draw(self, context):
        layout = self.layout
        layout.prop(self, "material_replacement_mode")

    # --- Make helper methods available to this operator instance ---
    # Reuse the stage creation/opening logic
    get_or_create_stage = ExportRemixAsset.get_or_create_stage 
    # Reuse the mesh export logic
    export_mesh = ExportRemixAsset.export_mesh
    # Light export is a standalone function, so it's available directly

    def execute(self, context):
        # Initial checks already done by poll

        selected_objects = context.selected_objects # Get selection once

        # --- Get Target Path (the mod file itself) --- 
        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        if not mod_file_path: # Should be caught by poll
             self.report({'ERROR'}, "Mod file path is not set.")
             return {'CANCELLED'}

        # --- Call the core export logic --- 
        success, success_count, fail_count = _export_remix_objects(
            self, 
            context, 
            mod_file_path, # Export directly to the mod file path
            selected_objects,
            material_replacement_mode=self.material_replacement_mode # Ensure this uses the property value
        )

        # --- Report final status --- 
        if not success and fail_count == 0 and success_count == 0:
             # Setup error
             return {'CANCELLED'} 
        elif fail_count > 0:
            self.report({'WARNING'}, f"Export to mod.usda finished with {success_count} successes and {fail_count} failures.")
            return {'FINISHED'}
        elif success_count > 0:
             self.report({'INFO'}, f"Successfully exported {success_count} objects to mod.usda.")
             return {'FINISHED'}
        else: # success is True, but success_count is 0 (e.g., only unsupported types selected)
             self.report({'WARNING'}, "No supported objects were exported to mod.usda.")
             return {'CANCELLED'}

# --- Invalidate Asset Processing State ---
class InvalidateRemixAssets(Operator):
    """Force reprocessing of selected assets on next export"""
    bl_idname = "object.rtx_remix_invalidate_assets"
    bl_label = "Invalidate Remix Assets"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        # Check if there are any selected objects
        return context.selected_objects and USD_AVAILABLE
    
    def execute(self, context):
        invalidated_count = 0
        for obj in context.selected_objects:
            if "remix_processed" in obj:
                obj["remix_processed"] = False
                if "remix_material_path" in obj:
                    del obj["remix_material_path"] # Clear material path when invalidating
                invalidated_count += 1
                
        self.report({'INFO'}, f"Invalidated {invalidated_count} assets for reprocessing.")
        return {'FINISHED'}

# --- Test Operator for Manual Transform Application ---
class TestApplyAllTransforms(Operator):
    """Test operator to manually apply all transforms to selected objects"""
    bl_idname = "object.test_apply_all_transforms"
    bl_label = "Test: Apply All Transforms"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return context.selected_objects and any(obj.type == 'MESH' for obj in context.selected_objects)
    
    def execute(self, context):
        mesh_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
        
        if not mesh_objects:
            self.report({'WARNING'}, "No mesh objects selected.")
            return {'CANCELLED'}
        
        # Store original selection and active object
        original_selection = context.selected_objects[:]
        original_active = context.view_layer.objects.active
        
        applied_count = 0
        
        try:
            for obj in mesh_objects:
                print(f"\n--- Testing Transform Application for: {obj.name} ---")
                
                # Show current transform values before applying
                print(f"  BEFORE transform application:")
                print(f"    Location: {obj.location[:]}")
                print(f"    Rotation (Euler): {obj.rotation_euler[:]}")
                print(f"    Scale: {obj.scale[:]}")
                
                # Clear selection and make this object active
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                context.view_layer.objects.active = obj
                
                # Check if any transforms need to be applied
                needs_location_apply = any(abs(l) > 0.001 for l in obj.location)
                needs_rotation_apply = any(abs(r) > 0.001 for r in obj.rotation_euler) or \
                                     any(abs(r) > 0.001 for r in obj.rotation_quaternion[1:]) or \
                                     abs(obj.rotation_quaternion[0] - 1.0) > 0.001
                needs_scale_apply = any(abs(s - 1.0) > 0.001 for s in obj.scale)
                
                print(f"  Transform analysis:")
                print(f"    Needs location apply: {needs_location_apply}")
                print(f"    Needs rotation apply: {needs_rotation_apply}")
                print(f"    Needs scale apply: {needs_scale_apply}")
                
                if needs_location_apply or needs_rotation_apply or needs_scale_apply:
                    print(f"  Applying all transforms...")
                    
                    # Check if mesh data is shared with other objects (multi-user)
                    if obj.data.users > 1:
                        print(f"  Mesh data '{obj.data.name}' is shared by {obj.data.users} objects - making unique copy")
                        # Make the mesh data unique to this object so we can apply transforms
                        obj.data = obj.data.copy()
                        print(f"  Created unique mesh data copy: '{obj.data.name}'")
                    
                    # Apply ALL transforms (location, rotation, and scale)
                    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
                    
                    # Show transform values after applying
                    print(f"  AFTER transform application:")
                    print(f"    Location: {obj.location[:]}")
                    print(f"    Rotation (Euler): {obj.rotation_euler[:]}")
                    print(f"    Scale: {obj.scale[:]}")
                    
                    applied_count += 1
                    print(f"  ✓ Applied all transforms to {obj.name}")
                else:
                    print(f"  ✓ No transform application needed for {obj.name} (already identity)")
                    
        except Exception as e:
            self.report({'ERROR'}, f"Failed to apply transforms: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}
            
        finally:
            # Restore original selection and active object
            bpy.ops.object.select_all(action='DESELECT')
            for selected_obj in original_selection:
                if selected_obj.name in bpy.data.objects:
                    bpy.data.objects[selected_obj.name].select_set(True)
            if original_active and original_active.name in bpy.data.objects:
                context.view_layer.objects.active = bpy.data.objects[original_active.name]
        
        if applied_count > 0:
            self.report({'INFO'}, f"Applied transforms to {applied_count} objects.")
        else:
            self.report({'INFO'}, "All selected objects already have identity transforms.")
            
        return {'FINISHED'}

# --- Material Replacement Export Helper ---

def export_material_replacement(operator, context, obj, sublayer_stage, project_root, target_sublayer_path):
    """Exports material replacement for an existing mesh object."""
    if not obj or obj.type != 'MESH':
        return False

    print(f"\n--- Material Replacement for Mesh: {obj.name} ---")

    # Check if object has a stored USD prim path
    usd_prim_path = obj.get("usd_prim_path", None)
    if not usd_prim_path:
        operator.report({'WARNING'}, f"Object '{obj.name}' has no stored USD prim path. Cannot perform material replacement.")
        return False

    print(f"  Using stored USD prim path: {usd_prim_path}")

    # Get the material to export
    bl_mat = None
    if obj.material_slots:
        bl_mat = obj.material_slots[0].material
        if not bl_mat:
            operator.report({'WARNING'}, f"Object '{obj.name}' has no material to replace.")
            return False
    else:
        operator.report({'WARNING'}, f"Object '{obj.name}' has no material slots.")
        return False

    try:
        # Convert string path to Sdf.Path
        prim_path = Sdf.Path(usd_prim_path)
        
        # Export material using the stored prim path as parent
        material_path = export_material(bl_mat, sublayer_stage, project_root, target_sublayer_path, parent_mesh_path=prim_path, obj=obj)
        
        if material_path:
            print(f"  Successfully replaced material for {obj.name} at {prim_path}")
            # Update stored material path
            obj["remix_material_path"] = str(material_path)
            return True
        else:
            operator.report({'ERROR'}, f"Failed to export replacement material for {obj.name}")
            return False
            
    except Exception as e:
        operator.report({'ERROR'}, f"Error during material replacement for {obj.name}: {e}")
        import traceback
        traceback.print_exc()
        return False

# --- Core Export Logic Helper ---