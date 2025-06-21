import bpy
import os
import math
import hashlib
import json
try:
    from pxr import Usd, UsdShade, Sdf, Gf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

if USD_AVAILABLE:
    from .usd_utils import get_shader_from_material, get_input_value
    from .texture_utils import load_texture, resolve_material_asset_path
    from . import constants
    from .core_utils import set_material_blend_method_compatible

# Cache for Blender materials to avoid redundant creation
# Key: unique identifier (e.g., base_path + metadata_hash)
# Value: bpy.types.Material
_material_cache = {}

# Global material cache for cross-import reuse
# Key: (usd_material_path, texture_context_hash)
# Value: bpy.types.Material
_global_material_cache = {}

def clear_material_cache():
    """Clear the global material cache."""
    global _material_cache, _global_material_cache
    _material_cache.clear()
    _global_material_cache.clear()

def _generate_material_cache_key(usd_material_path, usd_file_path_context):
    """Generate a cache key for materials based on USD path and texture context."""
    import hashlib
    
    # Create a hash of the texture context directory to handle different capture folders
    context_dir = os.path.dirname(usd_file_path_context) if usd_file_path_context else ""
    context_hash = hashlib.md5(context_dir.encode('utf-8')).hexdigest()[:8]
    
    return f"{usd_material_path}#{context_hash}"

# --- Custom Node Group Handling ---
APERTURE_OPAQUE_NODE_GROUP_NAME = "Aperture Opaque"
APERTURE_TRANSLUCENT_NODE_GROUP_NAME = "Aperture Translucent"

def set_principled_transmission(node, value):
    """Set transmission value on Principled BSDF, handling different Blender versions."""
    if 'Transmission Weight' in node.inputs:
        node.inputs['Transmission Weight'].default_value = value
    elif 'Transmission' in node.inputs:
        node.inputs['Transmission'].default_value = value
    else:
        print(f"Warning: No transmission input found on {node.name}")

def get_principled_transmission(node):
    """Get transmission value from Principled BSDF, handling different Blender versions."""
    if 'Transmission Weight' in node.inputs:
        return node.inputs['Transmission Weight']
    elif 'Transmission' in node.inputs:
        return node.inputs['Transmission']
    return None

def create_aperture_opaque_node_group():
    """
    Creates the 'Aperture Opaque' node group programmatically.
    Returns the node group.
    """
    if APERTURE_OPAQUE_NODE_GROUP_NAME in bpy.data.node_groups:
        print(f"Node group '{APERTURE_OPAQUE_NODE_GROUP_NAME}' already exists.")
        return bpy.data.node_groups[APERTURE_OPAQUE_NODE_GROUP_NAME]

    # Create new node group
    node_group = bpy.data.node_groups.new(name=APERTURE_OPAQUE_NODE_GROUP_NAME, type='ShaderNodeTree')
    
    # Create input and output nodes
    group_inputs = node_group.nodes.new('NodeGroupInput')
    group_outputs = node_group.nodes.new('NodeGroupOutput')
    group_inputs.location = (-600, 0)
    group_outputs.location = (600, 0)
    
    # For opaque materials, we'll create a simplified node group with basic PBR inputs
    # This can be expanded later based on the actual AperturePBR_Opaque specification
    input_sockets = [
        ('NodeSocketColor', 'Base Color', (0.8, 0.8, 0.8, 1.0)),
        ('NodeSocketFloat', 'Metallic', 0.0, 0.0, 1.0),
        ('NodeSocketFloat', 'Roughness', 0.5, 0.0, 1.0),
        ('NodeSocketFloat', 'IOR', 1.45, 1.0, 3.0),
        ('NodeSocketColor', 'Emissive Color', (0.0, 0.0, 0.0, 1.0)),
        ('NodeSocketFloat', 'Emissive Intensity', 1.0, 0.0, 65504.0),
        ('NodeSocketVector', 'Normal Map', (0.0, 0.0, 1.0))
    ]
    
    # Create input sockets (compatible with different Blender versions)
    for socket_data in input_sockets:
        if len(socket_data) == 3:  # Color or Vector socket
            socket_type, socket_name, default_value = socket_data
            try:
                socket = node_group.interface.new_socket(name=socket_name, in_out='INPUT', socket_type=socket_type)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
            except AttributeError:
                socket = node_group.inputs.new(socket_type, socket_name)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
        elif len(socket_data) == 5:  # Float socket with min/max
            socket_type, socket_name, default_value, min_val, max_val = socket_data
            try:
                socket = node_group.interface.new_socket(name=socket_name, in_out='INPUT', socket_type=socket_type)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
                if hasattr(socket, 'min_value'):
                    socket.min_value = min_val
                if hasattr(socket, 'max_value'):
                    socket.max_value = max_val
            except AttributeError:
                socket = node_group.inputs.new(socket_type, socket_name)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
                if hasattr(socket, 'min_value'):
                    socket.min_value = min_val
                if hasattr(socket, 'max_value'):
                    socket.max_value = max_val
    
    # Create output sockets
    try:
        node_group.interface.new_socket(name='BSDF', in_out='OUTPUT', socket_type='NodeSocketShader')
        node_group.interface.new_socket(name='Displacement', in_out='OUTPUT', socket_type='NodeSocketVector')
    except AttributeError:
        node_group.outputs.new('NodeSocketShader', 'BSDF')
        node_group.outputs.new('NodeSocketVector', 'Displacement')
    
    # Create nodes for the shader network
    nodes = node_group.nodes
    links = node_group.links
    
    # Main Principled BSDF
    main_bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    main_bsdf.location = (0, 0)
    main_bsdf.name = "Main_BSDF"
    
    # Normal map node
    normal_map = nodes.new(type='ShaderNodeNormalMap')
    normal_map.location = (-300, -200)
    
    # Emission shader
    emission = nodes.new(type='ShaderNodeEmission')
    emission.location = (0, -300)
    
    # Math node for emission intensity
    emission_mult = nodes.new(type='ShaderNodeMath')
    emission_mult.location = (-200, -300)
    emission_mult.operation = 'MULTIPLY'
    
    # Mix shader for emission
    mix_emission = nodes.new(type='ShaderNodeMixShader')
    mix_emission.location = (300, 0)
    mix_emission.name = "Mix_Emission"
    
    # Math node to check if emission is enabled (intensity > 0)
    emission_check = nodes.new(type='ShaderNodeMath')
    emission_check.location = (-200, -400)
    emission_check.operation = 'GREATER_THAN'
    emission_check.inputs[1].default_value = 0.0
    
    # Value node for displacement (constant zero)
    displacement_value = nodes.new(type='ShaderNodeValue')
    displacement_value.location = (300, -200)
    displacement_value.outputs[0].default_value = 0.0
    
    # Create links - Input connections
    links.new(group_inputs.outputs['Base Color'], main_bsdf.inputs['Base Color'])
    links.new(group_inputs.outputs['Metallic'], main_bsdf.inputs['Metallic'])
    links.new(group_inputs.outputs['Roughness'], main_bsdf.inputs['Roughness'])
    links.new(group_inputs.outputs['IOR'], main_bsdf.inputs['IOR'])
    links.new(group_inputs.outputs['Normal Map'], normal_map.inputs['Color'])
    links.new(group_inputs.outputs['Emissive Color'], emission.inputs['Color'])
    links.new(group_inputs.outputs['Emissive Intensity'], emission_mult.inputs[0])
    links.new(group_inputs.outputs['Emissive Intensity'], emission_check.inputs[0])
    
    # Internal node connections
    links.new(normal_map.outputs['Normal'], main_bsdf.inputs['Normal'])
    links.new(emission_mult.outputs['Value'], emission.inputs['Strength'])
    links.new(emission_check.outputs['Value'], mix_emission.inputs['Fac'])
    links.new(main_bsdf.outputs['BSDF'], mix_emission.inputs[1])  # Shader input 1
    links.new(emission.outputs['Emission'], mix_emission.inputs[2])  # Shader input 2
    
    # Set a constant 1.0 for the emission multiplier second input
    emission_mult.inputs[1].default_value = 1.0
    
    # Output connections
    links.new(mix_emission.outputs['Shader'], group_outputs.inputs['BSDF'])
    links.new(displacement_value.outputs['Value'], group_outputs.inputs['Displacement'])
    
    print(f"Successfully created node group: {APERTURE_OPAQUE_NODE_GROUP_NAME}")
    return node_group


