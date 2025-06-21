import bpy
import os
import asyncio
from typing import Optional, Dict, Any, Tuple, List, Callable
from .core_utils import (
    USD_AVAILABLE, 
    get_relative_path, 
    sanitize_prim_name, 
    generate_uuid_name,
    get_texture_processor,
    ProgressTracker
)

if USD_AVAILABLE:
    from pxr import Usd, UsdGeom, UsdShade, UsdLux, Sdf, Vt, Gf

# --- MDL File Management ---

class MDLManager:
    """Manages MDL shader files for RTX Remix."""
    
    @staticmethod
    def ensure_mdl_files(project_root: str) -> str:
        """Copy MDL shader files from addon to project directory."""
        from . import constants
        import shutil
        
        mdl_source_dir = os.path.join(constants.ADDON_DIR, "materials")
        mdl_target_dir = os.path.join(project_root, "assets", "remix_materials")
        
        os.makedirs(mdl_target_dir, exist_ok=True)
        
        copied_files = []
        if os.path.exists(mdl_source_dir):
            for mdl_file in os.listdir(mdl_source_dir):
                if mdl_file.endswith(".mdl"):
                    source_file = os.path.join(mdl_source_dir, mdl_file)
                    target_file = os.path.join(mdl_target_dir, mdl_file)
                    
                    if not os.path.exists(target_file) or os.path.getmtime(source_file) > os.path.getmtime(target_file):
                        shutil.copy2(source_file, target_file)
                        copied_files.append(mdl_file)
        
        if copied_files:
            print(f"Copied MDL files to {mdl_target_dir}: {', '.join(copied_files)}")
        
        return mdl_target_dir

# --- Material Path Management ---

class MaterialPathManager:
    """Manages material path creation for different RTX Remix styles."""
    
    @staticmethod
    def create_nvidia_style_material_path(
        sublayer_stage: Usd.Stage,
        parent_mesh_path: Sdf.Path,
        obj: Optional[bpy.types.Object] = None
    ) -> Tuple[Sdf.Path, Usd.Prim, Usd.Prim]:
        """Create material path using NVIDIA Remix style."""
        xforms_path = parent_mesh_path.AppendPath("XForms")
        mesh_name = sanitize_prim_name(obj.name) if obj else "Suzanne"
        mesh_path = xforms_path.AppendPath(mesh_name)
        
        # Apply MaterialBindingAPI to mesh
        mesh_prim = sublayer_stage.OverridePrim(mesh_path)
        schemas = mesh_prim.GetAppliedSchemas()
        if "MaterialBindingAPI" not in schemas:
            # Apply MaterialBindingAPI schema using the proper USD API
            UsdShade.MaterialBindingAPI.Apply(mesh_prim)
            print(f"    Applied MaterialBindingAPI schema to {mesh_path}")
        
        # Create material and shader paths
        looks_path = mesh_path.AppendPath("Looks")
        material_prim = sublayer_stage.DefinePrim(looks_path, "Material")
        
        shader_path = looks_path.AppendPath("Shader")
        shader_prim = sublayer_stage.DefinePrim(shader_path, "Shader")
        
        # Add material binding
        binding_rel = mesh_prim.CreateRelationship("material:binding")
        binding_rel.SetTargets([looks_path])
        mesh_prim.CreateAttribute("material:binding:bindMaterialAs", Sdf.ValueTypeNames.Token).Set("strongerThanDescendants")
        
        return looks_path, material_prim, shader_prim
    
    @staticmethod
    def create_standard_material_path(
        sublayer_stage: Usd.Stage,
        material_name: str
    ) -> Tuple[Sdf.Path, Usd.Prim, Usd.Prim]:
        """Create material path using standard style."""
        looks_path = Sdf.Path("/RootNode/Looks")
        sublayer_stage.OverridePrim(looks_path)
        
        mat_base_name = sanitize_prim_name(material_name)
        mat_name_sanitized = generate_uuid_name(mat_base_name, prefix="mat_")
        mat_path = looks_path.AppendPath(mat_name_sanitized)
        
        material_prim = sublayer_stage.OverridePrim(mat_path)
        material_prim.SetTypeName("Material")
        
        shader_name_sanitized = generate_uuid_name(f"shader_{mat_base_name}", prefix="shader_")
        shader_path = mat_path.AppendPath(shader_name_sanitized)
        shader_prim = sublayer_stage.OverridePrim(shader_path)
        shader_prim.SetTypeName("Shader")
        
        return mat_path, material_prim, shader_prim

