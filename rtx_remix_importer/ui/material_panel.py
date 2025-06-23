import bpy
from bpy.types import Panel

class PT_RemixMaterialPanel(Panel):
    """Panel for RTX Remix material operations"""
    bl_label = "RTX Remix Materials"
    bl_idname = "PT_remix_material_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        # Show panel when we have an active object with materials
        return (context.active_object is not None and 
                len(context.active_object.material_slots) > 0)
    
    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        
        if not obj:
            layout.label(text="No active object")
            return
        
        if not obj.material_slots:
            layout.label(text="No materials on this object")
            return
        
        active_material = obj.active_material
        
        if not active_material:
            layout.label(text="No active material")
            return
        
        # Material info section
        box = layout.box()
        box.label(text=f"Active Material: {active_material.name}", icon='MATERIAL')
        
        # Check if material uses Aperture node groups
        uses_aperture = False
        aperture_type = "Unknown"
        
        if active_material.use_nodes:
            for node in active_material.node_tree.nodes:
                if node.type == 'GROUP' and node.node_tree:
                    if "Aperture Opaque" in node.node_tree.name:
                        uses_aperture = True
                        aperture_type = "Aperture Opaque"
                        break
                    elif "Aperture Translucent" in node.node_tree.name:
                        uses_aperture = True
                        aperture_type = "Aperture Translucent"
                        break
        
        if uses_aperture:
            box.label(text=f"Type: {aperture_type}", icon='CHECKMARK')
        else:
            box.label(text="Type: Standard Blender Material", icon='DOT')
        
        # Conversion operators section
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Convert Material:", icon='NODETREE')
        
        # Quick conversion buttons
        row = col.row(align=True)
        row.operator("material.convert_to_aperture_opaque", 
                    text="To Opaque", 
                    icon='SHADING_SOLID')
        row.operator("material.convert_to_aperture_translucent", 
                    text="To Translucent", 
                    icon='SHADING_RENDERED')
        
        # Advanced options
        layout.separator()
        box = layout.box()
        box.label(text="Advanced Options:", icon='SETTINGS')
        
        # Main operator with options
        col = box.column(align=True)
        col.operator("material.create_aperture_node_group", 
                    text="Create/Update Aperture Node Group",
                    icon='ADD')
        
        # Info about what the operators do
        layout.separator()
        box = layout.box()
        box.label(text="About Aperture Materials:", icon='INFO')
        
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text="• Opaque: Standard PBR materials")
        col.label(text="• Translucent: Glass, liquids, transparent")
        col.label(text="• Preserves existing textures and connections")
        col.label(text="• Compatible with RTX Remix export")


class PT_RemixNodeGroupPanel(Panel):
    """Panel for managing Aperture node groups"""
    bl_label = "Aperture Node Groups"
    bl_idname = "PT_remix_node_group_panel"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    
    @classmethod
    def poll(cls, context):
        # Show in Node Editor when editing materials
        return (context.space_data.type == 'NODE_EDITOR' and
                context.space_data.tree_type == 'ShaderNodeTree')
    
    def draw(self, context):
        layout = self.layout
        
        # Creation buttons
        col = layout.column(align=True)
        col.label(text="Create Node Groups:", icon='ADD')
        
        col.operator("material.create_aperture_node_group", 
                    text="Create Aperture Opaque").node_group_type = 'OPAQUE'
        
        col.operator("material.create_aperture_node_group", 
                    text="Create Aperture Translucent").node_group_type = 'TRANSLUCENT'


# Operator properties panel for the create aperture node group operator
class PT_RemixMaterialOperatorPanel(Panel):
    """Properties panel for the create aperture node group operator"""
    bl_label = "Aperture Conversion Options"
    bl_idname = "PT_remix_material_operator_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_parent_id = "PT_remix_material_panel"
    bl_options = {'DEFAULT_CLOSED'}
    
    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and 
                context.active_object.active_material is not None)
    
    def draw(self, context):
        layout = self.layout
        
        # This would show the operator properties if we had a way to access them
        # For now, we'll just show some helpful information
        
        col = layout.column(align=True)
        col.label(text="Conversion Options:", icon='SETTINGS')
        
        box = layout.box()
        col = box.column(align=True)
        col.scale_y = 0.8
        col.label(text="Preserve Connections:")
        col.label(text="• Keeps existing texture links")
        col.label(text="• Maintains material values")
        col.label(text="• Maps to appropriate inputs")
        
        col.separator()
        col.label(text="Force Recreate:")
        col.label(text="• Rebuilds node group from scratch")
        col.label(text="• Updates to latest implementation")
        col.label(text="• Useful for fixing issues")
        
        # Quick access to advanced operator
        layout.separator()
        layout.operator("material.create_aperture_node_group", 
                       text="Advanced Conversion Options",
                       icon='PREFERENCES') 