def append_aperture_opaque_node_group():
    """
    Appends the 'Aperture Opaque' node group from the addon's .blend file
    if it doesn't already exist in the current Blender data.
    If the .blend file doesn't exist, creates the node group programmatically.
    Returns the node group.
    """
    if APERTURE_OPAQUE_NODE_GROUP_NAME in bpy.data.node_groups:
        print(f"Node group '{APERTURE_OPAQUE_NODE_GROUP_NAME}' already exists.")
        return bpy.data.node_groups[APERTURE_OPAQUE_NODE_GROUP_NAME]

    # First try to load from .blend file
    blend_file_path = os.path.join(constants.ADDON_DIR, "nodes", "ApertureOpaque.blend")

    if os.path.exists(blend_file_path):
        try:
            with bpy.data.libraries.load(blend_file_path, link=False) as (data_from, data_to):
                if APERTURE_OPAQUE_NODE_GROUP_NAME in data_from.node_groups:
                    data_to.node_groups = [APERTURE_OPAQUE_NODE_GROUP_NAME]
                    print(f"Successfully appended node group from file: {APERTURE_OPAQUE_NODE_GROUP_NAME}")
                    return bpy.data.node_groups.get(APERTURE_OPAQUE_NODE_GROUP_NAME)
                else:
                    print(f"WARNING: Node group '{APERTURE_OPAQUE_NODE_GROUP_NAME}' not found in {blend_file_path}")
        except Exception as e:
            print(f"WARNING: Failed to load node group from {blend_file_path}: {e}")
    else:
        print(f"INFO: ApertureOpaque.blend not found at {blend_file_path}")

    # Fallback to programmatic creation
    print("Creating Aperture Opaque node group programmatically...")
    return create_aperture_opaque_node_group()


