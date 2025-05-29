import bpy
import os
from typing import Optional, Dict, Any, Tuple, List
from .core_utils import (
    USD_AVAILABLE, 
    create_material_cache_key, 
    MaterialPathResolver,
    sanitize_prim_name
)

if USD_AVAILABLE:
    from pxr import Usd, UsdShade, Sdf, Gf
    from .usd_utils import get_shader_from_material, get_input_value
    from .texture_utils import load_texture, resolve_material_asset_path
    from . import constants

# --- Node Group Management ---

APERTURE_OPAQUE_NODE_GROUP_NAME = "Aperture Opaque"

def ensure_aperture_opaque_node_group() -> Optional[bpy.types.NodeGroup]:
    """Ensure the Aperture Opaque node group is available."""
    if APERTURE_OPAQUE_NODE_GROUP_NAME in bpy.data.node_groups:
        return bpy.data.node_groups[APERTURE_OPAQUE_NODE_GROUP_NAME]

    blend_file_path = os.path.join(constants.ADDON_DIR, "nodes", "ApertureOpaque.blend")
    if not os.path.exists(blend_file_path):
        print(f"ERROR: Could not find ApertureOpaque.blend at {blend_file_path}")
        return None

    try:
        with bpy.data.libraries.load(blend_file_path, link=False) as (data_from, data_to):
            if APERTURE_OPAQUE_NODE_GROUP_NAME in data_from.node_groups:
                data_to.node_groups = [APERTURE_OPAQUE_NODE_GROUP_NAME]
                print(f"Successfully appended node group: {APERTURE_OPAQUE_NODE_GROUP_NAME}")
            else:
                print(f"ERROR: Node group '{APERTURE_OPAQUE_NODE_GROUP_NAME}' not found in {blend_file_path}")
                return None
    except Exception as e:
        print(f"ERROR: Failed to load node group from {blend_file_path}: {e}")
        return None

    return bpy.data.node_groups.get(APERTURE_OPAQUE_NODE_GROUP_NAME)

# --- Material Creation ---

class MaterialFactory:
    """Factory for creating Blender materials with consistent setup."""
    
    @staticmethod
    def create_default_material(name: str) -> Tuple[bpy.types.Material, bpy.types.Node]:
        """Create a default Blender material with Aperture Opaque node group."""
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        output_node = nodes.new(type='ShaderNodeOutputMaterial')
        output_node.location = (300, 0)

        aperture_node_group = ensure_aperture_opaque_node_group()
        if not aperture_node_group:
            # Fallback to Principled BSDF
            bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
            bsdf.location = (0, 0)
            links.new(bsdf.outputs['BSDF'], output_node.inputs['Surface'])
            return mat, bsdf

        # Add custom node group
        group_node = nodes.new(type='ShaderNodeGroup')
        group_node.node_tree = aperture_node_group
        group_node.name = APERTURE_OPAQUE_NODE_GROUP_NAME
        group_node.location = (0, 0)

        # Connect outputs
        if 'BSDF' in group_node.outputs:
            links.new(group_node.outputs['BSDF'], output_node.inputs['Surface'])
        if 'Displacement' in group_node.outputs:
            links.new(group_node.outputs['Displacement'], output_node.inputs['Displacement'])

        return mat, group_node
    
    @staticmethod
    def create_error_material(name: str) -> bpy.types.Material:
        """Create an error material with red color."""
        if name in bpy.data.materials:
            return bpy.data.materials[name]
        
        mat, shader_node = MaterialFactory.create_default_material(name)
        
        # Set red color for error indication
        if hasattr(shader_node, 'inputs') and 'Base Color' in shader_node.inputs:
            shader_node.inputs['Base Color'].default_value = (1.0, 0.0, 0.0, 1.0)
        elif hasattr(shader_node, 'inputs') and 'Albedo Color' in shader_node.inputs:
            shader_node.inputs['Albedo Color'].default_value = (1.0, 0.0, 0.0, 1.0)
        
        return mat

# --- Input Processing ---