# --- Shader Setup ---

class ShaderSetup:
    """Handles USD shader setup and connections."""
    
    @staticmethod
    def setup_material_connections(material_prim: Usd.Prim, shader_prim: Usd.Prim):
        """Set up standard material-shader connections."""
        material_api = UsdShade.Material(material_prim)
        shader_output = UsdShade.Shader(shader_prim).CreateOutput("out", Sdf.ValueTypeNames.Token)
        shader_output.SetRenderType("material")
        
        material_api.CreateSurfaceOutput("mdl:surface").ConnectToSource(shader_output)
        material_api.CreateDisplacementOutput("mdl:displacement").ConnectToSource(shader_output)
        material_api.CreateVolumeOutput("mdl:volume").ConnectToSource(shader_output)
    
    @staticmethod
    def setup_shader_attributes(shader_prim: Usd.Prim, mdl_name: str = "AperturePBR_Opacity"):
        """Set up shader attributes for RTX Remix."""
        shader_prim.CreateAttribute("info:implementationSource", Sdf.ValueTypeNames.Token).Set("sourceAsset")
        shader_prim.CreateAttribute("info:mdl:sourceAsset", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(f"{mdl_name}.mdl"))
        shader_prim.CreateAttribute("info:mdl:sourceAsset:subIdentifier", Sdf.ValueTypeNames.Token).Set(mdl_name)
    
    @staticmethod
    def detect_material_type(blender_material: bpy.types.Material) -> str:
        """Detect the material type based on the node group used."""
        if not blender_material.use_nodes:
            return "AperturePBR_Opacity"
        
        # Look for custom node groups
        for node in blender_material.node_tree.nodes:
            if node.type == 'GROUP' and node.node_tree:
                if "Aperture Translucent" in node.node_tree.name:
                    return "AperturePBR_Translucent"
                elif "Aperture Opaque" in node.node_tree.name:
                    return "AperturePBR_Opacity"
        
        # Default to opacity type
        return "AperturePBR_Opacity"

# --- Texture Export ---

