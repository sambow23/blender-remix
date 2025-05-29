import os
try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Ar
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

if USD_AVAILABLE:
    from .constants import MATERIAL_TYPES

def extract_material_type(source_shader):
    """
    Extract the material type from a USD shader prim or definition.

    Args:
        source_shader: The UsdShade.Shader object or potentially the material prim itself.

    Returns:
        str: Material type key (e.g., "STANDARD", "EMISSIVE", "MDL") or "UNKNOWN".
    """
    if not USD_AVAILABLE or not source_shader:
        return "UNKNOWN"

    shader_prim = source_shader.GetPrim()
    if not shader_prim:
        return "UNKNOWN"

    # Get the shader ID/type name from the prim definition if possible
    shader_id = ""
    if shader_prim.GetPrimDefinition():
         shader_id = str(shader_prim.GetPrimDefinition().GetTypeName()) # More robust way

    # Fallback to GetTypeName if definition not available
    if not shader_id:
        shader_id = str(shader_prim.GetTypeName())

    shader_name = shader_prim.GetName()

    # Check for known Aperture types first based on shader_id or name
    for type_name, internal_type in MATERIAL_TYPES.items():
        if type_name in shader_id or type_name in shader_name:
            return internal_type

    # Generic checks if Aperture type wasn't explicit
    if "MDL" in shader_id or "Mdl" in shader_id:
        return "MDL" # Check for MDL specifically
    elif "Emissive" in shader_id or "emissive" in shader_name:
         return "EMISSIVE"
    elif source_shader.GetInput("emissiveColor") or source_shader.GetInput("emissive_texture"):
         # Check for emissive inputs even if not named emissive
         return "EMISSIVE"
    elif "SphereLight" in shader_id or "DiskLight" in shader_id or "CylinderLight" in shader_id:
        # Exclude lights being misinterpreted as materials
        return "LIGHT"
    elif shader_id == "Shader": # Generic USD shader type
        # Could be standard, check inputs later if needed
        return "STANDARD"
    else:
        # Default to standard if no other indicators match
        print(f"Warning: Unrecognized shader type '{shader_id}', defaulting to STANDARD for '{shader_name}'")
        return "STANDARD"


def resolve_material_references(material_prim, stage, material_map):
    """
    Resolve material references to their target material prims.

    Args:
        material_prim: Material prim to check for references
        stage: USD stage
        material_map: Dictionary mapping material paths to resolved material prims

    Returns:
        UsdShade.Material: Resolved material prim or the original prim.
    """
    if not USD_AVAILABLE:
        return material_prim # Return original if USD not available

    # Basic implementation.
    # A more complex implementation would trace references/inherits.
    # For Remix, materials are often defined directly or in separate .usda files,
    # so deep referencing might be less common than standard USD workflows.
    try:
        prim = material_prim.GetPrim()
        # Example check (needs refinement based on actual Remix structure):
        if hasattr(prim, 'GetReferences'):
            ref_list = prim.GetReferences()
            if ref_list and ref_list.GetNumReferences() > 0:
                # TODO: Implement actual reference resolution logic if needed
                ref_path = ref_list.GetReferences()[0].assetPath
                print(f"Note: Material '{prim.GetName()}' has references (e.g., to {ref_path}), but resolution logic is basic.")
                # Example: Try finding a material in the map based on ref_path name
                ref_name = os.path.splitext(os.path.basename(ref_path))[0]
                for path, mat in material_map.items():
                    if ref_name.lower() in path.lower():
                         print(f"  -> Tentatively resolved to {mat.GetPrim().GetName()}")
                         return mat # Return the first match found

    except Exception as e:
        print(f"Error checking material references for {material_prim.GetPath()}: {e}")

    # If no reference found or resolved, return the original
    return material_prim

