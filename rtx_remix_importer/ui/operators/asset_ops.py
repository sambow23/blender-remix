import bpy

try:
    from pxr import Usd
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

# --- Operator to Select an Object by Name ---
class SelectObjectByName(bpy.types.Operator):
    """Select an object by name"""
    bl_idname = "object.select_by_name"
    bl_label = "Select Object"
    bl_options = {'REGISTER', 'UNDO'}
    
    object_name: bpy.props.StringProperty(
        name="Object Name",
        description="Name of the object to select",
    )
    
    def execute(self, context):
        # Deselect all objects first
        for obj in context.selected_objects:
            obj.select_set(False)
        
        # Select the target object
        obj = bpy.data.objects.get(self.object_name)
        if obj:
            obj.select_set(True)
            context.view_layer.objects.active = obj
            self.report({'INFO'}, f"Selected {self.object_name}")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, f"Object '{self.object_name}' not found")
            return {'CANCELLED'}

# --- Operator to Invalidate a Single Asset ---
class InvalidateRemixSingleAsset(bpy.types.Operator):
    """Invalidate a single RTX Remix asset for reprocessing"""
    bl_idname = "object.rtx_remix_invalidate_single_asset"
    bl_label = "Invalidate Single Asset"
    bl_options = {'REGISTER', 'UNDO'}
    
    object_name: bpy.props.StringProperty(
        name="Object Name",
        description="Name of the object to invalidate",
    )
    
    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE
    
    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if not obj:
            self.report({'WARNING'}, f"Object '{self.object_name}' not found")
            return {'CANCELLED'}
            
        if "remix_processed" in obj:
            obj["remix_processed"] = False
            if "remix_material_path" in obj:
                del obj["remix_material_path"]
            self.report({'INFO'}, f"Invalidated asset: {self.object_name}")
            
            # Refresh the UI
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
                    
            return {'FINISHED'}
        else:
            self.report({'INFO'}, f"Object '{self.object_name}' is not a processed asset")
            return {'CANCELLED'} 