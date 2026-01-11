import bpy
import os

try:
    from pxr import Usd, Sdf, UsdGeom, UsdLux
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False
    Usd = None
    Sdf = None
    UsdGeom = None
    UsdLux = None


class ScanExportedAssets(bpy.types.Operator):
    """Scan mod.usda and sublayers to find all exported meshes and lights"""
    bl_idname = "remix.scan_exported_assets"
    bl_label = "Scan Exported Assets"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE and context.scene.remix_mod_file_path

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        
        if not os.path.exists(mod_file_path):
            self.report({'ERROR'}, f"Mod file not found: {mod_file_path}")
            return {'CANCELLED'}
        
        project_dir = os.path.dirname(mod_file_path)
        print(f"\nScanning exported assets in: {mod_file_path}")
        
        # Store assets as a list of dicts: {name, type, sublayer_path, in_scene, prim_path}
        exported_assets = []
        
        # Get all Blender objects for comparison
        blender_mesh_names = {obj.name for obj in context.scene.objects if obj.type == 'MESH'}
        blender_light_names = {obj.name for obj in context.scene.objects if obj.type == 'LIGHT'}
        
        def scan_stage_for_assets(stage_path, sublayer_rel_path):
            """Scan a USD layer for mesh and light references defined in it"""
            try:
                if not os.path.exists(stage_path):
                    print(f"  Skipping non-existent file: {stage_path}")
                    return
                
                # Open as a layer (not stage) to avoid composition
                layer = Sdf.Layer.FindOrOpen(stage_path)
                if not layer:
                    print(f"  Could not open layer: {stage_path}")
                    return
                
                # But also need stage for type checking
                stage = Usd.Stage.Open(stage_path)
                if not stage:
                    print(f"  Could not open stage: {stage_path}")
                    return
                
                print(f"  Scanning: {sublayer_rel_path}")
                
                # Look for mesh references in /RootNode/meshes
                meshes_path = Sdf.Path("/RootNode/meshes")
                meshes_prim = stage.GetPrimAtPath(meshes_path)
                
                if meshes_prim and meshes_prim.IsValid():
                    for child in meshes_prim.GetChildren():
                        # Look for reference prims (these are the Xform refs like ref_xxx)
                        for ref_child in child.GetChildren():
                            # Check if this prim is actually defined in this layer (not inherited)
                            ref_prim_path = ref_child.GetPath()
                            if not layer.GetPrimAtPath(ref_prim_path):
                                # Prim not in this layer, skip it
                                continue
                            
                            if ref_child.HasAuthoredReferences():
                                refs = ref_child.GetMetadata('references')
                                if refs:
                                    for ref in refs.prependedItems:
                                        asset_path = ref.assetPath
                                        if asset_path:
                                            # Extract mesh name from reference path
                                            mesh_file = os.path.basename(asset_path)
                                            mesh_name = os.path.splitext(mesh_file)[0]
                                            
                                            # Also try to get the mesh name from the XForms hierarchy
                                            # Look for the actual mesh prim under XForms
                                            xforms_path = ref_child.GetPath().AppendPath("XForms")
                                            xforms_prim = stage.GetPrimAtPath(xforms_path)
                                            if xforms_prim and xforms_prim.IsValid():
                                                xforms_children = list(xforms_prim.GetChildren())
                                                if xforms_children:
                                                    # Use the name from the XForms child (the actual mesh name)
                                                    actual_mesh_name = xforms_children[0].GetName()
                                                    if actual_mesh_name:
                                                        mesh_name = actual_mesh_name
                                            
                                            # Check if this mesh exists in Blender scene
                                            # Try exact match first, then handle Blender's naming conventions
                                            in_scene = False
                                            if mesh_name in blender_mesh_names:
                                                in_scene = True
                                            else:
                                                # Blender uses dots (.) while USD uses underscores (_)
                                                # Convert USD name to Blender format: "Plane_001" -> "Plane.001"
                                                mesh_name_blender_format = mesh_name.replace('_', '.')
                                                
                                                for bl_name in blender_mesh_names:
                                                    # Direct match with converted name
                                                    if bl_name == mesh_name_blender_format:
                                                        in_scene = True
                                                        break
                                                    # Match base name (e.g., "Plane" matches "Plane.001")
                                                    if bl_name.startswith(mesh_name + "."):
                                                        in_scene = True
                                                        break
                                                    # Match with underscore to dot conversion
                                                    if '.' in bl_name and '_' in mesh_name:
                                                        # Extract base and number
                                                        bl_parts = bl_name.rsplit('.', 1)
                                                        mesh_parts = mesh_name.rsplit('_', 1)
                                                        if len(bl_parts) == 2 and len(mesh_parts) == 2:
                                                            if bl_parts[0] == mesh_parts[0] and bl_parts[1] == mesh_parts[1]:
                                                                in_scene = True
                                                                break
                                            
                                            # Get the full reference prim path (not the parent)
                                            prim_path = str(ref_child.GetPath())
                                            
                                            exported_assets.append({
                                                'name': mesh_name,
                                                'type': 'MESH',
                                                'sublayer': sublayer_rel_path,
                                                'in_scene': in_scene,
                                                'prim_path': prim_path,
                                                'asset_path': asset_path
                                            })
                                            
                                            status = "✓ In Scene" if in_scene else "✗ Not in Scene"
                                            print(f"    Found mesh: {mesh_name} at {prim_path} ({status})")
                
                # Look for lights throughout the hierarchy (not just /RootNode/lights)
                # Helper function to recursively find lights
                def find_lights_recursive(prim):
                    """Recursively search for light prims"""
                    # Check if this is a light by checking its type name
                    prim_type = prim.GetTypeName()
                    is_light = prim_type in ['SphereLight', 'RectLight', 'DiskLight', 'CylinderLight', 'DistantLight', 'DomeLight']
                    
                    if is_light:
                        # Check if this prim is actually defined in this layer (not inherited)
                        light_prim_path = prim.GetPath()
                        if layer.GetPrimAtPath(light_prim_path):
                            light_name = prim.GetName()
                            
                            # Check if this light exists in Blender scene
                            # Handle both exact name match and name_uuid format
                            in_scene = False
                            if light_name in blender_light_names:
                                in_scene = True
                            else:
                                # Check if light name starts with any Blender light name (for name_uuid format)
                                for bl_name in blender_light_names:
                                    # Handle format like "Point_a1b2c3d4" matching "Point"
                                    if light_name.startswith(bl_name + "_"):
                                        in_scene = True
                                        break
                                    # Handle Blender's dot naming like "Point.001"
                                    if '.' in bl_name:
                                        bl_base = bl_name.split('.')[0]
                                        if light_name.startswith(bl_base + "_"):
                                            in_scene = True
                                            break
                            
                            prim_path = str(prim.GetPath())
                            
                            exported_assets.append({
                                'name': light_name,
                                'type': 'LIGHT',
                                'sublayer': sublayer_rel_path,
                                'in_scene': in_scene,
                                'prim_path': prim_path,
                                'asset_path': ''
                            })
                            
                            status = "✓ In Scene" if in_scene else "✗ Not in Scene"
                            print(f"    Found light: {light_name} ({prim_type}) at {prim_path} ({status})")
                    
                    # Recurse into children
                    for child in prim.GetChildren():
                        find_lights_recursive(child)
                
                # Start recursive search from RootNode
                root_prim = stage.GetPrimAtPath("/RootNode")
                if root_prim and root_prim.IsValid():
                    find_lights_recursive(root_prim)
                
            except Exception as e:
                print(f"  Error scanning {stage_path}: {e}")
        
        # Scan main mod.usda
        scan_stage_for_assets(mod_file_path, "mod.usda")
        
        # Scan all sublayers
        sublayers_ordered = context.scene.get("_remix_sublayers_ordered", [])
        for sublayer_data in sublayers_ordered:
            if hasattr(sublayer_data, 'get') and callable(sublayer_data.get):
                full_path = sublayer_data.get('full_path', '')
                display_name = sublayer_data.get('display_name', '')
            elif len(sublayer_data) >= 3:
                full_path, display_name = sublayer_data[0], sublayer_data[1]
            else:
                continue
            
            if full_path and full_path != mod_file_path:
                scan_stage_for_assets(full_path, display_name)
        
        # Store results in scene
        context.scene["_remix_exported_assets"] = exported_assets
        
        total_count = len(exported_assets)
        mesh_count = sum(1 for a in exported_assets if a['type'] == 'MESH')
        light_count = sum(1 for a in exported_assets if a['type'] == 'LIGHT')
        in_scene_count = sum(1 for a in exported_assets if a['in_scene'])
        not_in_scene_count = total_count - in_scene_count
        
        print(f"\nScan complete:")
        print(f"  Total assets: {total_count} ({mesh_count} meshes, {light_count} lights)")
        print(f"  In scene: {in_scene_count}")
        print(f"  Not in scene: {not_in_scene_count}")
        
        self.report({'INFO'}, f"Found {total_count} assets ({in_scene_count} in scene, {not_in_scene_count} not in scene)")
        return {'FINISHED'}