def create_aperture_translucent_node_group():
    """
    Creates the 'Aperture Translucent' node group programmatically.
    Returns the node group.
    """
    if APERTURE_TRANSLUCENT_NODE_GROUP_NAME in bpy.data.node_groups:
        print(f"Node group '{APERTURE_TRANSLUCENT_NODE_GROUP_NAME}' already exists.")
        return bpy.data.node_groups[APERTURE_TRANSLUCENT_NODE_GROUP_NAME]

    # Create new node group
    node_group = bpy.data.node_groups.new(name=APERTURE_TRANSLUCENT_NODE_GROUP_NAME, type='ShaderNodeTree')
    
    # Create input and output nodes
    group_inputs = node_group.nodes.new('NodeGroupInput')
    group_outputs = node_group.nodes.new('NodeGroupOutput')
    group_inputs.location = (-800, 0)
    group_outputs.location = (800, 0)
    
    # Define input sockets with their types and default values
    # Note: Using float sockets with 0.0/1.0 range for boolean-like behavior since NodeSocketBool isn't available in all Blender versions
    # The boolean-like inputs are processed through ROUND math nodes to snap to clean 0.0/1.0 values
    input_sockets = [
        ('NodeSocketColor', 'Transmittance/Diffuse Albedo', (0.97, 0.97, 0.97, 1.0)),
        ('NodeSocketFloat', 'IOR', 1.3, 1.0, 3.0),
        ('NodeSocketFloat', 'Thin Walled', 0.0, 0.0, 1.0),  # Boolean as float
        ('NodeSocketFloat', 'Thin Wall Thickness', 1.0, 0.001, 65504.0),
        ('NodeSocketFloat', 'Use Diffuse Layer', 0.0, 0.0, 1.0),  # Boolean as float
        ('NodeSocketFloat', 'Transmittance Measurement Distance', 1.0, 0.001, 65504.0),
        ('NodeSocketFloat', 'Enable Emission', 0.0, 0.0, 1.0),  # Boolean as float
        ('NodeSocketColor', 'Emissive Color', (1.0, 0.1, 0.1, 1.0)),
        ('NodeSocketFloat', 'Emissive Intensity', 40.0, 0.0, 65504.0),
        ('NodeSocketVector', 'Normal Map', (0.0, 0.0, 1.0))
    ]
    
    # Create input sockets (compatible with different Blender versions)
    for socket_data in input_sockets:
        if len(socket_data) == 3:  # Color or Vector socket
            socket_type, socket_name, default_value = socket_data
            try:
                # Try new Blender 4.0+ interface
                socket = node_group.interface.new_socket(name=socket_name, in_out='INPUT', socket_type=socket_type)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
            except AttributeError:
                # Fallback for older Blender versions
                socket = node_group.inputs.new(socket_type, socket_name)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
        elif len(socket_data) == 5:  # Float socket with min/max
            socket_type, socket_name, default_value, min_val, max_val = socket_data
            try:
                # Try new Blender 4.0+ interface
                socket = node_group.interface.new_socket(name=socket_name, in_out='INPUT', socket_type=socket_type)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
                if hasattr(socket, 'min_value'):
                    socket.min_value = min_val
                if hasattr(socket, 'max_value'):
                    socket.max_value = max_val
                    
                # For boolean-like floats (0.0-1.0 range), set step size for better UI
                is_boolean_like = (socket_name in ['Thin Walled', 'Use Diffuse Layer', 'Enable Emission'] and 
                                 min_val == 0.0 and max_val == 1.0)
                if is_boolean_like and hasattr(socket, 'step'):
                    socket.step = 100  # Makes it snap to 0.0 or 1.0
            except AttributeError:
                # Fallback for older Blender versions
                socket = node_group.inputs.new(socket_type, socket_name)
                if hasattr(socket, 'default_value'):
                    socket.default_value = default_value
                if hasattr(socket, 'min_value'):
                    socket.min_value = min_val
                if hasattr(socket, 'max_value'):
                    socket.max_value = max_val
                    
                # For boolean-like floats, set step size for better UI
                is_boolean_like = (socket_name in ['Thin Walled', 'Use Diffuse Layer', 'Enable Emission'] and 
                                 min_val == 0.0 and max_val == 1.0)
                if is_boolean_like and hasattr(socket, 'step'):
                    socket.step = 100
    
    # Create output sockets (compatible with different Blender versions)
    try:
        # Try new Blender 4.0+ interface
        node_group.interface.new_socket(name='BSDF', in_out='OUTPUT', socket_type='NodeSocketShader')
        node_group.interface.new_socket(name='Displacement', in_out='OUTPUT', socket_type='NodeSocketVector')
    except AttributeError:
        # Fallback for older Blender versions
        node_group.outputs.new('NodeSocketShader', 'BSDF')
        node_group.outputs.new('NodeSocketVector', 'Displacement')
    
    # Create nodes for the shader network
    nodes = node_group.nodes
    links = node_group.links
    
    # Main transmission BSDF
    main_bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    main_bsdf.location = (0, 200)
    main_bsdf.name = "Main_Transmission_BSDF"
    
    # Set transmission for translucent behavior
    set_principled_transmission(main_bsdf, 1.0)
    
    main_bsdf.inputs['Roughness'].default_value = 0.0
    
    # Diffuse layer BSDF
    diffuse_bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    diffuse_bsdf.location = (0, -200)
    diffuse_bsdf.name = "Diffuse_Layer_BSDF"
    
    # Set transmission for opaque diffuse layer
    set_principled_transmission(diffuse_bsdf, 0.0)
    
    # Normal map node
    normal_map = nodes.new(type='ShaderNodeNormalMap')
    normal_map.location = (-300, 0)
    
    # Mix shader for diffuse/transmission
    mix_diffuse = nodes.new(type='ShaderNodeMixShader')
    mix_diffuse.location = (300, 0)
    mix_diffuse.name = "Mix_Diffuse_Transmission"
    
    # Emission shader
    emission = nodes.new(type='ShaderNodeEmission')
    emission.location = (0, -400)
    
    # Math node for emission intensity control
    emission_mult = nodes.new(type='ShaderNodeMath')
    emission_mult.location = (-200, -400)
    emission_mult.operation = 'MULTIPLY'
    
    # Math nodes to snap boolean-like inputs to 0.0 or 1.0
    thin_walled_snap = nodes.new(type='ShaderNodeMath')
    thin_walled_snap.location = (-500, 300)
    thin_walled_snap.operation = 'ROUND'
    
    use_diffuse_snap = nodes.new(type='ShaderNodeMath')
    use_diffuse_snap.location = (-500, 100)
    use_diffuse_snap.operation = 'ROUND'
    
    enable_emission_snap = nodes.new(type='ShaderNodeMath')
    enable_emission_snap.location = (-500, -300)
    enable_emission_snap.operation = 'ROUND'
    
    # Mix shader for final emission
    mix_emission = nodes.new(type='ShaderNodeMixShader')
    mix_emission.location = (500, 0)
    mix_emission.name = "Mix_Final_Emission"
    
    # Value node for displacement (constant zero)
    displacement_value = nodes.new(type='ShaderNodeValue')
    displacement_value.location = (500, -200)
    displacement_value.outputs[0].default_value = 0.0
    
    # Create links - Input connections
    links.new(group_inputs.outputs['Transmittance/Diffuse Albedo'], main_bsdf.inputs['Base Color'])
    links.new(group_inputs.outputs['Transmittance/Diffuse Albedo'], diffuse_bsdf.inputs['Base Color'])
    links.new(group_inputs.outputs['IOR'], main_bsdf.inputs['IOR'])
    links.new(group_inputs.outputs['IOR'], diffuse_bsdf.inputs['IOR'])
    links.new(group_inputs.outputs['Normal Map'], normal_map.inputs['Color'])
    links.new(group_inputs.outputs['Emissive Color'], emission.inputs['Color'])
    links.new(group_inputs.outputs['Emissive Intensity'], emission_mult.inputs[0])
    
    # Connect boolean-like inputs through snap nodes for cleaner 0/1 behavior
    links.new(group_inputs.outputs['Thin Walled'], thin_walled_snap.inputs[0])
    links.new(group_inputs.outputs['Use Diffuse Layer'], use_diffuse_snap.inputs[0])
    links.new(group_inputs.outputs['Enable Emission'], enable_emission_snap.inputs[0])
    
    # Connect snapped boolean outputs to their destinations
    links.new(use_diffuse_snap.outputs['Value'], mix_diffuse.inputs['Fac'])
    links.new(enable_emission_snap.outputs['Value'], emission_mult.inputs[1])
    links.new(enable_emission_snap.outputs['Value'], mix_emission.inputs['Fac'])
    
    # Internal node connections
    links.new(normal_map.outputs['Normal'], main_bsdf.inputs['Normal'])
    links.new(normal_map.outputs['Normal'], diffuse_bsdf.inputs['Normal'])
    links.new(emission_mult.outputs['Value'], emission.inputs['Strength'])
    links.new(main_bsdf.outputs['BSDF'], mix_diffuse.inputs[1])  # Shader input 1
    links.new(diffuse_bsdf.outputs['BSDF'], mix_diffuse.inputs[2])  # Shader input 2
    links.new(mix_diffuse.outputs['Shader'], mix_emission.inputs[1])  # Shader input 1
    links.new(emission.outputs['Emission'], mix_emission.inputs[2])  # Shader input 2
    
    # Output connections
    links.new(mix_emission.outputs['Shader'], group_outputs.inputs['BSDF'])
    links.new(displacement_value.outputs['Value'], group_outputs.inputs['Displacement'])
    
    print(f"Successfully created node group: {APERTURE_TRANSLUCENT_NODE_GROUP_NAME}")
    return node_group


def append_aperture_translucent_node_group():
    """
    Appends the 'Aperture Translucent' node group from the addon's .blend file
    if it doesn't already exist in the current Blender data.
    If the .blend file doesn't exist, creates the node group programmatically.
    Returns the node group.
    """
    if APERTURE_TRANSLUCENT_NODE_GROUP_NAME in bpy.data.node_groups:
        print(f"Node group '{APERTURE_TRANSLUCENT_NODE_GROUP_NAME}' already exists.")
        return bpy.data.node_groups[APERTURE_TRANSLUCENT_NODE_GROUP_NAME]

    # First try to load from .blend file
    blend_file_path = os.path.join(constants.ADDON_DIR, "nodes", "ApertureTranslucent.blend")

    if os.path.exists(blend_file_path):
        try:
            with bpy.data.libraries.load(blend_file_path, link=False) as (data_from, data_to):
                if APERTURE_TRANSLUCENT_NODE_GROUP_NAME in data_from.node_groups:
                    data_to.node_groups = [APERTURE_TRANSLUCENT_NODE_GROUP_NAME]
                    print(f"Successfully appended node group from file: {APERTURE_TRANSLUCENT_NODE_GROUP_NAME}")
                    return bpy.data.node_groups.get(APERTURE_TRANSLUCENT_NODE_GROUP_NAME)
                else:
                    print(f"WARNING: Node group '{APERTURE_TRANSLUCENT_NODE_GROUP_NAME}' not found in {blend_file_path}")
        except Exception as e:
            print(f"WARNING: Failed to load node group from {blend_file_path}: {e}")
    else:
        print(f"INFO: ApertureTranslucent.blend not found at {blend_file_path}")

    # Fallback to programmatic creation
    print("Creating Aperture Translucent node group programmatically...")
    return create_aperture_translucent_node_group()


