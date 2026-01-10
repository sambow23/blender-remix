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
        row_apply_changes.operator(ApplyRemixModChanges.bl_idname, icon='FILE_TICK', text="Load mod.usda Changes")
        # --- End Button ---
        
        # --- Button to convert DDS to PNG ---
        row_convert_dds = box.row(align=True)
        row_convert_dds.operator("remix.convert_mod_dds_to_png", icon='IMAGE_DATA', text="Convert DDS to PNG")
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
            col.label(text="Sublayers (Lowest Depth First)", icon='COLLAPSEMENU')

            # Get the ordered list stored in the scene
            sublayers_ordered = scene.get("_remix_sublayers_ordered", [])

            if not sublayers_ordered:
                col.label(text=" (No sublayers found or project not loaded)", icon='ERROR')
            else:
                active_path = scene.remix_active_sublayer_path
                # Display sublayers in tree order with indentation
                for i, sublayer_data in enumerate(sublayers_ordered):
                    # Handle both dict format (new) and tuple formats (old) for compatibility
                    # ID properties return dict-like objects, so check for 'get' method instead of isinstance
                    if hasattr(sublayer_data, 'get') and callable(sublayer_data.get):
                        # New dict format from ID properties
                        # Note: ID properties may convert ints to strings, so we need to convert back
                        full_path = sublayer_data.get('full_path', '')
                        display_name = sublayer_data.get('display_name', '')
                        rel_path = sublayer_data.get('rel_path', '')
                        
                        # Safe type conversion - handle both string and int
                        depth_val = sublayer_data.get('depth', 0)
                        depth = int(depth_val) if depth_val is not None else 0
                        
                        has_children_val = sublayer_data.get('has_children', False)
                        # Convert string 'True'/'False' or bool to bool
                        if isinstance(has_children_val, str):
                            has_children = has_children_val.lower() in ('true', '1', 'yes')
                        else:
                            has_children = bool(has_children_val)
                    elif len(sublayer_data) == 5:
                        # Old 5-tuple format
                        full_path, display_name, rel_path, depth, has_children = sublayer_data
                    else:
                        # Really old 3-tuple format fallback
                        full_path, display_name, rel_path = sublayer_data[:3]
                        depth = 0
                        has_children = False
                    
                    row = col.row(align=True)
                    
                    # Add indentation based on depth (tree structure)
                    if depth > 0:
                        # Use smaller indentation factor to prevent excessive spacing
                        row.separator(factor=depth * 0.5)  # 0.5 units per depth level
                        # Add tree branch icon
                        row.label(text="", icon='FORWARD')
                    
                    # Determine icon based on active state
                    if full_path == active_path:
                        icon = 'RADIOBUT_ON'
                    else:
                        icon = 'RADIOBUT_OFF'
                    
                    # Show tree icon + name with tooltip showing full relative path
                    button_text = f"{display_name}"
                    
                    # Calculate relative path from mod directory for tooltip
                    import os
                    mod_file_path = bpy.path.abspath(scene.remix_mod_file_path)
                    project_dir = os.path.dirname(mod_file_path)
                    try:
                        rel_path_from_mod = os.path.relpath(full_path, project_dir).replace('\\', '/')
                    except (ValueError, TypeError):
                        rel_path_from_mod = full_path
                    
                    # Create operator button - Blender will show the full path in status bar on hover
                    op_row = row.row()
                    op_row.alert = False
                    # Set the row's tooltip/description by adding path info to button
                    op = op_row.operator(SetTargetSublayer.bl_idname, text=button_text, icon=icon, emboss=True)
                    op.sublayer_path = full_path
                    
                    # Show relative path as subtle text on the same row
                    path_label = row.row()
                    path_label.scale_x = 0.6  # Make it smaller
                    path_label.enabled = False  # Gray it out
                    path_label.label(text=f"({rel_path_from_mod})")
                    
                    # Add folder icon at the end if has children
                    if has_children:
                        row.label(text="", icon='OUTLINER_OB_GROUP_INSTANCE')
                    
                    # Add depth level number at the end (right side) for all layers
                    depth_label = row.row()
                    depth_label.alignment = 'RIGHT'
                    depth_label.label(text=f"Depth: {depth}")

            # --- Anchoring & Export --- 
            col.separator() 

        # --- Export Section --- 
        if scene.remix_mod_file_path: # Only show if a project is loaded
            layout.separator()
            
            # --- Material Export Button ---
            row_material = layout.row()
            row_material.scale_y = 1.5  # Make it slightly larger
            row_material.enabled = bool(scene.remix_active_sublayer_path)
            material_op = row_material.operator("export_scene.rtx_remix_asset", text="Export Selected Materials", icon='MATERIAL')
            material_op.material_replacement_mode = True
            
            # --- Mesh/Light Export Button ---
            row_mesh = layout.row()
            row_mesh.scale_y = 1.5  # Make it slightly larger
            row_mesh.enabled = bool(scene.remix_active_sublayer_path)
            mesh_op = row_mesh.operator("export_scene.rtx_remix_asset", text="Export Selected Meshes/Lights", icon='EXPORT')
            mesh_op.material_replacement_mode = False
            
            if not scene.remix_active_sublayer_path:
                # Show warning if no sublayer is selected
                warning_row = layout.row()
                warning_row.label(text="Select a layer to export", icon='INFO')

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
            # Add Hide Original Mesh option
            row_hide_original = box_export_settings.row()
            row_hide_original.prop(scene, "remix_hide_original_mesh")
            # Add Anchor selection to the Export settings
            row_anchor = box_export_settings.row()
            row_anchor.prop(scene, "remix_anchor_object_target")