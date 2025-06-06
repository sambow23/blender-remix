import bpy
from .operators.asset_ops import *

class PT_RemixAssetProcessingPanel(bpy.types.Panel):
    """Panel for managing processed RTX Remix assets"""
    bl_label = "Exported Asset Processing"
    bl_idname = "SCENE_PT_remix_asset_processing"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_context = ""
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Add Invalidate button for selected objects
        box = layout.box()
        box.label(text="Asset Management", icon='FILE_REFRESH')
        row = box.row()
        row.operator("object.rtx_remix_invalidate_assets", icon='TRASH', text="Invalidate Selected Assets")
        
        # Display processed asset list
        box = layout.box()
        box.label(text="Processed Assets", icon='CHECKMARK')
        
        # Count how many objects are processed
        processed_count = 0
        for obj in bpy.data.objects:
            if obj.type == 'MESH' and "remix_processed" in obj and obj["remix_processed"]:
                processed_count += 1
        
        # Show count at the top
        row = box.row()
        row.label(text=f"Total: {processed_count} assets")
        
        # Show list of processed objects
        if processed_count > 0:
            box.separator()
            col = box.column()
            for obj in bpy.data.objects:
                if obj.type == 'MESH' and "remix_processed" in obj and obj["remix_processed"]:
                    row = col.row(align=True)
                    # Use select icon if selected, otherwise use regular object icon
                    icon = 'RESTRICT_SELECT_OFF' if obj.select_get() else 'OBJECT_DATA'
                    row.label(text=obj.name, icon=icon)
                    
                    # Add button to select this object
                    select_op = row.operator(SelectObjectByName.bl_idname, text="", icon='RESTRICT_SELECT_OFF')
                    select_op.object_name = obj.name
                    
                    # Add button to invalidate just this object
                    invalidate_op = row.operator(InvalidateRemixSingleAsset.bl_idname, text="", icon='TRASH')
                    invalidate_op.object_name = obj.name 