# Modified default Blender material creation function
def create_default_blender_material(name):
    """Creates a Blender material using the custom 'Aperture Opaque' node group."""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (300, 0) # Output to the right

    aperture_node_group = append_aperture_opaque_node_group()
    if not aperture_node_group:
        print(f"ERROR: Could not append or find '{APERTURE_OPAQUE_NODE_GROUP_NAME}'. Creating a fallback Principled BSDF.")
        # Fallback to Principled BSDF if custom node group fails
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])
        return mat, bsdf # Return material and the BSDF node

    # Add an instance of the custom node group
    group_node = nodes.new(type='ShaderNodeGroup')
    group_node.node_tree = aperture_node_group
    group_node.name = APERTURE_OPAQUE_NODE_GROUP_NAME
    group_node.location = (0, 0)

    # Connect the group node's outputs to the material output
    if 'BSDF' in group_node.outputs:
        links.new(group_node.outputs['BSDF'], output_node.inputs['Surface'])
    else:
        print(f"WARNING: Output 'BSDF' not found in '{APERTURE_OPAQUE_NODE_GROUP_NAME}' node group.")

    if 'Displacement' in group_node.outputs:
        links.new(group_node.outputs['Displacement'], output_node.inputs['Displacement'])
    else:
        print(f"WARNING: Output 'Displacement' not found in '{APERTURE_OPAQUE_NODE_GROUP_NAME}' node group.")
        # If no displacement output, connect a zero vector or leave it disconnected.
        # For now, we'll leave it disconnected.

    return mat, group_node # Return material and the group node instance


def create_translucent_blender_material(name):
    """Creates a Blender material using the custom 'Aperture Translucent' node group."""
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (300, 0)

    aperture_node_group = append_aperture_translucent_node_group()
    if not aperture_node_group:
        print(f"ERROR: Could not append or find '{APERTURE_TRANSLUCENT_NODE_GROUP_NAME}'. Creating a fallback Principled BSDF.")
        # Fallback to Principled BSDF with transmission settings
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        # Set up for transmission
        set_principled_transmission(bsdf, 1.0)
        bsdf.inputs['IOR'].default_value = 1.3
        links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])
        return mat, bsdf

    # Add an instance of the custom node group
    group_node = nodes.new(type='ShaderNodeGroup')
    group_node.node_tree = aperture_node_group
    group_node.name = APERTURE_TRANSLUCENT_NODE_GROUP_NAME
    group_node.location = (0, 0)

    # Connect the group node's outputs to the material output
    if 'BSDF' in group_node.outputs:
        links.new(group_node.outputs['BSDF'], output_node.inputs['Surface'])
    else:
        print(f"WARNING: Output 'BSDF' not found in '{APERTURE_TRANSLUCENT_NODE_GROUP_NAME}' node group.")

    if 'Displacement' in group_node.outputs:
        links.new(group_node.outputs['Displacement'], output_node.inputs['Displacement'])
    else:
        print(f"WARNING: Output 'Displacement' not found in '{APERTURE_TRANSLUCENT_NODE_GROUP_NAME}' node group.")

    return mat, group_node


# Create material from USD path
def create_material(usd_material_path, usd_stage, usd_file_path_context):
    """
    Creates a Blender material from a USD material path using a node setup.

    Args:
        usd_material_path: Path string to the material prim in the USD stage.
        usd_stage: The Usd.Stage containing the material.
        usd_file_path_context: Absolute path to the main imported USD file for resolving relative paths.

    Returns:
        bpy.types.Material: Created or existing Blender material, or None on failure.
    """
    if not USD_AVAILABLE:
        print("USD libraries not available, cannot create materials.")
        return None

    print(f"Processing material path: {usd_material_path}")
    
    # Generate cache key for this material + context combination
    cache_key = _generate_material_cache_key(usd_material_path, usd_file_path_context)
    
    # Check global cache first for cross-import reuse
    if cache_key in _global_material_cache:
        cached_material = _global_material_cache[cache_key]
        if cached_material and cached_material.name in bpy.data.materials:
            print(f"Reusing cached material: {cached_material.name} (key: {cache_key})")
            return cached_material
        else:
            # Remove invalid cache entry
            del _global_material_cache[cache_key]
    
    material_prim = usd_stage.GetPrimAtPath(usd_material_path)

    if not material_prim or not material_prim.IsA(UsdShade.Material):
        print(f"WARNING: Material prim not found or invalid at path: {usd_material_path}")
        error_mat_name = f"ERROR_{os.path.basename(usd_material_path)}"
        
        # Check if error material already exists
        if error_mat_name in bpy.data.materials:
            return bpy.data.materials[error_mat_name]
        
        error_mat, bsdf_node = create_default_blender_material(error_mat_name)
        if bsdf_node and bsdf_node.type == 'BSDF_PRINCIPLED':
            bsdf_node.inputs['Base Color'].default_value = (1.0, 0.0, 0.0, 1.0) # Red
        return error_mat

    # Use a Blender-safe name based on the prim name
    material_name = bpy.path.clean_name(material_prim.GetName())
    if not material_name:
        material_name = bpy.path.clean_name(os.path.basename(usd_material_path))

    # Generate unique material name to avoid conflicts across different contexts
    context_suffix = cache_key.split('#')[1] if '#' in cache_key else "default"
    unique_material_name = f"{material_name}_{context_suffix}"
    
    # Check if this specific material already exists
    if unique_material_name in bpy.data.materials:
        existing_material = bpy.data.materials[unique_material_name]
        print(f"Material '{unique_material_name}' already exists, reusing.")
        _global_material_cache[cache_key] = existing_material
        return existing_material

    # Find the actual shader connected to the material surface
    surface_shader = get_shader_from_material(material_prim)
    if not surface_shader:
        print(f"WARNING: No surface shader found for material: {unique_material_name}. Using default Principled BSDF.")
        bl_material, main_shader_node = create_default_blender_material(unique_material_name)
        _global_material_cache[cache_key] = bl_material
        return bl_material # Return the default material

    shader_prim = surface_shader.GetPrim()
    print(f"Found shader '{shader_prim.GetName()}' (type: {shader_prim.GetTypeName()}) for material '{unique_material_name}'")
    
    # Determine material type based on shader MDL source asset
    is_translucent = False
    mdl_source_asset = get_input_value(surface_shader, "info:mdl:sourceAsset")
    if mdl_source_asset:
        mdl_asset_str = str(mdl_source_asset)
        print(f"Detected MDL source asset: {mdl_asset_str}")
        if "AperturePBR_Translucent" in mdl_asset_str:
            is_translucent = True
            print("Material identified as AperturePBR_Translucent")
    
    # Create appropriate Blender material based on type
    if is_translucent:
        bl_material, main_shader_node = create_translucent_blender_material(unique_material_name)
    else:
        bl_material, main_shader_node = create_default_blender_material(unique_material_name)
    
    nodes = bl_material.node_tree.nodes

    if not main_shader_node: # Check if main_shader_node (group or fallback BSDF) was created
        print(f"ERROR: Could not create main shader node in new material '{unique_material_name}'.")
        return bl_material # Return the basic material

    # --- DEBUG: Print shader inputs --- #
    print(f"      Available inputs on {shader_prim.GetPath()}:")
    for shader_input in surface_shader.GetInputs():
        print(f"        - {shader_input.GetBaseName()}")
    # --- END DEBUG --- #

    # --- Processing ---
    # Directly process using a standardized PBR approach for all shaders
    process_pbr(surface_shader, bl_material, main_shader_node, usd_file_path_context)
    # -- Processing ---

    # Cache the created material
    _global_material_cache[cache_key] = bl_material
    print(f"Successfully processed and cached material: {unique_material_name} (key: {cache_key})")
    return bl_material