class DeleteExportedAsset(bpy.types.Operator):
    """Delete an exported asset from the USD sublayer"""
    bl_idname = "remix.delete_exported_asset"
    bl_label = "Delete Exported Asset"
    bl_options = {'REGISTER', 'UNDO'}
    
    asset_index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE and context.scene.remix_mod_file_path

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        exported_assets = context.scene.get("_remix_exported_assets", [])
        
        if self.asset_index < 0 or self.asset_index >= len(exported_assets):
            self.report({'ERROR'}, "Invalid asset index")
            return {'CANCELLED'}
        
        asset = exported_assets[self.asset_index]
        asset_name = asset['name']
        asset_type = asset['type']
        sublayer_name = asset['sublayer']
        prim_path = asset['prim_path']
        
        print(f"\nDeleting {asset_type.lower()}: {asset_name} from {sublayer_name}")
        print(f"  Prim path: {prim_path}")
        
        # Find the sublayer file path
        mod_file_path = bpy.path.abspath(context.scene.remix_mod_file_path)
        project_dir = os.path.dirname(mod_file_path)
        
        # Determine which file to edit
        if sublayer_name == "mod.usda":
            target_file = mod_file_path
        else:
            # Find the sublayer
            sublayers_ordered = context.scene.get("_remix_sublayers_ordered", [])
            target_file = None
            for sublayer_data in sublayers_ordered:
                if hasattr(sublayer_data, 'get') and callable(sublayer_data.get):
                    full_path = sublayer_data.get('full_path', '')
                    display_name = sublayer_data.get('display_name', '')
                elif len(sublayer_data) >= 3:
                    full_path, display_name = sublayer_data[0], sublayer_data[1]
                else:
                    continue
                
                if display_name == sublayer_name:
                    target_file = full_path
                    break
            
            if not target_file:
                self.report({'ERROR'}, f"Could not find sublayer file: {sublayer_name}")
                return {'CANCELLED'}
        
        if not os.path.exists(target_file):
            self.report({'ERROR'}, f"File not found: {target_file}")
            return {'CANCELLED'}
        
        print(f"  Target file: {target_file}")
        
        try:
            # Open the layer directly (not as a composed stage)
            layer = Sdf.Layer.FindOrOpen(target_file)
            if not layer:
                self.report({'ERROR'}, f"Could not open layer: {target_file}")
                return {'CANCELLED'}
            
            prim_path_sdf = Sdf.Path(prim_path)
            
            # Check if the prim spec exists in this layer
            prim_spec = layer.GetPrimAtPath(prim_path_sdf)
            if not prim_spec:
                print(f"  Prim not found directly at: {prim_path}")
                print(f"  Searching layer hierarchy...")
                
                # Helper function to recursively print prim hierarchy
                def print_hierarchy(spec, indent=0):
                    print("  " + "  " * indent + f"- {spec.path}")
                    if hasattr(spec, 'nameChildren'):
                        for child_name in spec.nameChildren.keys():
                            child = spec.nameChildren[child_name]
                            print_hierarchy(child, indent + 1)
                
                # Print the entire hierarchy
                for root_prim in layer.rootPrims:
                    print_hierarchy(root_prim)
                
                self.report({'ERROR'}, f"Prim not found in layer: {prim_path}")
                return {'CANCELLED'}
            
            # Remove the prim spec from the layer using Sdf API
            with Sdf.ChangeBlock():
                parent_path = prim_path_sdf.GetParentPath()
                parent_spec = layer.GetPrimAtPath(parent_path)
                
                if parent_spec:
                    # Remove the child prim spec
                    del parent_spec.nameChildren[prim_path_sdf.name]
                    print(f"  Removed {asset_type.lower()} prim: {prim_path}")
                else:
                    self.report({'ERROR'}, f"Parent prim not found: {parent_path}")
                    return {'CANCELLED'}
            
            # Save the layer
            layer.Save()
            
            self.report({'INFO'}, f"Removed {asset_type.lower()} reference: {asset_name}")
            
            # Rescan assets
            bpy.ops.remix.scan_exported_assets()
            
            return {'FINISHED'}
                
        except Exception as e:
            self.report({'ERROR'}, f"Error deleting asset: {e}")
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


def register():
    bpy.utils.register_class(ScanExportedAssets)
    bpy.utils.register_class(DeleteExportedAsset)


def unregister():
    bpy.utils.unregister_class(DeleteExportedAsset)
    bpy.utils.unregister_class(ScanExportedAssets)