class InputProcessor:
    """Processes USD shader inputs and connects them to Blender nodes."""
    
    def __init__(self, usd_file_path_context: str):
        self.path_resolver = MaterialPathResolver(usd_file_path_context)
    
    def process_input(
        self, 
        usd_input_value: Any, 
        input_type: str, 
        nodes: bpy.types.Nodes, 
        links: bpy.types.NodeLinks, 
        target_node: bpy.types.Node, 
        target_socket_name: str,
        node_pos: Tuple[int, int] = (-400, 0), 
        is_normal: bool = False, 
        is_non_color: bool = False
    ) -> Optional[bpy.types.Node]:
        """Process a USD input value and connect it to a Blender node socket."""
        if usd_input_value is None:
            return None

        target_socket = target_node.inputs.get(target_socket_name)
        if not target_socket:
            print(f"ERROR: Target socket '{target_socket_name}' not found on node '{target_node.name}'.")
            return None

        # Check if it's a texture path
        if self._is_texture_path(usd_input_value):
            return self._process_texture_input(
                usd_input_value, input_type, nodes, links, target_socket, 
                node_pos, is_normal, is_non_color
            )
        else:
            return self._process_constant_input(usd_input_value, target_socket)
    
    def _is_texture_path(self, value: Any) -> bool:
        """Check if a value represents a texture path."""
        if not isinstance(value, (str, Sdf.AssetPath)):
            return False
        
        path_str = str(value)
        if path_str.startswith('@') and path_str.endswith('@'):
            path_str = path_str[1:-1]
        
        return (
            '../' in path_str or 
            'assets/' in path_str or 
            any(path_str.lower().endswith(ext) for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tiff'])
        )
    
    def _process_texture_input(
        self, 
        texture_path: Any, 
        input_type: str, 
        nodes: bpy.types.Nodes, 
        links: bpy.types.NodeLinks, 
        target_socket: bpy.types.NodeSocket,
        node_pos: Tuple[int, int], 
        is_normal: bool, 
        is_non_color: bool
    ) -> Optional[bpy.types.Node]:
        """Process texture input."""
        path_str = str(texture_path)
        if path_str.startswith('@') and path_str.endswith('@'):
            path_str = path_str[1:-1]

        resolved_path = resolve_material_asset_path(path_str, self.path_resolver.usd_file_path_context)
        
        if not resolved_path or not os.path.exists(resolved_path):
            print(f"Warning: Texture path not found: {resolved_path}")
            return None

        image = load_texture(resolved_path, is_normal=is_normal, is_non_color=is_non_color)
        if not image:
            print(f"Warning: Failed to load texture: {resolved_path}")
            return None

        # Create texture node
        tex_node = nodes.new(type='ShaderNodeTexImage')
        tex_node.image = image
        tex_node.label = f"{input_type.replace('_', ' ').title()} Texture"
        tex_node.location = node_pos

        # Set color space
        if is_non_color or is_normal:
            image.colorspace_settings.name = 'Non-Color'

        # Handle normal maps specially
        if is_normal:
            normal_map_node = nodes.new(type='ShaderNodeNormalMap')
            normal_map_node.location = (node_pos[0] + 150, node_pos[1])
            links.new(tex_node.outputs['Color'], normal_map_node.inputs['Color'])
            links.new(normal_map_node.outputs['Normal'], target_socket)
            return normal_map_node
        else:
            # Choose appropriate output
            output_socket_name = 'Color'
            if is_non_color and 'Alpha' in tex_node.outputs and target_socket.type == 'VALUE':
                output_socket_name = 'Alpha'
            
            links.new(tex_node.outputs[output_socket_name], target_socket)
            return tex_node
    
    def _process_constant_input(self, value: Any, target_socket: bpy.types.NodeSocket) -> None:
        """Process constant value input."""
        try:
            if isinstance(value, Gf.Vec3f) and target_socket.type == 'RGBA':
                target_socket.default_value = (value[0], value[1], value[2], 1.0)
            elif isinstance(value, Gf.Vec4f) and target_socket.type == 'RGBA':
                target_socket.default_value = tuple(value)
            elif isinstance(value, (int, float)) and target_socket.type == 'VALUE':
                target_socket.default_value = float(value)
            elif isinstance(value, bool) and target_socket.type == 'VALUE':
                target_socket.default_value = 1.0 if value else 0.0
            else:
                # Attempt conversion
                if target_socket.type == 'VALUE' and isinstance(value, (int, float, bool)):
                    target_socket.default_value = float(value)
        except Exception as e:
            print(f"Could not set constant value {value} for socket {target_socket.name}: {e}")

# --- PBR Processing ---

class PBRProcessor:
    """Processes PBR material inputs."""
    
    def __init__(self, usd_file_path_context: str):
        self.input_processor = InputProcessor(usd_file_path_context)
        self.input_map = {
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
    
    def process_pbr_inputs(
        self, 
        shader: UsdShade.Shader, 
        bl_material: bpy.types.Material, 
        shader_node: bpy.types.Node
    ):
        """Process PBR inputs for a shader."""
        nodes = bl_material.node_tree.nodes
        links = bl_material.node_tree.links
        
        base_y_pos = shader_node.location.y
        y_pos_offset = 200
        texture_node_spacing = 250
        
        for group_socket_name, usd_input_names in self.input_map.items():
            target_socket = shader_node.inputs.get(group_socket_name)
            if not target_socket:
                continue
            
            # Find input value
            input_value = None
            for name in usd_input_names:
                input_value = get_input_value(shader, name)
                if input_value is not None:
                    break
            
            if input_value is None:
                continue
            
            # Check emission state
            if group_socket_name in ["Emissive Color", "Emissive Intensity"]:
                usd_enable_emission_val = get_input_value(shader, "inputs:enable_emission")
                if isinstance(usd_enable_emission_val, bool) and not usd_enable_emission_val:
                    continue
            
            # Process input
            is_normal = (group_socket_name == "Normal Map")
            is_non_color = group_socket_name in ["Metallic", "Roughness", "Opacity", "Height Map", "Emissive Intensity"]
            
            node_y_pos = base_y_pos + y_pos_offset
            created_node = self.input_processor.process_input(
                input_value, group_socket_name, nodes, links, shader_node, group_socket_name,
                node_pos=(-400, node_y_pos), is_normal=is_normal, is_non_color=is_non_color
            )
            
            if created_node:
                y_pos_offset -= texture_node_spacing
        
        # Post-process material settings
        self._post_process_material(bl_material, shader_node)
    
    def _post_process_material(self, bl_material: bpy.types.Material, shader_node: bpy.types.Node):
        """Apply post-processing to the material."""
        links = bl_material.node_tree.links
        
        # Handle alpha transparency
        opacity_socket = shader_node.inputs.get("Opacity")
        albedo_socket = shader_node.inputs.get("Albedo Color")
        
        if (opacity_socket and not opacity_socket.is_linked and 
            albedo_socket and albedo_socket.is_linked):
            
            albedo_node = albedo_socket.links[0].from_node
            if albedo_node.type == 'TEX_IMAGE' and 'Alpha' in albedo_node.outputs:
                links.new(albedo_node.outputs['Alpha'], opacity_socket)
        
        # Handle emission intensity
        emissive_color_socket = shader_node.inputs.get("Emissive Color")
        emissive_intensity_socket = shader_node.inputs.get("Emissive Intensity")
        
        if (emissive_color_socket and emissive_color_socket.is_linked and
            emissive_intensity_socket and not emissive_intensity_socket.is_linked and
            emissive_intensity_socket.default_value == 0.0):
            emissive_intensity_socket.default_value = 1.0

# --- Material Cache ---

class MaterialCache:
    """Manages material caching to avoid redundant creation."""
    
    def __init__(self):
        self._base_material_cache = {}
        self._instance_material_cache = {}
    
    def get_or_create_base_material(
        self, 
        usd_material_path: str, 
        usd_stage: Usd.Stage, 
        usd_file_path_context: str
    ) -> Optional[Tuple[bpy.types.Material, bpy.types.Node]]:
        """Get or create a base material."""
        if usd_material_path in self._base_material_cache:
            return self._base_material_cache[usd_material_path]
        
        result = self._create_base_material(usd_material_path, usd_stage, usd_file_path_context)
        self._base_material_cache[usd_material_path] = result
        return result
    
    def get_or_create_instance_material(
        self,
        base_material_path: str,
        instance_metadata: Dict[str, Any],
        usd_stage: Usd.Stage,
        usd_file_path_context: str
    ) -> Optional[bpy.types.Material]:
        """Get or create an instance material with metadata overrides."""
        cache_key = create_material_cache_key(base_material_path, instance_metadata)
        
        if cache_key in self._instance_material_cache:
            return self._instance_material_cache[cache_key]
        
        # Get base material
        base_result = self.get_or_create_base_material(base_material_path, usd_stage, usd_file_path_context)
        if not base_result:
            return None
        
        base_material, base_shader_node = base_result
        
        # Apply metadata overrides if needed
        if instance_metadata:
            final_material = self._apply_metadata_overrides(
                base_material, base_shader_node, instance_metadata, cache_key
            )
        else:
            final_material = base_material
        
        self._instance_material_cache[cache_key] = final_material
        return final_material
    
    def _create_base_material(
        self, 
        usd_material_path: str, 
        usd_stage: Usd.Stage, 
        usd_file_path_context: str
    ) -> Optional[Tuple[bpy.types.Material, bpy.types.Node]]:
        """Create a base material from USD."""
        if not USD_AVAILABLE:
            return None
        
        material_prim = usd_stage.GetPrimAtPath(usd_material_path)
        if not material_prim or not material_prim.IsA(UsdShade.Material):
            error_name = f"ERROR_{os.path.basename(usd_material_path)}"
            error_mat = MaterialFactory.create_error_material(error_name)
            return (error_mat, None)
        
        # Create material
        material_name = sanitize_prim_name(material_prim.GetName() or os.path.basename(usd_material_path))
        bl_material, shader_node = MaterialFactory.create_default_material(material_name)
        
        # Find surface shader
        surface_shader = get_shader_from_material(material_prim)
        if not surface_shader:
            return (bl_material, shader_node)
        
        # Process PBR inputs
        pbr_processor = PBRProcessor(usd_file_path_context)
        pbr_processor.process_pbr_inputs(surface_shader, bl_material, shader_node)
        
        return (bl_material, shader_node)
    
    def _apply_metadata_overrides(
        self,
        base_material: bpy.types.Material,
        base_shader_node: bpy.types.Node,
        metadata: Dict[str, Any],
        cache_key: str
    ) -> bpy.types.Material:
        """Apply metadata overrides to create a new material."""
        unique_name = f"{base_material.name}_{cache_key.split('_')[-1]}"
        
        if unique_name in bpy.data.materials:
            return bpy.data.materials[unique_name]
        
        # Duplicate material
        final_material = base_material.copy()
        final_material.name = unique_name
        
        # Find shader node in duplicated material
        duplicated_shader_node = None
        for node in final_material.node_tree.nodes:
            if (node.type == 'GROUP' and node.node_tree and 
                node.node_tree.name == APERTURE_OPAQUE_NODE_GROUP_NAME):
                duplicated_shader_node = node
                break
            elif node.type == 'BSDF_PRINCIPLED' and base_shader_node.type == 'BSDF_PRINCIPLED':
                duplicated_shader_node = node
                break
        
        if duplicated_shader_node:
            self._apply_metadata_to_node(metadata, duplicated_shader_node)
        
        return final_material
    
    def _apply_metadata_to_node(self, metadata: Dict[str, Any], shader_node: bpy.types.Node):
        """Apply metadata overrides to a shader node."""
        # This would contain the logic to apply specific metadata overrides
        # Implementation depends on the specific metadata format used by RTX Remix
        pass
    
    def clear_cache(self):
        """Clear all cached materials."""
        self._base_material_cache.clear()
        self._instance_material_cache.clear()

# Global material cache instance
_material_cache = MaterialCache()

def get_material_cache() -> MaterialCache:
    """Get the global material cache."""
    return _material_cache 