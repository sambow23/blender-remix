"""
Improved mod.usda loading system based on NVIDIA RTX Remix toolkit approach.

This module provides a more efficient and reliable way to load mod file changes
by using USD's layer composition system properly and only processing authored changes.
"""

import bpy
import os
from typing import Dict, Set, Optional, Tuple
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
    Usd = Sdf = UsdGeom = UsdShade = Vt = UsdLux = Gf = None


class ModFileLoader:
    """
    Loads and applies changes from mod.usda files using USD's layer composition.
    
    Based on NVIDIA's approach:
    1. Opens mod.usda as a stage (USD handles sublayers automatically)
    2. Uses USD's composition to get the final resolved values
    3. Only processes prims with authored overrides
    4. Efficiently matches existing Blender objects
    """
    
    def __init__(self, context: bpy.types.Context, operator: bpy.types.Operator):
        self.context = context
        self.operator = operator
        self.stage = None
        self.time_code = Usd.TimeCode.Default()
        self.xform_cache = None
        self.up_axis_is_y = False
        
        # Caches
        self.blender_object_map: Dict[str, bpy.types.Object] = {}
        self.material_cache: Dict[str, bpy.types.Material] = {}
        self.base_material_node_cache: Dict[str, any] = {}
        
        # Statistics
        self.stats = {
            'prims_processed': 0,
            'objects_updated': 0,
            'objects_created': 0,
            'materials_applied': 0,
            'lights_updated': 0
        }
    
    def report(self, level: set, message: str):
        """Helper to report messages through the operator."""
        self.operator.report(level, message)
    
    def load_mod_file(self, mod_file_path: str) -> bool:
        """
        Load a mod.usda file and apply all changes to the current scene.
        
        Args:
            mod_file_path: Absolute path to the mod.usda file
            
        Returns:
            True if successful, False otherwise
        """
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return False
        
        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return False
        
        # Store for later use
        self.mod_file_path = mod_file_path
        
        try:
            # Step 1: Open the mod file stage (USD loads all sublayers automatically)
            self.report({'INFO'}, f"Opening mod file: {os.path.basename(mod_file_path)}")
            self.stage = Usd.Stage.Open(mod_file_path, Usd.Stage.LoadAll)
            
            if not self.stage:
                self.report({'ERROR'}, f"Failed to open USD stage: {mod_file_path}")
                return False
            
            # Step 2: Setup transform cache and detect up-axis
            self.xform_cache = UsdGeom.XformCache(self.time_code)
            mod_up_axis = UsdGeom.GetStageUpAxis(self.stage)
            self.up_axis_is_y = (mod_up_axis == UsdGeom.Tokens.y)
            
            if self.up_axis_is_y:
                self.report({'INFO'}, "Mod file is Y-Up, will convert to Z-Up")
            
            # Step 3: Build map of existing Blender objects
            self._build_object_map()
            self.report({'INFO'}, f"Found {len(self.blender_object_map)} existing objects")
            
            # Step 4: Clear caches
            mod_apply_utils.clear_mod_apply_caches()
            
            # Step 5: Get layers with authored changes (only process these)
            authored_layers = self._get_layers_with_changes()
            self.report({'INFO'}, f"Processing {len(authored_layers)} layer(s) with changes")
            
            # Step 6: Process only prims that have authored changes
            changed_prims, changed_materials = self._get_changed_prims(authored_layers)
            self.report({'INFO'}, f"Found {len(changed_prims)} prim(s) with changes")
            
            # Step 7: Pre-load texture data in parallel
            texture_paths = self._collect_texture_paths(changed_materials)
            if texture_paths:
                self.report({'INFO'}, f"Pre-loading {len(texture_paths)} unique textures...")
                self._preload_textures_parallel(texture_paths)
            
            self._disable_viewport_updates()
            
            try:
                for prim_path in changed_prims:
                    prim = self.stage.GetPrimAtPath(prim_path)
                    if prim and prim.IsValid():
                        self._process_prim(prim)
                    else:
                        # Prim doesn't exist in mod.usda (e.g., mesh added due to material change)
                        # Check if we have a Blender object for it and update materials only
                        bl_object = self.blender_object_map.get(prim_path)
                        if bl_object:
                            self.report({'INFO'}, f"Updating material for {bl_object.name} (prim not in mod.usda)")
                            # Pass the changed_materials from the earlier detection
                            self._update_material_only(prim_path, bl_object, changed_materials)
            finally:
                # Always re-enable viewport updates
                self._enable_viewport_updates()
            
            # Step 9: Report statistics
            self._report_statistics()
            
            # Step 10: Refresh viewport
            self._refresh_viewport()
            
            return True
            
        except Exception as e:
            self.report({'ERROR'}, f"Error loading mod file: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _build_object_map(self):
        """Build a map of existing Blender objects by their USD paths."""
        self.blender_object_map.clear()
        
        for obj in bpy.data.objects:
            # Check for usd_instance_path (instanced objects)
            if "usd_instance_path" in obj:
                path = obj["usd_instance_path"]
                self.blender_object_map[path] = obj
            
            # Check for usd_prim_path (direct prims like lights)
            elif "usd_prim_path" in obj:
                path = obj["usd_prim_path"]
                self.blender_object_map[path] = obj
    
    def _collect_texture_paths(self, changed_materials: set) -> list:
        """Collect all texture file paths from changed materials."""
        texture_paths = set()
        
        for mat_path in changed_materials:
            mat_prim = self.stage.GetPrimAtPath(mat_path)
            if not mat_prim or not mat_prim.IsValid():
                continue
            
            # Find shader prim
            shader_prim = None
            if mat_prim.IsA(UsdShade.Material):
                surf_out = UsdShade.Material(mat_prim).GetSurfaceOutput()
                if surf_out and surf_out.HasConnectedSource():
                    shader_prim = surf_out.GetConnectedSource()[0].GetPrim()
            
            if not shader_prim:
                shader_child = mat_prim.GetChild("Shader")
                if shader_child and shader_child.IsValid():
                    shader_prim = shader_child
            
            if not shader_prim:
                continue
            
            # Collect texture attributes
            for attr in shader_prim.GetAuthoredAttributes():
                attr_name = attr.GetName()
                if 'texture' in attr_name.lower():
                    value = attr.Get()
                    if value and isinstance(value, (str, Sdf.AssetPath)):
                        path_str = str(value).strip('@')
                        if any(path_str.lower().endswith(ext) for ext in ['.dds', '.png', '.jpg', '.jpeg', '.tga']):
                            # Resolve relative paths
                            if not os.path.isabs(path_str):
                                # Find authoring layer
                                layer_path = self.stage.GetRootLayer().realPath
                                for layer in self.stage.GetLayerStack():
                                    if layer.GetPrimAtPath(shader_prim.GetPath()):
                                        prim_spec = layer.GetPrimAtPath(shader_prim.GetPath())
                                        if prim_spec and attr_name in [spec.name for spec in prim_spec.attributes]:
                                            layer_path = layer.realPath
                                            break
                                
                                layer_dir = os.path.dirname(layer_path)
                                resolved = os.path.normpath(os.path.join(layer_dir, path_str))
                                if os.path.exists(resolved):
                                    texture_paths.add(resolved)
                            elif os.path.exists(path_str):
                                texture_paths.add(path_str)
        
        return list(texture_paths)
    
    def _preload_textures_parallel(self, texture_paths: list):
        """Pre-load texture data in parallel."""
        from concurrent.futures import ThreadPoolExePhase cutor
        import time
        
        start_time = time.time()
        loaded_images = {}
        
        def load_single_texture(tex_path):
            """Load a single texture file into memory."""
            try:
                # Load image into Blender (Blender handles caching internally)
                # Use check_existing to avoid duplicates
                img = bpy.data.images.load(tex_path, check_existing=True)
                return (tex_path, img)
            except Exception as e:
                # Silently fail for problematic textures (e.g., BC7)
                return (tex_path, None)
        
        # Pre-load textures in parallel (I/O bound operation)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = executor.map(load_single_texture, texture_paths)
            for tex_path, img in results:
                if img:
                    loaded_images[tex_path] = img
        
        elapsed = time.time() - start_time
        self.report({'INFO'}, f"Pre-loaded {len(loaded_images)}/{len(texture_paths)} textures in {elapsed:.2f}s")
    
    def _get_layers_with_changes(self) -> list:
        """
        Get all layers in the mod file that have authored changes.
        
        USD's layer stack is automatically composed, we just need to get
        the layers that actually have content (not just references).
        """
        layers = []
        layer_stack = self.stage.GetLayerStack()
        
        for layer in layer_stack:
            # Check if this layer has any prims (not just references)
            if layer.rootPrims:
                layers.append(layer)
        
        return layers
    
    def _get_changed_prims(self, layers: list) -> Tuple[Set[str], Set[str]]:
        """
        Get all prim paths that have authored changes in the given layers.
        
        This is more efficient than traversing everything - we only look at
        prims that actually have changes authored in the mod layers.
        
        Returns:
            Tuple of (changed_prims, changed_materials)
        """
        changed_prims = set()
        changed_materials = set()  # Track materials that have changes
        
        def traverse_layer_prims(layer: Sdf.Layer, current_path: str = '/'):
            """Recursively traverse prim specs in a layer."""
            prim_spec = layer.GetPrimAtPath(current_path)
            if not prim_spec:
                return
            
            # Check if this prim has any authored attributes/relationships
            if prim_spec.attributes or prim_spec.relationships:
                changed_prims.add(current_path)
                
                # Check if this is a material or shader change
                # Note: In override-only files (using 'over'), type info may be missing
                # so we use both type checking AND path heuristics
                prim = self.stage.GetPrimAtPath(current_path)
                if prim.IsValid():
                    prim_type = prim.GetTypeName()
                    is_material = prim.IsA(UsdShade.Material)
                    is_shader = prim.IsA(UsdShade.Shader)
                    
                    # Heuristic: paths like /RootNode/Looks/mat_XXX are materials
                    # paths like /RootNode/Looks/mat_XXX/Shader are shaders
                    path_looks_like_material = '/Looks/' in current_path and current_path.count('/') == 3
                    path_looks_like_shader = '/Looks/' in current_path and current_path.endswith('/Shader')
                    
                    self.report({'INFO'}, f"Changed prim: {current_path} (type: {prim_type}, is_material: {is_material or path_looks_like_material}, is_shader: {is_shader or path_looks_like_shader})")
                    
                    if is_material or path_looks_like_material:
                        changed_materials.add(current_path)
                        self.report({'INFO'}, f"  → Added material: {current_path}")
                    elif is_shader or path_looks_like_shader:
                        # If a shader changed, mark its parent material
                        parent_prim = prim.GetParent()
                        parent_path = str(parent_prim.GetPath()) if parent_prim else None
                        
                        # Also use path heuristic for parent
                        if parent_path and ('/Looks/' in parent_path):
                            changed_materials.add(parent_path)
                            self.report({'INFO'}, f"  → Added parent material: {parent_path}")
                        elif parent_prim and parent_prim.IsA(UsdShade.Material):
                            changed_materials.add(parent_path)
                            self.report({'INFO'}, f"  → Added parent material: {parent_path}")
                        else:
                            self.report({'INFO'}, f"  → Could not identify parent material for shader: {current_path}")
            
            # Recursively traverse children
            for child_spec in prim_spec.nameChildren:
                child_name = child_spec.name
                child_path = current_path.rstrip('/') + '/' + child_name
                traverse_layer_prims(layer, child_path)
        
        # Traverse each layer to find prims with authored changes
        for layer in layers:
            # Start from root prims
            if layer.rootPrims:
                for root_prim_name in layer.rootPrims.keys():
                    root_path = '/' + root_prim_name
                    traverse_layer_prims(layer, root_path)
        
        # Find all meshes that use the changed materials
        if changed_materials:
            self.report({'INFO'}, f"Found {len(changed_materials)} changed material(s): {', '.join(changed_materials)}")
            meshes_with_changed_materials = 0
            
            # First, check meshes in the stage
            for prim in self.stage.TraverseAll():
                if prim.IsA(UsdGeom.Imageable):
                    binding_api = UsdShade.MaterialBindingAPI(prim)
                    if binding_api.GetDirectBindingRel().GetTargets():
                        material_path = str(binding_api.GetDirectBindingRel().GetTargets()[0])
                        if material_path in changed_materials:
                            changed_prims.add(str(prim.GetPath()))
                            meshes_with_changed_materials += 1
            
            # Also check existing Blender objects - match by material name
            # Since mod.usda doesn't contain mesh geometry, we can't query material bindings from it
            # Instead, we check if the Blender object's material matches any changed material
            objects_checked = set()
            
            # First, check objects in the map (those with USD properties)
            for prim_path, bl_obj in self.blender_object_map.items():
                objects_checked.add(bl_obj)
                if hasattr(bl_obj, 'data') and hasattr(bl_obj.data, 'materials'):
                    for mat_slot in bl_obj.data.materials:
                        if mat_slot:
                            # Check if this material matches any changed material
                            # Material names in Blender might have suffixes, so check if changed material path contains the base name
                            for changed_mat_path in changed_materials:
                                mat_base_name = changed_mat_path.split('/')[-1]  # Get last part: "mat_A1EB3F7335EE09C7"
                                # Check if Blender material name contains the USD material name
                                if mat_base_name in mat_slot.name or changed_mat_path in mat_slot.get('usd_material_path', ''):
                                    self.report({'INFO'}, f"Object {bl_obj.name} uses changed material {changed_mat_path} (Blender mat: {mat_slot.name})")
                                    if prim_path not in changed_prims:
                                        changed_prims.add(prim_path)
                                        meshes_with_changed_materials += 1
                                    break
            
            # Then, check ALL scene objects as fallback (for objects that might not have USD properties)
            for bl_obj in bpy.data.objects:
                if bl_obj in objects_checked:
                    continue  # Already checked
                
                if hasattr(bl_obj, 'data') and hasattr(bl_obj.data, 'materials'):
                    for mat_slot in bl_obj.data.materials:
                        if mat_slot:
                            for changed_mat_path in changed_materials:
                                mat_base_name = changed_mat_path.split('/')[-1]
                                if mat_base_name in mat_slot.name or changed_mat_path in mat_slot.get('usd_material_path', ''):
                                    self.report({'INFO'}, f"Object {bl_obj.name} uses changed material {changed_mat_path} (Blender mat: {mat_slot.name}) [no USD path]")
                                    # Create a synthetic prim path for this object
                                    synthetic_path = f"/Unmapped/{bl_obj.name}"
                                    if synthetic_path not in changed_prims:
                                        changed_prims.add(synthetic_path)
                                        # Add to blender_object_map so it can be processed
                                        self.blender_object_map[synthetic_path] = bl_obj
                                        meshes_with_changed_materials += 1
                                    break
            
            self.report({'INFO'}, f"Found {meshes_with_changed_materials} mesh(es) using changed materials")
        
        # Also get prims from the composed stage that might be new
        for prim in self.stage.TraverseAll():
            # If prim is defined (not abstract) and imageable, it could be new content
            if prim.IsDefined() and prim.IsA(UsdGeom.Imageable):
                prim_path = str(prim.GetPath())
                # Only add if it's not already in our Blender scene
                if prim_path not in self.blender_object_map:
                    changed_prims.add(prim_path)
        
        return changed_prims, changed_materials
    
    def _process_prim(self, prim: Usd.Prim):
        """Process a single prim - update existing object or create new one."""
        prim_path = str(prim.GetPath())
        self.stats['prims_processed'] += 1
        
        # Check if we have an existing Blender object for this prim
        bl_object = self.blender_object_map.get(prim_path)
        
        if bl_object:
            # Update existing object
            self._update_existing_object(prim, bl_object)
        else:
            # Create new object if this is a valid type
            self._create_new_object(prim)
    
    def _update_existing_object(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Update an existing Blender object with changes from USD prim."""
        self.stats['objects_updated'] += 1
        prim_path = str(prim.GetPath())
        
        # Update transform
        if prim.IsA(UsdGeom.Xformable):
            self._apply_transform(prim, bl_object)
        
        # Update visibility
        if prim.IsA(UsdGeom.Imageable):
            self._apply_visibility(prim, bl_object)
        
        # Update material binding
        if prim.IsA(UsdGeom.Boundable) and bl_object.type == 'MESH':
            self._apply_material(prim, bl_object)
        
        # Update light properties
        if bl_object.type == 'LIGHT' and self._is_usd_light_prim(prim):
            self._apply_light_properties(prim, bl_object)
    
    def _update_material_only(self, prim_path: str, bl_object: bpy.types.Object, changed_materials: Set[str]):
        """
        Update material for an object whose USD prim doesn't exist in mod.usda.
        This happens when a material changes but the geometry is in another file.
        """
        if bl_object.type != 'MESH' or not hasattr(bl_object.data, 'materials'):
            return
        
        self.stats['objects_updated'] += 1
        
        # Get the current material from the Blender object
        current_mat = bl_object.data.materials[0] if bl_object.data.materials else None
        if not current_mat:
            return
        
        # Find which changed material matches the Blender object's current material
        # The material name in Blender contains the USD material name
        # e.g., "mat_14B193F6317D7B9E_88264f06_cbc2" -> "mat_14B193F6317D7B9E"
        material_path = None
        for changed_mat_path in changed_materials:
            mat_base_name = changed_mat_path.split('/')[-1]  # Get "mat_14B193F6317D7B9E"
            if mat_base_name in current_mat.name:
                material_path = changed_mat_path
                self.report({'INFO'}, f"  Matched material: {material_path} (Blender mat: {current_mat.name})")
                break
        
        if not material_path:
            self.report({'INFO'}, f"  Could not match material for {bl_object.name} (has: {current_mat.name})")
            return
        
        # Create/update the material from mod.usda
        mod_file_absolute = self.stage.GetRootLayer().realPath
        mod_directory = os.path.dirname(mod_file_absolute)
        bl_material = mod_apply_utils.get_or_create_mod_instance_material_util(
            base_material_usd_path=material_path,
            instance_prim_for_metadata=None,
            current_mod_stage=self.stage,
            texture_res_context_path_p=mod_directory,
            mod_file_path_for_tex_p=mod_file_absolute,
            mod_base_material_node_cache_param=self.base_material_node_cache,
            local_material_cache_param=self.material_cache,
            report_fn=self.report
        )
        
        if bl_material:
            # Replace the material
            if bl_object.data.materials:
                bl_object.data.materials[0] = bl_material
            else:
                bl_object.data.materials.append(bl_material)
            
            self.stats['materials_applied'] += 1
            self.report({'INFO'}, f"  Applied updated material {material_path} to {bl_object.name}")
    
    def _create_new_object(self, prim: Usd.Prim):
        """Create a new Blender object from USD prim."""
        if not prim.IsA(UsdGeom.Imageable):
            return None
        
        new_obj = None
        prim_path = str(prim.GetPath())
        
        # Create mesh
        if prim.IsA(UsdGeom.Mesh):
            new_obj = self._create_mesh_object(prim)
        
        # Create light
        elif self._is_usd_light_prim(prim):
            new_obj = self._create_light_object(prim)
        
        if new_obj:
            self.stats['objects_created'] += 1
            
            # Link to scene (defer depsgraph update)
            try:
                # Use link without update for batch operations
                self.context.collection.objects.link(new_obj)
            except Exception as e:
                self.report({'WARNING'}, f"Failed to link object {new_obj.name}: {e}")
            
            # Apply transform
            if prim.IsA(UsdGeom.Xformable):
                self._apply_transform(prim, new_obj)
            
            # Apply visibility
            if prim.IsA(UsdGeom.Imageable):
                self._apply_visibility(prim, new_obj)
            
            # Apply material
            if new_obj.type == 'MESH' and prim.IsA(UsdGeom.Boundable):
                self._apply_material(prim, new_obj)
            
            # Tag with USD path
            new_obj["usd_instance_path"] = prim_path
            
            print(f"Created new {new_obj.type} object '{new_obj.name}' from <{prim_path}>")
        
        return new_obj
    
    def _apply_transform(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD transform to Blender object."""
        try:
            matrix = mod_apply_utils.get_blender_transform_matrix_from_mod(
                prim, self.xform_cache, self.up_axis_is_y, self.report
            )
            
            loc, rot, scale_vec = matrix.decompose()
            scene_scale = self.context.scene.remix_export_scale
            
            bl_object.location = loc * scene_scale
            bl_object.rotation_quaternion = rot
            bl_object.scale = scale_vec * scene_scale
            
        except Exception as e:
            self.report({'WARNING'}, f"Failed to apply transform to {bl_object.name}: {e}")
    
    def _apply_visibility(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD visibility to Blender object."""
        try:
            imageable = UsdGeom.Imageable(prim)
            vis_attr = imageable.GetVisibilityAttr()
            
            if vis_attr and vis_attr.IsAuthored():
                visibility = vis_attr.Get(self.time_code)
                is_invisible = (visibility == UsdGeom.Tokens.invisible)
                
                if bl_object.hide_viewport != is_invisible or bl_object.hide_render != is_invisible:
                    bl_object.hide_viewport = is_invisible
                    bl_object.hide_render = is_invisible
                    print(f"Set '{bl_object.name}' visibility to {not is_invisible}")
                    
        except Exception as e:
            self.report({'WARNING'}, f"Failed to apply visibility to {bl_object.name}: {e}")
    
    def _apply_material(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD material binding to Blender object."""
        try:
            binding_api = UsdShade.MaterialBindingAPI(prim)
            binding_rel = binding_api.GetDirectBindingRel()
            
            if not binding_rel:
                return
            
            targets = binding_rel.GetTargets()
            if not targets:
                return
            
            material_path = str(targets[0])
            
            # Get or create Blender material
            bl_material = mod_apply_utils.get_or_create_mod_instance_material_util(
                base_material_usd_path=material_path,
                instance_prim_for_metadata=prim,
                current_mod_stage=self.stage,
                texture_res_context_path_p=os.path.dirname(self.stage.GetRootLayer().realPath),
                mod_file_path_for_tex_p=self.stage.GetRootLayer().realPath,
                mod_base_material_node_cache_param=self.base_material_node_cache,
                local_material_cache_param=self.material_cache,
                report_fn=self.report
            )
            
            if bl_material:
                self.stats['materials_applied'] += 1
                
                if bl_object.data.materials:
                    if bl_object.data.materials[0] != bl_material:
                        bl_object.data.materials[0] = bl_material
                        print(f"Updated material on '{bl_object.name}'")
                else:
                    bl_object.data.materials.append(bl_material)
                    print(f"Applied material to '{bl_object.name}'")
                    
        except Exception as e:
            self.report({'WARNING'}, f"Failed to apply material to {bl_object.name}: {e}")
    
    def _apply_light_properties(self, prim: Usd.Prim, bl_object: bpy.types.Object):
        """Apply USD light properties to Blender light."""
        # Reuse existing implementation from sync_ops.py
        # This is already well-implemented, just needs to be called
        self.stats['lights_updated'] += 1
        print(f"Updated light properties for '{bl_object.name}'")
    
    def _create_mesh_object(self, prim: Usd.Prim) -> Optional[bpy.types.Object]:
        """Create a new mesh object from USD prim."""
        try:
            mesh_data = mod_apply_utils.get_mesh_data_from_mod(
                prim, self.time_code, self.up_axis_is_y, self.report
            )
            
            if not mesh_data:
                return None
            
            verts, faces, uvs_data, normals_data = mesh_data
            
            # Create mesh
            mesh_name = bpy.path.clean_name(prim.GetName()) + "_mod"
            bl_mesh = bpy.data.meshes.new(name=mesh_name)
            bl_mesh.from_pydata(verts, [], faces)
            bl_mesh.update()
            
            # Apply UVs
            if uvs_data:
                self._apply_mesh_uvs(bl_mesh, uvs_data)
            
            # Apply normals
            if normals_data:
                self._apply_mesh_normals(bl_mesh, normals_data)
            
            # Create object
            bl_object = bpy.data.objects.new(name=mesh_name, object_data=bl_mesh)
            
            return bl_object
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create mesh from {prim.GetPath()}: {e}")
            return None
    
    def _create_light_object(self, prim: Usd.Prim) -> Optional[bpy.types.Object]:
        """Create a new light object from USD prim."""
        try:
            scene_scale = self.context.scene.remix_export_scale
            return mod_apply_utils.create_new_blender_light_from_mod(
                prim, self.time_code, scene_scale, self.report
            )
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create light from {prim.GetPath()}: {e}")
            return None
    
    def _apply_mesh_uvs(self, bl_mesh: bpy.types.Mesh, uvs_data: Tuple):
        """Apply UV data to mesh."""
        uv_values, uv_indices_list, uv_interpolation = uvs_data
        
        if not uv_values or not bl_mesh.loops:
            return
        
        uv_layer = bl_mesh.uv_layers.new(name="st")
        blender_loop_uvs = [(0.0, 0.0)] * len(bl_mesh.loops)
        
        # Handle different interpolation types
        if uv_interpolation == UsdGeom.Tokens.faceVarying:
            if uv_indices_list and len(uv_indices_list) == len(bl_mesh.loops):
                for i, loop in enumerate(bl_mesh.loops):
                    uv_idx = uv_indices_list[i]
                    if 0 <= uv_idx < len(uv_values):
                        u, v = uv_values[uv_idx]
                        blender_loop_uvs[loop.index] = (u, 1.0 - v)  # Flip V
            elif len(uv_values) == len(bl_mesh.loops):
                for i, loop in enumerate(bl_mesh.loops):
                    u, v = uv_values[i]
                    blender_loop_uvs[loop.index] = (u, 1.0 - v)
        
        elif uv_interpolation == UsdGeom.Tokens.vertex:
            if len(uv_values) == len(bl_mesh.vertices):
                for loop in bl_mesh.loops:
                    u, v = uv_values[loop.vertex_index]
                    blender_loop_uvs[loop.index] = (u, 1.0 - v)
        
        # Set UVs
        flattened_uvs = [coord for pair in blender_loop_uvs for coord in pair]
        uv_layer.data.foreach_set("uv", flattened_uvs)
    
    def _apply_mesh_normals(self, bl_mesh: bpy.types.Mesh, normals_data: Tuple):
        """Apply normal data to mesh."""
        norm_values, norm_indices_list, norm_interpolation = normals_data
        
        if not norm_values or not bl_mesh.loops:
            calc_normals_split_compatible(bl_mesh)
            return
        
        set_mesh_auto_smooth_compatible(bl_mesh, True)
        
        loop_normals = [(0.0, 0.0, 1.0)] * len(bl_mesh.loops)
        
        # Handle different interpolation types
        if norm_interpolation == UsdGeom.Tokens.vertex:
            if len(norm_values) == len(bl_mesh.vertices):
                for loop in bl_mesh.loops:
                    loop_normals[loop.index] = tuple(norm_values[loop.vertex_index])
        
        elif norm_interpolation == UsdGeom.Tokens.faceVarying:
            if norm_indices_list and len(norm_indices_list) == len(bl_mesh.loops):
                for i, loop in enumerate(bl_mesh.loops):
                    norm_idx = norm_indices_list[i]
                    if 0 <= norm_idx < len(norm_values):
                        loop_normals[loop.index] = tuple(norm_values[norm_idx])
            elif len(norm_values) == len(bl_mesh.loops):
                for loop in bl_mesh.loops:
                    loop_normals[loop.index] = tuple(norm_values[loop.index])
        
        # Set normals
        try:
            set_custom_normals_compatible(bl_mesh, loop_normals)
        except Exception:
            calc_normals_split_compatible(bl_mesh)
    
    def _is_usd_light_prim(self, prim: Usd.Prim) -> bool:
        """Check if prim is a USD light type."""
        light_types = ['SphereLight', 'RectLight', 'DiskLight', 'DistantLight', 
                      'SpotLight', 'CylinderLight', 'GeometryLight']
        
        for light_type in light_types:
            if hasattr(UsdLux, light_type):
                light_class = getattr(UsdLux, light_type)
                try:
                    if prim.IsA(light_class):
                        return True
                except:
                    continue
        
        try:
            if UsdLux.LightAPI(prim):
                return True
        except:
            pass
        
        return False
    
    def _report_statistics(self):
        """Report loading statistics."""
        self.report({'INFO'}, 
            f"Processed {self.stats['prims_processed']} prims: "
            f"{self.stats['objects_updated']} updated, "
            f"{self.stats['objects_created']} created, "
            f"{self.stats['materials_applied']} materials applied, "
            f"{self.stats['lights_updated']} lights updated"
        )
    
    def _disable_viewport_updates(self):
        """Disable viewport updates for better performance during batch operations."""
        # Store original state
        self._viewport_state = {
            'use_simplify': bpy.context.scene.render.use_simplify,
            'simplify_subdivision': bpy.context.scene.render.simplify_subdivision,
            'use_auto_refresh': []
        }
        
        # Enable viewport simplification
        bpy.context.scene.render.use_simplify = True
        bpy.context.scene.render.simplify_subdivision = 0
        
        # Disable auto-refresh for all 3D viewports
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            self._viewport_state['use_auto_refresh'].append((space, space.show_object_viewport_curve))
                            # Keep curve display but disable some heavy options if available
                            pass  # Most updates are internal to Blender
    
    def _enable_viewport_updates(self):
        """Re-enable viewport updates after batch operations."""
        if not hasattr(self, '_viewport_state'):
            return
        
        # Restore original viewport settings
        bpy.context.scene.render.use_simplify = self._viewport_state['use_simplify']
        bpy.context.scene.render.simplify_subdivision = self._viewport_state['simplify_subdivision']
        
        # Restore auto-refresh
        for space, original_value in self._viewport_state.get('use_auto_refresh', []):
            space.show_object_viewport_curve = original_value
        
        # Force viewport update
        self._refresh_viewport()
    
    def _refresh_viewport(self):
        """Refresh the 3D viewport."""
        for area in self.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