def get_shader_from_material(material_prim):
    """
    Get the primary surface shader connected to a USD material prim.

    Args:
        material_prim: Usd.Prim representing the material.

    Returns:
        UsdShade.Shader: Shader object or None if not found.
    """
    if not USD_AVAILABLE or not material_prim or not material_prim.IsA(UsdShade.Material):
        return None

    material = UsdShade.Material(material_prim)
    if not material:
        return None

    # Standard USD way: Get the surface output and trace its connection
    surface_output = material.GetSurfaceOutput() # Use standard token
    if surface_output and surface_output.HasConnectedSource():
        source_info = surface_output.GetConnectedSource()
        if source_info:
            # source_info can be (shader_prim, output_name, type)
            # or for older USD versions, just the shader prim directly
            connected_prim = None
            if isinstance(source_info, tuple):
                 source_shader_prim_path, source_output_name, _ = source_info
                 connected_prim = material_prim.GetStage().GetPrimAtPath(source_shader_prim_path)
            elif isinstance(source_info, Usd.Prim): # Older USD versions might return prim directly
                 connected_prim = source_info
            elif isinstance(source_info, UsdShade.Shader): # Or even the shader object
                return source_info

            if connected_prim and connected_prim.IsA(UsdShade.Shader):
                return UsdShade.Shader(connected_prim)


    # Fallback: Look for a child Shader prim (common in simpler structures or exports)
    shader_names_to_check = ["Shader", "shader", "Surface", "surface", "PBRShader"]
    for name in shader_names_to_check:
        shader_prim_path = material_prim.GetPath().AppendChild(name)
        shader_prim = material_prim.GetStage().GetPrimAtPath(shader_prim_path)
        if shader_prim and shader_prim.IsA(UsdShade.Shader):
            print(f"Found shader '{name}' as child of material '{material_prim.GetName()}'")
            return UsdShade.Shader(shader_prim)

    # Fallback: Iterate through all children that are shaders
    for child in material_prim.GetChildren():
        if child.IsA(UsdShade.Shader):
            print(f"Found shader '{child.GetName()}' as child of material '{material_prim.GetName()}' (generic search)")
            return UsdShade.Shader(child)


    print(f"WARNING: Could not find a surface shader for material: {material_prim.GetPath()}")
    return None


def get_input_value(shader, input_name):
    """
    Get the value or connected texture path for a shader input.

    Args:
        shader: UsdShade.Shader object.
        input_name: Name of the input attribute (e.g., "diffuseColor", "file").

    Returns:
        The input's value (could be float, color tuple, texture path string, etc.) or None.
    """
    if not USD_AVAILABLE or not shader:
        return None

    # Try getting input without prefix first
    shader_input = shader.GetInput(input_name)
    if not shader_input:
        # Fallback: Try with 'inputs:' prefix
        shader_input = shader.GetInput(f"inputs:{input_name}")
        if not shader_input:
             # print(f"Debug: Input '{input_name}' (with or without prefix) not found on shader '{shader.GetPrim().GetName()}'")
             return None

    # Get the underlying attribute
    attr = shader_input.GetAttr()
    if not attr:
        # This shouldn't happen if GetInput succeeded, but check anyway
        print(f"  Warning: Could not get attribute for input '{input_name}'")
        return None

    # Prioritize getting the direct value if it exists
    value = None
    if attr.HasValue(): # Check on the attribute
        value = attr.Get() # Get from the attribute

    # If a value was found directly, check if it looks like a texture path
    if value is not None:
        # Check if it's an AssetPath or a string that might be a path
        if isinstance(value, Sdf.AssetPath):
            print(f"  Found direct AssetPath value for '{input_name}': {value}")
            return value
        elif isinstance(value, str):
            # Basic check for typical path characters or extensions
            if '/' in value or '\\' in value or any(value.lower().endswith(ext) for ext in ['.dds', '.png', '.jpg', '.tga']):
                print(f"  Found direct string value resembling path for '{input_name}': {value}")
                return value
            # Else, it's probably just a string constant, fall through to return it later

    # Check if the input is connected to another prim (e.g., a texture node)
    if shader_input.HasConnectedSource():
        print(f"  Input '{input_name}' has connected source.") # LOGGING
        source_info = shader_input.GetConnectedSource()
        if source_info:
            # source_info = (source_prim_path, output_name, type)
            source_prim_path, source_output_name, _ = source_info
            source_prim = shader.GetPrim().GetStage().GetPrimAtPath(source_prim_path)

            if source_prim:
                # If connected to a Texture prim, get its 'file' input
                if source_prim.GetTypeName() in ["Texture", "UsdUVTexture"]:
                    file_input = source_prim.GetAttribute("inputs:file")
                    if file_input and file_input.HasValue():
                        asset_path = file_input.Get()
                        print(f"    Found connected texture file: {asset_path}") # LOGGING
                        return asset_path

                # If connected to another Shader, we might need to trace further,
                print(f"Input '{input_name}' is connected to another shader/prim '{source_prim.GetName()}', not directly resolvable to value/texture.")
                # Return None here, as we prioritize direct value or UsdUVTexture connection
                return None

    # If not connected, and we already retrieved a direct value, return it
    if value is not None:
        print(f"  Returning direct value for '{input_name}': {value}")
        return value

    # print(f"Debug: Input '{input_name}' on shader '{shader.GetPrim().GetName()}' has no connection and no direct value.")
    return None 