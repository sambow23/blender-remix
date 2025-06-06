import bpy
from .operators.project_ops import *
from .operators.sync_ops import *

class PT_RemixProjectPanel(bpy.types.Panel):
    """Creates a Panel in the Scene properties window for Remix Project Management"""
    bl_label = "RTX Remix Project"
    bl_idname = "SCENE_PT_remix_project"
    # Change space and region for N-Panel (Sidebar)
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    # Add a category name for the tab in the N-Panel
    bl_category = "RTX Remix"
    bl_context = ""

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.label(text="Project Setup", icon='SETTINGS')
        row_file = box.row(align=True)
        row_file.prop(scene, "remix_mod_file_path")
        # Button to create a new mod file (on its own row for clarity)
        box.operator(CreateRemixModFile.bl_idname, icon='FILE_NEW')
        
        # Add game name field
        row_game = box.row(align=True)
        row_game.prop(scene, "remix_game_name")
        
        row_root = box.row()
        row_root.label(text="Project Root:")
        row_root.label(text=scene.remix_project_root_display)
        row_load = box.row(align=True)
        row_load.operator(LoadRemixProject.bl_idname, icon='FILE_REFRESH', text="Load Project")
        
        # --- Button to apply mod file changes ---
        row_apply_changes = box.row(align=True)
        row_apply_changes.operator(ApplyRemixModChanges.bl_idname, icon='FILE_TICK', text="Load mod.usda changes (EXPERIMENTAL)")
        # --- End Button ---
        
        # --- Sublayer Management --- 
        box_sublayers = layout.box()
        box_sublayers.label(text="Sublayer Management", icon='LINENUMBERS_ON')
        col = box_sublayers.column(align=True)
        row_create = col.row(align=True)
        row_create.enabled = bool(scene.remix_mod_file_path) # Enable only if mod file is set
        row_create.prop(scene, "remix_new_sublayer_name", text="")
        row_create.operator(CreateRemixSublayer.bl_idname, icon='ADD', text="Create")
        
        row_add = col.row(align=True)
        row_add.enabled = bool(scene.remix_mod_file_path)
        row_add.operator(AddRemixSublayer.bl_idname, icon='FILEBROWSER', text="Add Existing Sublayer")

        # --- Sublayer List & Export --- 
        if scene.remix_mod_file_path:
            box_export = layout.box()
            col = box_export.column()
            col.label(text="Sublayers (Strongest First)", icon='COLLAPSEMENU')

            # Get the ordered list stored in the scene
            sublayers_ordered = scene.get("_remix_sublayers_ordered", [])

            if not sublayers_ordered:
                col.label(text=" (No sublayers found or project not loaded)", icon='ERROR')
            else:
                active_path = scene.remix_active_sublayer_path
                # Display sublayers in order
                for i, (full_path, display_name, rel_path) in enumerate(sublayers_ordered):
                    row = col.row(align=True)
                    # Add indentation based on index? Or just a fixed indent?
                    row.separator(factor=1.0) # Add some indentation
                    
                    # Show Icon indicating if active
                    icon = 'CHECKBOX_HLT' if full_path == active_path else 'CHECKBOX_DEHLT'
                    
                    # Operator button to set this layer as active
                    op = row.operator(SetTargetSublayer.bl_idname, text=display_name, icon=icon)
                    op.sublayer_path = full_path 
                    
                    # Show relative path as well?
                    # row.label(text=f"({rel_path})") # Maybe too cluttered

            # --- Anchoring & Export --- 
            col.separator() 

        # --- Export Section --- 
        if scene.remix_mod_file_path: # Only show if a project is loaded
            layout.separator()
            
            # --- Material Exports Box ---
            box_material_export = layout.box()
            box_material_export.label(text="Material Exports", icon='MATERIAL')
            
            # Material Replacement Export to mod.usda
            row_material_mod = box_material_export.row()
            row_material_mod.enabled = bool(scene.remix_mod_file_path) # Only enable if project is loaded
            material_mod_op = row_material_mod.operator("export_scene.rtx_remix_mod_file", text="Export Material Replacement to mod.usda", icon='FILE_REFRESH')
            material_mod_op.material_replacement_mode = True
            
            # Material Export to Active Sublayer
            row_material_sublayer = box_material_export.row()
            row_material_sublayer.enabled = bool(scene.remix_active_sublayer_path) 
            material_sublayer_op = row_material_sublayer.operator("export_scene.rtx_remix_asset", text="Export Material Replacement to Active Sublayer", icon='MATERIAL')
            material_sublayer_op.material_replacement_mode = True
            
            # --- Mesh/Light Exports Box ---
            box_mesh_export = layout.box()
            box_mesh_export.label(text="Mesh & Light Exports", icon='MESH_DATA')
            
            # Hotload Export to mod.usda
            row_mesh_mod = box_mesh_export.row()
            row_mesh_mod.enabled = bool(scene.remix_mod_file_path) # Only enable if project is loaded
            hotload_op = row_mesh_mod.operator("export_scene.rtx_remix_mod_file", text="Export Selected to mod.usda (Hotload)", icon='FILE_REFRESH')
            hotload_op.material_replacement_mode = False  # Explicitly set to False to ensure full export
            
            # Mesh/Light Export to Active Sublayer
            row_mesh_sublayer = box_mesh_export.row()
            row_mesh_sublayer.enabled = bool(scene.remix_active_sublayer_path) 
            export_op = row_mesh_sublayer.operator("export_scene.rtx_remix_asset", text="Export Selected to Active Sublayer", icon='EXPORT')

        # --- Export Settings ---
        if scene.remix_mod_file_path: # Only show if a project is loaded
            box_export_settings = layout.box()
            box_export_settings.label(text="Export Settings", icon='EXPORT')
            row_scale = box_export_settings.row()
            row_scale.prop(scene, "remix_export_scale")
            # Add Auto Apply Transforms option
            row_transform = box_export_settings.row()
            row_transform.prop(scene, "remix_auto_apply_transforms")
            # Add Texture Reuse option
            row_texture_reuse = box_export_settings.row()
            row_texture_reuse.prop(scene, "remix_reuse_existing_textures")
            # Add Anchor selection to the Export settings
            row_anchor = box_export_settings.row()
            row_anchor.prop(scene, "remix_anchor_object_target") 