# Input Processor
def process_input(usd_input_value, input_type, nodes, links, target_node, target_socket_name,
                  usd_file_path_context, node_pos=(-400, 0), is_normal=False, is_non_color=False):
    """
    Processes a USD input value and connects it to a Blender node socket with simple layout.
    Places textures to the left of the target node.
    """
    if usd_input_value is None:
        return None # No value to process

    print(f"      Processing input '{input_type}' with value: {usd_input_value}") # LOGGING
    target_socket = target_node.inputs.get(target_socket_name)
    if not target_socket:
        print(f"ERROR: Target socket '{target_socket_name}' not found on node '{target_node.name}'.")
        return None

    created_node = None
    texture_node_x = target_node.location.x - 400 # X position for texture nodes
    normal_map_node_x = target_node.location.x - 150 # X position for normal map node

    # Check if the value is a texture path
    # Relaxed check: assume string value starting with '../' or containing 'assets/' is a texture path
    is_likely_path = False
    if isinstance(usd_input_value, (str, Sdf.AssetPath)):
        path_str = str(usd_input_value)
        # Example USD path: @../assets/models/....dds@ -> remove @ symbols
        if path_str.startswith('@') and path_str.endswith('@'):
            path_str = path_str[1:-1]

        is_likely_path = '../' in path_str or 'assets/' in path_str or \
                         any(path_str.lower().endswith(ext) for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tiff'])

    print(f"        is_likely_path check result: {is_likely_path}") # LOGGING

    if is_likely_path:
        texture_path = str(usd_input_value)
        # Remove potential wrapper characters like '@' if present
        if texture_path.startswith('@') and texture_path.endswith('@'):
            texture_path = texture_path[1:-1]

        print(f"  Processing '{input_type}' as texture: {texture_path}")
        resolved_path = resolve_material_asset_path(texture_path, usd_file_path_context)

        if resolved_path and os.path.exists(resolved_path):
            # Use existing texture loading function
            image = load_texture(resolved_path, is_normal=is_normal, is_non_color=is_non_color)
            if image:
                tex_node = nodes.new(type='ShaderNodeTexImage')
                tex_node.image = image
                tex_node.label = f"{input_type.replace('_', ' ').title()} Texture"
                # Position texture node to the left, using the provided y offset
                tex_node.location = (texture_node_x, node_pos[1])

                output_socket_name = 'Color'
                if is_non_color or is_normal:
                    # Ensure correct color space for non-color data
                    image.colorspace_settings.name = 'Non-Color'
                    # Prefer Alpha output for single channel data if target socket expects VALUE
                    if 'Alpha' in tex_node.outputs and target_socket.type == 'VALUE':
                        output_socket_name = 'Alpha'

                # Special handling for Normal Maps
                if is_normal:
                    normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                    normal_map_node.location = (normal_map_node_x, node_pos[1])
                    # Connect Texture -> Normal Map Node -> Target Socket
                    links.new(tex_node.outputs[output_socket_name], normal_map_node.inputs['Color'])
                    links.new(normal_map_node.outputs['Normal'], target_socket)
                    created_node = normal_map_node # Return the normal map node
                else:
                    # Direct connection for other textures
                    links.new(tex_node.outputs[output_socket_name], target_socket)
                    created_node = tex_node # Return the image texture node
            else:
                print(f"  Warning: Failed to load texture for '{input_type}' from resolved path: {resolved_path}")
        else:
            print(f"  Warning: Texture path not found or invalid for '{input_type}': {resolved_path} (Original: {texture_path})")

    # Handle constant values (Color, Float, Int, Bool)
    elif isinstance(usd_input_value, Gf.Vec3f) and target_socket.type == 'RGBA':
        color = usd_input_value
        target_socket.default_value = (color[0], color[1], color[2], 1.0)
        print(f"  Set '{target_socket_name}' to color value: {target_socket.default_value[:3]}")
    elif isinstance(usd_input_value, Gf.Vec4f) and target_socket.type == 'RGBA':
         color = usd_input_value
         target_socket.default_value = tuple(color)
         print(f"  Set '{target_socket_name}' to color value: {target_socket.default_value}")
    elif isinstance(usd_input_value, (int, float)) and target_socket.type == 'VALUE':
        target_socket.default_value = float(usd_input_value)
        print(f"  Set '{target_socket_name}' to scalar value: {target_socket.default_value}")
    elif isinstance(usd_input_value, bool) and target_socket.type == 'VALUE':
        target_socket.default_value = 1.0 if usd_input_value else 0.0
        print(f"  Set '{target_socket_name}' to boolean value: {target_socket.default_value}")
    else:
        # Type mismatch or unhandled type - attempt conversion for basic types if possible
        try:
            if target_socket.type == 'VALUE' and isinstance(usd_input_value, (int, float, bool)):
                 target_socket.default_value = float(usd_input_value)
                 print(f"  Set '{target_socket_name}' to converted scalar value: {target_socket.default_value}")
            # Add other potential conversions if needed
            else:
                 print(f"  Notice: Input '{input_type}' has value '{usd_input_value}' (type: {type(usd_input_value)}), "
                       f"but target socket '{target_socket_name}' expects type '{target_socket.type}'. Skipping direct set.")
        except Exception as e:
             print(f"  Notice: Could not convert input '{input_type}' value '{usd_input_value}' for socket '{target_socket_name}'. Error: {e}. Skipping.")


    return created_node # Return the image/normal node if created


# PBR Processor
def process_pbr(shader, bl_material, shader_node, usd_file_path_context):
    """Processes common PBR inputs."""
    nodes = bl_material.node_tree.nodes
    links = bl_material.node_tree.links
    print(f"    Processing PBR inputs for shader: {shader.GetPath()} onto node: {shader_node.name}") # LOGGING

    # Determine if this is a translucent material based on the node group name
    is_translucent = shader_node.name == APERTURE_TRANSLUCENT_NODE_GROUP_NAME
    
    if is_translucent:
        # Input map for "Aperture Translucent" node group
        input_map = {
            "Transmittance/Diffuse Albedo": ["inputs:transmittance_texture", "transmittance_texture", "inputs:transmittance_color", "transmittance_color"],
            "IOR": ["inputs:ior_constant", "ior_constant"],
            "Thin Walled": ["inputs:thin_walled", "thin_walled"],
            "Thin Wall Thickness": ["inputs:thin_wall_thickness", "thin_wall_thickness"],
            "Use Diffuse Layer": ["inputs:use_diffuse_layer", "use_diffuse_layer"],
            "Transmittance Measurement Distance": ["inputs:transmittance_measurement_distance", "transmittance_measurement_distance"],
            "Normal Map": ["inputs:normalmap_texture", "normalmap_texture"],
            
            # Emission inputs
            "Enable Emission": ["inputs:enable_emission", "enable_emission"],
            "Emissive Color": ["inputs:emissive_color", "emissive_color"],
            "Emissive Intensity": ["inputs:emissive_intensity", "emissive_intensity"],
        }
    else:
        # Input map for "Aperture Opaque" node group
        input_map = {
            # From your export.json and common PBR:
            "Albedo Color": ["inputs:diffuse_texture", "diffuse_texture", "diffuse_color_constant"],
            "Opacity": ["inputs:opacity_texture", "opacity_texture", "opacity_constant", "inputs:opacity", "opacity"], # Added more specific opacity
            "Roughness": ["inputs:reflectionroughness_texture", "reflectionroughness_texture", "reflection_roughness_constant"],
            "Metallic": ["inputs:metallic_texture", "metallic_texture", "metallic_constant"],
            "Normal Map": ["inputs:normalmap_texture", "normalmap_texture"], # This will be handled by process_input creating a Normal Map node
            "Height Map": ["inputs:height_texture", "height_texture", "height_constant"], # For displacement

            # Emission (matching export.json)
            "Enable Emission": ["inputs:enable_emission"], # This might control visibility of other emission inputs
            "Emissive Color": ["inputs:emissive_mask_texture", "emissive_mask_texture", "emissive_color_constant"],
            "Emissive Intensity": ["inputs:emissive_intensity", "emissive_intensity"],

            # Other potential direct mappings from export.json (if they are top-level inputs in the group)
            # "Enable Iridescence": ["inputs:enable_iridescence"], # Example, if such an input exists
            # "Thickness": ["inputs:thickness"], # Example
            # "Inwards Displacement": ["inputs:inwards_displacement"], # Example for direct value
            # "Outwards Displacement": ["inputs:outwards_displacement"], # Example for direct value
        }

    # Y position for texture nodes will be relative to the shader_node
    base_y_pos = shader_node.location.y
    # Texture node X will be to the left of shader_node
    # texture_node_x_offset = -400 (handled in process_input)

    y_pos_offset = 200 # Initial Y offset from shader_node for the first texture
    texture_node_spacing = 250 # Vertical spacing between texture nodes

    # Process each PBR input
    processed_texture = False # Flag to track if any texture node was created in this cycle
    for group_socket_name, usd_input_names in input_map.items():
        target_socket = shader_node.inputs.get(group_socket_name)
        if not target_socket:
            # print(f" Socket '{group_socket_name}' not found on '{shader_node.name}', skipping.")
            continue # Skip if group socket doesn't exist

        print(f"      Checking input for Group socket: '{group_socket_name}'") # LOGGING
        input_value = None
        found_name = None
        for name in usd_input_names:
            input_value = get_input_value(shader, name)
            if input_value is not None:
                found_name = name
                # Special case: if enable_emission is false, ignore emission inputs
                if group_socket_name in ["Emissive Color", "Emissive Intensity"]:
                    # Check the state of "Enable Emission" on the group node itself if it exists and is set by USD
                    # This assumes "Enable Emission" is a boolean input on the group
                    enable_emission_group_input = shader_node.inputs.get("Enable Emission")
                    if enable_emission_group_input:
                        # Check if this input was set by a previous USD value
                        # This is a bit tricky as default_value reflects the current state, not necessarily if it was *just* set
                        # For now, we rely on the USD 'inputs:enable_emission' directly
                        pass # Further logic might be needed if the group's own state should gate this

                    # More reliably, check the original USD 'inputs:enable_emission'
                    usd_enable_emission_val = get_input_value(shader, "inputs:enable_emission")
                    if isinstance(usd_enable_emission_val, bool) and not usd_enable_emission_val:
                        print("  Emission disabled via USD 'inputs:enable_emission', skipping emission inputs.")
                        input_value = None # Force skip this input
                        break # Don't check other names for this socket
                
                if input_value is not None:
                    break # Use the first value found


        if input_value is not None:
            is_normal = (group_socket_name == "Normal Map") # If we're trying to connect TO "Normal Map"
            is_height = (group_socket_name == "Height Map")
            # Identify non-color data sockets based on common PBR conventions
            is_non_color = group_socket_name in ["Metallic", "Roughness", "Opacity", "Height Map", "Emissive Intensity"] # Add others if needed
            
            # Special case for Normal Map: the target socket on group expects final normal vector,
            # but process_input creates a ShaderNodeNormalMap if is_normal is true.
            # So, for "Normal Map" input on the group, we pass is_normal=True to process_input.
            # For "Height Map", it expects a scalar, so is_normal=False, but is_non_color=True.

            print(f"        Found value for '{found_name}': {input_value} (Type: {type(input_value)}) for group socket '{group_socket_name}'") # LOGGING
            # Calculate node position for textures relative to the main shader_node
            node_y_pos = base_y_pos + y_pos_offset

            created_node = process_input(
                input_value, found_name, nodes, links, shader_node, group_socket_name,
                usd_file_path_context, node_pos=(-400, node_y_pos), # X is hardcoded, Y is dynamic
                is_normal=is_normal, 
                is_non_color=is_non_color
            )

            # If a texture or normal map node was created, decrease y_pos_offset for the next one
            if created_node:
                y_pos_offset -= texture_node_spacing
                processed_texture = True # Mark that a texture was processed


    # --- Alpha / Transparency Handling (for Aperture Opaque) ---
    # This needs to be adapted based on how "Aperture Opaque" handles opacity/alpha.
    # Assuming "Opacity" input on the group node.
    opacity_socket = shader_node.inputs.get("Opacity")
    albedo_socket = shader_node.inputs.get("Albedo Color") # Assuming "Albedo Color" is the new name

    if opacity_socket and not opacity_socket.is_linked and albedo_socket and albedo_socket.is_linked:
        albedo_node = albedo_socket.links[0].from_node
        if albedo_node.type == 'TEX_IMAGE' and 'Alpha' in albedo_node.outputs:
            # Connect Albedo Alpha to Opacity if Opacity is not already driven by an explicit map.
            print(f"  Connecting Alpha from Albedo Color texture ('{albedo_node.image.name if albedo_node.image else 'Unknown'}') to Opacity input as a fallback.")
            links.new(albedo_node.outputs['Alpha'], opacity_socket)
            # Blend mode settings might be handled by properties on Aperture Opaque or material settings.
            # For now, we'll assume the group node or explicit USD metadata handles blend modes.
            # bl_material.blend_method = 'HASHED'
            # bl_material.shadow_method = 'HASHED'

    # --- Emission Strength (if "Enable Emission" is a property of the node group and is true) ---
    # This logic assumes "Emissive Color" and "Emissive Intensity" are inputs,
    # and "Enable Emission" might also be an input on the group.
    emissive_color_socket = shader_node.inputs.get("Emissive Color")
    emissive_intensity_socket = shader_node.inputs.get("Emissive Intensity")
    enable_emission_socket = shader_node.inputs.get("Enable Emission") # Check if this socket exists

    # If Emissive Color is linked, and Intensity isn't, and (Enable Emission is true OR not present)
    if emissive_color_socket and emissive_intensity_socket and \
       emissive_color_socket.is_linked and not emissive_intensity_socket.is_linked:
        
        emission_is_enabled_by_group_input = True # Assume enabled if socket doesn't exist or is not 0
        if enable_emission_socket:
            # Check the default_value of the "Enable Emission" socket on the group node
            if isinstance(enable_emission_socket.default_value, (float, int)) and enable_emission_socket.default_value == 0:
                emission_is_enabled_by_group_input = False
            elif isinstance(enable_emission_socket.default_value, bool) and not enable_emission_socket.default_value:
                 emission_is_enabled_by_group_input = False


        # Also check the original USD 'inputs:enable_emission' as a primary source of truth
        usd_enable_emission_val = get_input_value(shader, "inputs:enable_emission")
        explicitly_disabled_by_usd = isinstance(usd_enable_emission_val, bool) and not usd_enable_emission_val

        if not explicitly_disabled_by_usd and emission_is_enabled_by_group_input:
            if emissive_intensity_socket.default_value == 0.0:
                 emissive_intensity_socket.default_value = 1.0 # Default to 1.0
                 print("  Set Emissive Intensity to 1.0 as Emissive Color is present and emission is not explicitly disabled.")


    # --- TODO: Displacement Handling ---
    # If "Height Map" was processed by process_input, it might be directly connected to a "Height Map" input.
    # The "Aperture Opaque" node is expected to do the displacement calculation internally.
    # The main displacement output of the group is already connected to Material Output in create_default_blender_material.
    # We might need to set "Inwards Displacement" and "Outwards Displacement" if they are inputs on the group.
    
    # Example: Setting displacement scale factors if they are inputs on the group
    # outwards_disp_socket = shader_node.inputs.get("Outwards Displacement")
    # if outwards_disp_socket:
    #     outwards_disp_val = get_input_value(shader, "inputs:outwards_displacement_factor") # Fictional USD input
    #     if outwards_disp_val is not None:
    #         outwards_disp_socket.default_value = float(outwards_disp_val)
    # (Similar for "Inwards Displacement")


# Remove or comment out old/unused processing functions
# def setup_transparency(...): pass
# def process_emissive_material(...): pass
# def process_mdl_material(...): pass

# --- New Main Function ---
def get_or_create_instance_material(base_material_path, instance_metadata, usd_stage, usd_file_path_context, material_cache):
    """
    Gets an existing Blender material or creates a new one based on the base USD material
    path and instance-specific metadata overrides.

    Args:
        base_material_path (str): USD path to the base material prim.
        instance_metadata (dict): Dictionary of _remix_metadata overrides for this instance.
        usd_stage (Usd.Stage): The USD stage.
        usd_file_path_context (str): Absolute path of the main imported USD file.
        material_cache (dict): Cache dictionary to store/retrieve created materials.

    Returns:
        bpy.types.Material: The resulting Blender material, or None.
    """
    if not USD_AVAILABLE:
        return None

    # --- Generate Unique Key/Name ---
    metadata_hash = ""
    if instance_metadata: # Only hash if metadata is present and non-empty
        # Sort the dictionary by key for consistent hashing
        sorted_meta_string = json.dumps(instance_metadata, sort_keys=True)
        metadata_hash = hashlib.md5(sorted_meta_string.encode('utf-8')).hexdigest()[:8] # Short hash

    # Generate base cache key
    base_cache_key = _generate_material_cache_key(base_material_path, usd_file_path_context)
    unique_key = f"{base_cache_key}_{metadata_hash}" if metadata_hash else base_cache_key

    # --- Check Cache ---
    if unique_key in material_cache:
        print(f"  Found cached material for key: {unique_key} -> '{material_cache[unique_key].name}'")
        return material_cache[unique_key]
    
    # Also check global cache
    if unique_key in _global_material_cache:
        cached_material = _global_material_cache[unique_key]
        if cached_material and cached_material.name in bpy.data.materials:
            print(f"  Found globally cached material for key: {unique_key} -> '{cached_material.name}'")
            material_cache[unique_key] = cached_material  # Add to local cache too
            return cached_material
        else:
            # Remove invalid cache entry
            del _global_material_cache[unique_key]

    print(f"  Processing material for key: {unique_key}")

    # --- Get or Create Base Material --- #
    base_bl_material = create_material(base_material_path, usd_stage, usd_file_path_context)
    if not base_bl_material:
        print(f"    ERROR: Failed to create base material for {base_material_path}")
        return None

    # Find the shader node in the base material
    base_shader_node = None
    for node in base_bl_material.node_tree.nodes:
        if node.type == 'GROUP' and node.node_tree and node.node_tree.name == APERTURE_OPAQUE_NODE_GROUP_NAME:
            base_shader_node = node
            break
        elif node.type == 'BSDF_PRINCIPLED':
            base_shader_node = node
            break

    if not base_shader_node:
        print(f"    ERROR: Could not find shader node in base material '{base_bl_material.name}'")
        return base_bl_material

    # --- Apply Overrides (if metadata exists) ---
    final_bl_material = base_bl_material
    if metadata_hash: # Needs override
        print(f"    Applying metadata overrides (hash: {metadata_hash})")
        # Generate unique material name
        context_suffix = base_cache_key.split('#')[1] if '#' in base_cache_key else "default"
        unique_material_name = f"{base_bl_material.name}_{metadata_hash}"

        # Check if this specific override already exists
        if unique_material_name in bpy.data.materials:
            print(f"    Found existing overridden material: {unique_material_name}")
            final_bl_material = bpy.data.materials[unique_material_name]
        else:
            print(f"    Duplicating base '{base_bl_material.name}' to '{unique_material_name}'")
            final_bl_material = base_bl_material.copy()
            final_bl_material.name = unique_material_name

            # Find the shader node in the duplicated material
            duplicated_shader_node = None
            for node in final_bl_material.node_tree.nodes:
                if node.type == 'GROUP' and node.node_tree and node.node_tree.name == APERTURE_OPAQUE_NODE_GROUP_NAME:
                    duplicated_shader_node = node
                    break
                elif node.type == 'BSDF_PRINCIPLED' and base_shader_node.type == 'BSDF_PRINCIPLED':
                    duplicated_shader_node = node
                    break
            
            if duplicated_shader_node:
                apply_metadata_overrides(instance_metadata, final_bl_material, duplicated_shader_node)
            else:
                print(f"    ERROR: Could not find shader node in duplicated material '{unique_material_name}'")
                final_bl_material = base_bl_material # Fallback

    # --- Cache and Return ---
    material_cache[unique_key] = final_bl_material
    _global_material_cache[unique_key] = final_bl_material  # Also cache globally
    print(f"  Material finalized and cached: '{final_bl_material.name}'")
    return final_bl_material


# --- Refactored Base Material Creation ---
def create_base_material_nodes(usd_material_path, usd_stage, usd_file_path_context):
    """
    Creates a Blender material with nodes based *only* on the USD material prim,
    without applying instance metadata.

    Returns:
        tuple: (bpy.types.Material, shader_node) or None on failure.
    """
    material_prim = usd_stage.GetPrimAtPath(usd_material_path)
    if not material_prim or not material_prim.IsA(UsdShade.Material):
        print(f"    WARNING: Base material prim not found or invalid at path: {usd_material_path}")
        return None # Cannot create base

    material_name = bpy.path.clean_name(material_prim.GetName())
    if not material_name:
        material_name = bpy.path.clean_name(os.path.basename(usd_material_path))

    # Get existing or create new Blender material
    if material_name in bpy.data.materials:
        bl_material = bpy.data.materials[material_name]
        # Ensure it has nodes and our custom group (or a fallback BSDF)
        if not bl_material.use_nodes:
            bl_material.use_nodes = True
        if not bl_material.node_tree:
            bl_material, shader_node = create_default_blender_material(material_name) # Rebuild if no tree
        else:
            # Try to find the Aperture Opaque group or a Principled BSDF
            shader_node = None
            for node in bl_material.node_tree.nodes:
                if node.type == 'GROUP' and node.node_tree and node.node_tree.name == APERTURE_OPAQUE_NODE_GROUP_NAME:
                    shader_node = node
                    break
            if not shader_node: # If not found, try to find a Principled BSDF (fallback or older material)
                 shader_node = bl_material.node_tree.nodes.get("Principled BSDF") # Old name
                 if not shader_node: # Still not found, create default setup
                     bl_material, shader_node = create_default_blender_material(material_name)

        print(f"    Reusing existing material: '{material_name}' with shader node '{shader_node.name if shader_node else 'None'}'")

    else:
        # Create new Blender material if it doesn't exist
        bl_material, shader_node = create_default_blender_material(material_name)
        if not shader_node: # Should be guaranteed by create_default_blender_material
            print(f"    ERROR: Could not create main shader node in new material '{material_name}'.")
            return None
        print(f"    Created new material: '{material_name}' with shader node '{shader_node.name}'")

    # Find the USD surface shader
    surface_shader = get_shader_from_material(material_prim)
    if not surface_shader:
        print(f"    WARNING: No surface shader found for material: {material_name}. Using default setup.")
        return bl_material, shader_node # Return default setup

    shader_prim = surface_shader.GetPrim()
    print(f"    Found shader '{shader_prim.GetName()}' for base material '{material_name}'")

    # --- DEBUG: Print shader inputs --- #
    print(f"      Available inputs on {shader_prim.GetPath()}:")
    for shader_input in surface_shader.GetInputs():
        print(f"        - {shader_input.GetBaseName()}")
    # --- END DEBUG --- #

    # Process PBR inputs (this populates the node tree)
    process_pbr(surface_shader, bl_material, shader_node, usd_file_path_context)

    return bl_material, shader_node # Return the shader_node (group or BSDF)

# --- New Metadata Application Function ---
def apply_metadata_overrides(metadata, bl_material, shader_node):
    """
    Modifies a Blender material based on Remix metadata overrides.
    Operates on the provided shader_node (custom group or BSDF).
    """
    nodes = bl_material.node_tree.nodes
    links = bl_material.node_tree.links
    print(f"    Applying overrides to {bl_material.name} using shader node {shader_node.name}...")

    # --- Alpha Blending --- #
    alpha_blend_enabled = metadata.get('alphaBlendEnabled', 0) == 1
    alpha_test_enabled = metadata.get('alphaTestEnabled', 0) == 1

    if alpha_blend_enabled:
        set_material_blend_method_compatible(bl_material, 'BLEND', 'HASHED')
        print(f"      Set blend_method=BLEND, shadow_method=HASHED")
    elif alpha_test_enabled:
        alpha_threshold = metadata.get('alphaTestReferenceValue', 0) / 255.0
        set_material_blend_method_compatible(bl_material, 'CLIP', 'CLIP', alpha_threshold)
        print(f"      Set blend_method=CLIP, shadow_method=CLIP, threshold={alpha_threshold:.3f}")
    else:
        set_material_blend_method_compatible(bl_material, 'OPAQUE', 'OPAQUE')
        # print(f"      Set blend_method=OPAQUE, shadow_method=OPAQUE")

    # --- Texture Operations (Example - Needs Refinement) --- #
    # This part is complex and requires mapping Remix ops to Blender nodes
    # Example: COLOR = TextureColor <OP> DiffuseColor
    # Example: ALPHA = TextureAlpha <OP> DiffuseAlpha

    tex_color_op = metadata.get('textureColorOperation')
    tex_alpha_op = metadata.get('textureAlphaOperation')

    # Example: If color op is MODULATE (4), insert a Mix node
    if tex_color_op == 4: # D3DTOP_MODULATE
        # Target "Albedo Color" on Aperture Opaque, or "Base Color" on Principled BSDF
        target_socket_name = "Albedo Color" if shader_node.type == 'GROUP' else "Base Color"
        color_socket = shader_node.inputs.get(target_socket_name)

        if color_socket and color_socket.is_linked:
            tex_node = color_socket.links[0].from_node
            if tex_node.type == 'TEX_IMAGE':
                print(f"      Applying TextureColorOperation: MODULATE to '{target_socket_name}'")
                original_color = color_socket.default_value[:]
                mix_node = nodes.new(type='ShaderNodeMixRGB')
                mix_node.blend_type = 'MULTIPLY'
                mix_node.location = (shader_node.location.x - 200, shader_node.location.y + 100)
                links.new(tex_node.outputs['Color'], mix_node.inputs['Color1'])
                mix_node.inputs['Color2'].default_value = original_color
                links.remove(color_socket.links[0])
                links.new(mix_node.outputs['Color'], color_socket)

    # --- Handle Alpha Operation --- #
    if tex_alpha_op == 1: # D3DTOP_SELECTARG1 (Use texture alpha)
        # Target "Opacity" on Aperture Opaque, or "Alpha" on Principled BSDF
        alpha_target_socket_name = "Opacity" if shader_node.type == 'GROUP' else "Alpha"
        # Source of alpha is usually from the Albedo/BaseColor texture
        color_source_socket_name = "Albedo Color" if shader_node.type == 'GROUP' else "Base Color"

        alpha_socket = shader_node.inputs.get(alpha_target_socket_name)
        color_socket = shader_node.inputs.get(color_source_socket_name)

        if alpha_socket and not alpha_socket.is_linked and color_socket and color_socket.is_linked:
            incoming_node = color_socket.links[0].from_node

            if incoming_node.type == 'TEX_IMAGE' and 'Alpha' in incoming_node.outputs:
                print(f"      Applying TextureAlphaOperation: SELECTARG1 (Connecting Texture Alpha to '{alpha_target_socket_name}')")
                links.new(incoming_node.outputs['Alpha'], alpha_socket)
            elif incoming_node.type == 'MIX_RGB' and incoming_node.inputs['Color1'].is_linked: # Modulated color
                tex_node = incoming_node.inputs['Color1'].links[0].from_node
                if tex_node.type == 'TEX_IMAGE' and 'Alpha' in tex_node.outputs:
                    print(f"      Applying TextureAlphaOperation: SELECTARG1 (Connecting Texture Alpha via Mix to '{alpha_target_socket_name}')")
                    links.new(tex_node.outputs['Alpha'], alpha_socket)

    # TODO: Handle other textureAlphaOp values

    # This might involve different Mix node types, Math nodes, or Separate/Combine RGBA nodes.
    # Needs careful mapping based on DirectX texture stage states.
    print(f"      TODO: Implement handling for textureColorOp={tex_color_op}, textureAlphaOp={tex_alpha_op}")