class TextureExporter:
    """Handles texture export with async processing."""
    
    def __init__(self, project_root: str, sublayer_path: str):
        self.project_root = project_root
        self.sublayer_path = sublayer_path
        self.texture_processor = get_texture_processor()
        self.textures_dir = os.path.join(project_root, "rtx-remix", "textures")
        
        # Texture type suffixes
        self.type_suffixes = {
            'base color': ".a.rtex",
            'normal': ".n.rtex", 
            'roughness': ".r.rtex",
            'metallic': ".m.rtex",
            'emission': ".e.rtex",
            'opacity': ".o.rtex"
        }
        
        # DDS format mapping
        self.format_map = {
            'base color': 'BC7_UNORM_SRGB',
            'normal': 'BC5_UNORM',
            'roughness': 'BC4_UNORM',
            'metallic': 'BC4_UNORM',
            'emission': 'BC7_UNORM_SRGB',
            'opacity': 'BC4_UNORM'
        }
    
    async def export_textures_parallel(
        self,
        texture_list: List[Tuple[bpy.types.Image, str]],
        progress_callback: Optional[Callable[[str], None]] = None,
        timeout: Optional[float] = None
    ) -> Dict[str, Optional[str]]:
        """Export multiple textures in parallel using the queue system.
        
        Args:
            texture_list: List of (bl_image, texture_type) tuples
            progress_callback: Optional progress callback function
            timeout: Optional timeout in seconds
            
        Returns:
            Dictionary mapping texture name to relative path (or None if failed)
        """
        if not self.texture_processor.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return {}
        
        try:
            os.makedirs(self.textures_dir, exist_ok=True)
        except OSError as e:
            if progress_callback:
                progress_callback(f"Could not create textures directory: {e}")
            return {}
        
        # Prepare tasks for queue
        tasks = []
        texture_map = {}  # Maps task info to texture name for result mapping
        
        for bl_image, texture_type in texture_list:
            # Generate output filename
            base_name, _ = os.path.splitext(bl_image.name)
            type_suffix = self.type_suffixes.get(texture_type.lower(), "")
            dds_file_name = f"{base_name}{type_suffix}.dds"
            absolute_dds_path = os.path.normpath(os.path.join(self.textures_dir, dds_file_name))
            
            # Get DDS format
            dds_format = self.format_map.get(texture_type.lower(), 'BC7_UNORM_SRGB')
            
            # Add to tasks
            task_info = (bl_image, absolute_dds_path, texture_type, dds_format)
            tasks.append(task_info)
            texture_map[bl_image.name] = (absolute_dds_path, texture_type)
        
        if not tasks:
            return {}
        
        # Process textures in parallel
        if progress_callback:
            progress_callback(f"Starting parallel processing of {len(tasks)} textures...")
        
        results = await self.texture_processor.process_textures_parallel(
            tasks, progress_callback, timeout
        )
        
        # Map results back to texture names and convert to relative paths
        final_results = {}
        for bl_image, texture_type in texture_list:
            texture_name = bl_image.name
            absolute_path, _ = texture_map[texture_name]
            
            # Find the corresponding task result
            success = False
            for task_id, task_success in results.items():
                task_status = self.texture_processor.get_conversion_status(task_id)
                if task_status and task_status.get('texture_name') == texture_name:
                    success = task_success
                    break
            
            if success and os.path.exists(absolute_path):
                final_results[texture_name] = get_relative_path(self.sublayer_path, absolute_path)
            else:
                final_results[texture_name] = None
                if progress_callback:
                    progress_callback(f"Failed to export texture: {texture_name}")
        
        if progress_callback:
            successful = sum(1 for v in final_results.values() if v is not None)
            progress_callback(f"Completed texture export: {successful}/{len(final_results)} successful")
        
        return final_results
    
    async def export_texture_async(
        self, 
        bl_image: bpy.types.Image, 
        texture_type: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Optional[str]:
        """Export a single texture asynchronously."""
        results = await self.export_textures_parallel(
            [(bl_image, texture_type)], 
            progress_callback
        )
        return results.get(bl_image.name)
    
    async def queue_texture_export(
        self,
        bl_image: bpy.types.Image,
        texture_type: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> str:
        """Queue a texture export and return task ID for tracking."""
        if not self.texture_processor.is_available():
            raise RuntimeError("texconv.exe not found")
        
        try:
            os.makedirs(self.textures_dir, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"Could not create textures directory: {e}")
        
        # Generate output filename
        base_name, _ = os.path.splitext(bl_image.name)
        type_suffix = self.type_suffixes.get(texture_type.lower(), "")
        dds_file_name = f"{base_name}{type_suffix}.dds"
        absolute_dds_path = os.path.normpath(os.path.join(self.textures_dir, dds_file_name))
        
        # Get DDS format
        dds_format = self.format_map.get(texture_type.lower(), 'BC7_UNORM_SRGB')
        
        # Queue the conversion
        return await self.texture_processor.queue_texture_conversion(
            bl_image, absolute_dds_path, texture_type, dds_format, progress_callback
        )
    
    def get_export_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a texture export task."""
        return self.texture_processor.get_conversion_status(task_id)
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get overall texture export queue status."""
        return self.texture_processor.get_queue_status()

# --- Node Analysis ---

class NodeAnalyzer:
    """Analyzes Blender node trees to extract material information."""
    
    @staticmethod
    def find_principled_bsdf(material: bpy.types.Material) -> Optional[bpy.types.Node]:
        """Find the Principled BSDF node in a material."""
        if not material.use_nodes or not material.node_tree:
            return None
        
        for node in material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                return node
        return None
    
    @staticmethod
    def find_aperture_node_group(material: bpy.types.Material) -> Optional[bpy.types.Node]:
        """Find the Aperture node group (Opaque or Translucent) in a material."""
        if not material.use_nodes or not material.node_tree:
            return None
        
        for node in material.node_tree.nodes:
            if node.type == 'GROUP' and node.node_tree:
                if "Aperture" in node.node_tree.name:
                    return node
        return None
    
    @staticmethod
    def find_connected_image_node(socket: bpy.types.NodeSocket) -> Optional[bpy.types.Node]:
        """Find the connected image texture node for a socket."""
        if not socket or not socket.is_linked:
            return None
        
        node = socket.links[0].from_node
        
        # Follow links upstream to find TEX_IMAGE node
        while node and node.type != 'TEX_IMAGE':
            input_found = False
            for input_socket in node.inputs:
                if input_socket.is_linked:
                    node = input_socket.links[0].from_node
                    input_found = True
                    break
            if not input_found:
                break
        
        return node if node and node.type == 'TEX_IMAGE' else None

# --- Material Exporter ---

class MaterialExporter:
    """Main material export coordinator."""
    
    def __init__(self, project_root: str, sublayer_path: str):
        self.project_root = project_root
        self.sublayer_path = sublayer_path
        self.texture_exporter = TextureExporter(project_root, sublayer_path)
        self.mdl_manager = MDLManager()
        self.path_manager = MaterialPathManager()
        self.shader_setup = ShaderSetup()
        self.node_analyzer = NodeAnalyzer()
    
    async def export_material_async(
        self,
        blender_material: bpy.types.Material,
        sublayer_stage: Usd.Stage,
        parent_mesh_path: Optional[Sdf.Path] = None,
        obj: Optional[bpy.types.Object] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Optional[Sdf.Path]:
        """Export a material asynchronously."""
        if not blender_material or not USD_AVAILABLE:
            return None
        
        progress = ProgressTracker(6, progress_callback)
        progress.update(f"Exporting material: {blender_material.name}")
        
        # Ensure MDL files
        self.mdl_manager.ensure_mdl_files(self.project_root)
        progress.step("MDL files ready")
        
        # Create material path
        if parent_mesh_path and sublayer_stage.GetPrimAtPath(parent_mesh_path):
            mat_path, material_prim, shader_prim = self.path_manager.create_nvidia_style_material_path(
                sublayer_stage, parent_mesh_path, obj
            )
        else:
            mat_path, material_prim, shader_prim = self.path_manager.create_standard_material_path(
                sublayer_stage, blender_material.name
            )
        
        progress.step("Material path created")
        
        # Setup shader with detected material type
        material_type = self.shader_setup.detect_material_type(blender_material)
        self.shader_setup.setup_material_connections(material_prim, shader_prim)
        self.shader_setup.setup_shader_attributes(shader_prim, material_type)
        progress.step("Shader setup complete")
        
        # Find main shader node (Aperture node group or Principled BSDF)
        main_node = self.node_analyzer.find_aperture_node_group(blender_material)
        if not main_node:
            main_node = self.node_analyzer.find_principled_bsdf(blender_material)
        
        if not main_node:
            progress.step("No shader node found, using defaults")
            return mat_path
        
        # Export textures based on material type
        print(f"DEBUG: About to export textures with material_type: {material_type}")
        await self._export_material_textures(main_node, shader_prim, progress, material_type)
        
        progress.step("Material export complete")
        return mat_path
    
    async def _export_material_textures(
        self,
        shader_node: bpy.types.Node,
        shader_prim: Usd.Prim,
        progress: ProgressTracker,
        material_type: str = "AperturePBR_Opacity"
    ):
        """Export textures for a material."""
        print(f"DEBUG: _export_material_textures called with material_type: {material_type}")
        if material_type == "AperturePBR_Translucent":
            # Translucent material texture mappings
            texture_mappings = [
                ('Transmittance/Diffuse Albedo', 'inputs:transmittance_texture', 'inputs:transmittance_color', 'base color'),
                ('Normal Map', 'inputs:normalmap_texture', None, 'normal'),
            ]
            
            # Also handle constant values for translucent materials
            constant_mappings = [
                ('IOR', 'inputs:ior_constant'),
                ('Thin Walled', 'inputs:thin_walled'),
                ('Thin Wall Thickness', 'inputs:thin_wall_thickness'),
                ('Use Diffuse Layer', 'inputs:use_diffuse_layer'),
                ('Transmittance Measurement Distance', 'inputs:transmittance_measurement_distance'),
                ('Enable Emission', 'inputs:enable_emission'),
                ('Emissive Color', 'inputs:emissive_color'),
                ('Emissive Intensity', 'inputs:emissive_intensity'),
            ]
        else:
            # Opaque material texture mappings (existing)
            texture_mappings = [
                ('Base Color', 'inputs:diffuse_texture', 'inputs:diffuse_color_constant', 'base color'),
                ('Metallic', 'inputs:metallic_texture', 'inputs:metallic_constant', 'metallic'),
                ('Roughness', 'inputs:reflectionroughness_texture', 'inputs:reflection_roughness_constant', 'roughness'),
                ('Normal', 'inputs:normalmap_texture', None, 'normal'),
                # More need to be added but this is a good start
            ]
            constant_mappings = []
        
        # Process texture mappings
        for socket_name, texture_attr, constant_attr, texture_type in texture_mappings:
            if progress.is_cancelled():
                break
            
            socket = shader_node.inputs.get(socket_name)
            if not socket:
                continue
            
            image_node = self.node_analyzer.find_connected_image_node(socket)
            
            if image_node and image_node.image:
                # Export texture
                def progress_cb(msg):
                    progress.update(f"Processing {texture_type}: {msg}")
                
                texture_path = await self.texture_exporter.export_texture_async(
                    image_node.image, texture_type, progress_cb
                )
                
                if texture_path:
                    shader_prim.CreateAttribute(texture_attr, Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(texture_path))
                    if socket_name in ['Normal', 'Normal Map']:
                        shader_prim.CreateAttribute("inputs:encoding", Sdf.ValueTypeNames.Int).Set(2)
            
            elif constant_attr and socket:
                # Set constant value
                value = socket.default_value
                if socket_name in ['Base Color', 'Transmittance/Diffuse Albedo', 'Emissive Color']:
                    if hasattr(value, '__len__') and len(value) >= 3:
                        shader_prim.CreateAttribute(constant_attr, Sdf.ValueTypeNames.Color3f).Set(
                            Gf.Vec3f(value[0], value[1], value[2])
                        )
                else:
                    shader_prim.CreateAttribute(constant_attr, Sdf.ValueTypeNames.Float).Set(float(value))
        
        # Process constant-only mappings for translucent materials
        if material_type == "AperturePBR_Translucent":
            for socket_name, usd_attr in constant_mappings:
                if progress.is_cancelled():
                    break
                
                socket = shader_node.inputs.get(socket_name)
                if socket:
                    value = socket.default_value
                    if socket_name in ['Enable Emission', 'Thin Walled', 'Use Diffuse Layer']:
                        # Boolean values
                        shader_prim.CreateAttribute(usd_attr, Sdf.ValueTypeNames.Bool).Set(bool(value))
                    elif socket_name == 'Emissive Color':
                        # Color value
                        if hasattr(value, '__len__') and len(value) >= 3:
                            shader_prim.CreateAttribute(usd_attr, Sdf.ValueTypeNames.Color3f).Set(
                                Gf.Vec3f(value[0], value[1], value[2])
                            )
                    else:
                        # Float values
                        shader_prim.CreateAttribute(usd_attr, Sdf.ValueTypeNames.Float).Set(float(value))
    
    def export_material_sync(
        self,
        blender_material: bpy.types.Material,
        sublayer_stage: Usd.Stage,
        parent_mesh_path: Optional[Sdf.Path] = None,
        obj: Optional[bpy.types.Object] = None
    ) -> Optional[Sdf.Path]:
        """Export a material synchronously (for compatibility)."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                self.export_material_async(blender_material, sublayer_stage, parent_mesh_path, obj)
            )
        except Exception as e:
            print(f"Error in sync material export: {e}")
            return None
        finally:
            loop.close()

# --- Light Export ---

class LightExporter:
    """Handles light export operations."""
    
    def __init__(self, project_root: str):
        self.project_root = project_root
    
    def export_light(
        self,
        obj: bpy.types.Object,
        sublayer_stage: Usd.Stage,
        context: bpy.types.Context,
        anchor_path: Optional[Sdf.Path] = None
    ) -> bool:
        """Export a Blender light to USD."""
        if not obj or obj.type != 'LIGHT' or not USD_AVAILABLE:
            return False
        
        bl_light = obj.data
        remix_export_scale = context.scene.remix_export_scale
        
        # Determine light type and attributes
        light_info = self._analyze_light(bl_light)
        if not light_info:
            return False
        
        usd_light_type, light_attrs, shaping_attrs, extent = light_info
        
        # Create light prim
        light_prim_path = self._create_light_path(obj, sublayer_stage, anchor_path)
        light_prim = sublayer_stage.OverridePrim(light_prim_path)
        
        if not UsdGeom.Xform(light_prim).GetPrim().IsDefined():
            UsdGeom.Xform.Define(sublayer_stage, light_prim_path)
        
        light_prim.SetTypeName(usd_light_type)
        
        # Set light attributes
        self._set_light_attributes(light_prim, light_attrs, shaping_attrs, extent)
        
        # Set transform
        self._set_light_transform(light_prim, obj, remix_export_scale)
        
        return True
    
    def _analyze_light(self, bl_light: bpy.types.Light) -> Optional[Tuple[str, Dict, Dict, Any]]:
        """Analyze Blender light and return USD light info."""
        light_attrs = {
            "inputs:color": Gf.Vec3f(bl_light.color[:]),
            "inputs:intensity": float(bl_light.energy),
            "inputs:enableColorTemperature": False
        }
        
        shaping_attrs = {}
        extent = None
        
        if bl_light.type == 'POINT':
            usd_light_type = "SphereLight"
            radius = bl_light.shadow_soft_size if bl_light.shadow_soft_size > 0 else 1.0
            light_attrs["inputs:radius"] = float(radius)
            shaping_attrs.update({
                "shaping:cone:angle": 180.0,
                "shaping:cone:softness": 0.0,
                "shaping:focus": 0.0
            })
            default_extent = 5.0
            extent = Vt.Vec3fArray([Gf.Vec3f(-default_extent), Gf.Vec3f(default_extent)])
            
        elif bl_light.type == 'SUN':
            usd_light_type = "DistantLight"
            light_attrs["inputs:angle"] = float(bl_light.angle * 0.5 * 180.0 / 3.14159265)
            
        elif bl_light.type == 'SPOT':
            usd_light_type = "SphereLight"
            radius = bl_light.shadow_soft_size if bl_light.shadow_soft_size > 0 else 1.0
            light_attrs["inputs:radius"] = float(radius)
            shaping_attrs.update({
                "shaping:cone:angle": float(bl_light.spot_size * 0.5 * 180.0 / 3.14159265),
                "shaping:cone:softness": float(bl_light.spot_blend)
            })
            default_extent = 5.0
            extent = Vt.Vec3fArray([Gf.Vec3f(-default_extent), Gf.Vec3f(default_extent)])
            
        elif bl_light.type == 'AREA':
            if bl_light.shape in ['SQUARE', 'RECTANGLE']:
                usd_light_type = "RectLight"
                light_attrs["inputs:width"] = float(bl_light.size)
                light_attrs["inputs:height"] = float(bl_light.size_y if bl_light.shape == 'RECTANGLE' else bl_light.size)
                half_w, half_h = light_attrs["inputs:width"]/2.0, light_attrs["inputs:height"]/2.0
                extent = Vt.Vec3fArray([Gf.Vec3f(-half_w, -half_h, 0), Gf.Vec3f(half_w, half_h, 0)])
            elif bl_light.shape in ['DISK', 'ELLIPSE']:
                usd_light_type = "DiskLight"
                light_attrs["inputs:radius"] = float(bl_light.size * 0.5)
                r = light_attrs["inputs:radius"]
                extent = Vt.Vec3fArray([Gf.Vec3f(-r, -r, 0), Gf.Vec3f(r, r, 0)])
            else:
                return None
        else:
            return None
        
        return usd_light_type, light_attrs, shaping_attrs, extent
    
    def _create_light_path(
        self,
        obj: bpy.types.Object,
        sublayer_stage: Usd.Stage,
        anchor_path: Optional[Sdf.Path]
    ) -> Sdf.Path:
        """Create the USD path for the light."""
        light_base_name = sanitize_prim_name(obj.name)
        light_name_sanitized = generate_uuid_name(light_base_name, prefix="light_")
        
        if anchor_path:
            parent_prim_path = anchor_path
        else:
            parent_prim_path = Sdf.Path("/RootNode/remix_assets")
            sublayer_stage.DefinePrim(parent_prim_path, "Xform")
        
        return parent_prim_path.AppendPath(light_name_sanitized)
    
    def _set_light_attributes(
        self,
        light_prim: Usd.Prim,
        light_attrs: Dict,
        shaping_attrs: Dict,
        extent: Any
    ):
        """Set light attributes on the USD prim."""
        for attr_name, value in light_attrs.items():
            if isinstance(value, Gf.Vec3f):
                light_prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Color3f).Set(value)
            elif isinstance(value, float):
                light_prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Float).Set(value)
            elif isinstance(value, bool):
                light_prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Bool).Set(value)
        
        for attr_name, value in shaping_attrs.items():
            light_prim.CreateAttribute(attr_name, Sdf.ValueTypeNames.Float).Set(float(value))
        
        if extent:
            light_prim.CreateAttribute("extent", Sdf.ValueTypeNames.Float3Array).Set(extent)
    
    def _set_light_transform(
        self,
        light_prim: Usd.Prim,
        obj: bpy.types.Object,
        scale: float
    ):
        """Set the light transform."""
        import mathutils
        
        # Get object transform
        matrix = obj.matrix_world.copy()
        
        # Apply scale
        scale_matrix = mathutils.Matrix.Scale(scale, 4)
        matrix = scale_matrix @ matrix
        
        # Convert to USD transform
        loc, rot, scale_vec = matrix.decompose()
        
        # Set transform attributes
        light_prim.CreateAttribute("xformOp:translate", Sdf.ValueTypeNames.Double3).Set(
            Gf.Vec3d(loc.x, loc.y, loc.z)
        )
        light_prim.CreateAttribute("xformOp:rotateXYZ", Sdf.ValueTypeNames.Double3).Set(
            Gf.Vec3d(*rot.to_euler())
        )
        light_prim.CreateAttribute("xformOp:scale", Sdf.ValueTypeNames.Double3).Set(
            Gf.Vec3d(scale_vec.x, scale_vec.y, scale_vec.z)
        )
        
        # Set xform op order
        light_prim.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set([
            "xformOp:translate", "xformOp:rotateXYZ", "xformOp:scale"
        ]) 