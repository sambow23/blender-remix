import bpy
from bpy.utils import previews
from .. import core_utils
from .operators.capture_ops import *
from .operators.utility_ops import *

# Global preview collection
preview_collections = {}

class PT_RemixCapturePanel(bpy.types.Panel):
    """Panel for managing RTX Remix capture imports"""
    bl_label = "Captures"
    bl_idname = "SCENE_PT_remix_capture"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_context = ""
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Capture Folder Selection
        box = layout.box()
        box.label(text="Capture Folder", icon='FOLDER_REDIRECT')
        row = box.row(align=True)
        row.prop(scene, "remix_capture_folder_path", text="")
        row.operator(ScanCaptureFolder.bl_idname, icon='FILE_REFRESH', text="Refresh")
        
        # Cache Management
        cache_row = box.row(align=True)
        cache_row.operator(ClearMaterialCache.bl_idname, icon='TRASH', text="Clear Cache")
        cache_row.operator(FixBrokenTextures.bl_idname, icon='TEXTURE', text="Fix Broken Textures")
        cache_row.scale_y = 0.8  # Make it smaller since it's a utility function
        
        # Import Settings
        if scene.remix_capture_folder_path:
            settings_box = layout.box()
            settings_box.label(text="Import Settings", icon='SETTINGS')
            
            col = settings_box.column(align=True)
            col.prop(scene, "remix_capture_scene_scale")
            col.prop(scene, "remix_capture_import_materials")
            col.prop(scene, "remix_capture_import_lights")
            
            # Advanced settings (collapsible)
            col.separator()
        
        # Available Captures List
        captures_box = layout.box()
        captures_box.label(text="Available Captures", icon='FILE_3D')
        
        # Get the scanned captures
        available_captures = scene.remix_captures
        
        if not available_captures:
            if scene.remix_capture_folder_path:
                captures_box.label(text="No captures found. Folder may be empty.", icon='INFO')
            else:
                captures_box.label(text="Select a capture folder first.", icon='ERROR')
        else:
            # Show count and controls
            header_row = captures_box.row(align=True)
            header_row.label(text=f"Found: {len(available_captures)} files")
            
            # Batch import button
            selected_count = len([c for c in available_captures if c.is_selected])
            if selected_count > 0:
                op = header_row.operator("remix.batch_import_selected_captures", text=f"Import {selected_count} Selected")
            else:
                op = header_row.operator("remix.batch_import_all_captures", text=f"Import All")

            header_row.operator(ClearCaptureList.bl_idname, icon='TRASH', text="Clear")
            
            captures_box.separator()

            # Align View button is now a menu
            # Check if there are any imported cameras to decide if the menu should be active
            imported_cameras_exist = any(
                obj.type == 'CAMERA' and 'is_remix_camera' in obj.data 
                for obj in context.scene.objects
            )
            
            row = captures_box.row()
            row.enabled = imported_cameras_exist
            row.menu("UI_MT_remix_camera_menu", text="Align View to Imported Camera", icon='CAMERA_DATA')
            
            captures_box.separator()

            # Draw the UIList
            captures_box.template_list(
                "REMIX_UL_capture_list", # UIList class name
                "",                      # propertyname of the list's data
                scene,                   # data pointer for the list
                "remix_captures",        # propertyname of the list's data
                scene,                   # data pointer for the active index
                "remix_captures_index",  # propertyname for the active index
                rows=10                  # Number of rows to display
            )

            # --- Thumbnail Preview ---
            active_capture_index = scene.remix_captures_index
            if 0 <= active_capture_index < len(available_captures):
                active_capture = available_captures[active_capture_index]
                
                # Get the preview image path
                pcoll = preview_collections["main"]
                
                if active_capture.full_path in pcoll:
                    preview_image = pcoll[active_capture.full_path]
                    
                    # Draw the preview
                    preview_box = captures_box.box()
                    preview_box.label(text="Preview:")
                    preview_box.template_icon(icon_value=preview_image.icon_id, scale=5)
                else:
                    # If preview not loaded yet, show a placeholder
                    preview_box = captures_box.box()
                    preview_box.label(text="Preview: (Generating...)")


def register_previews():
    # Create a new preview collection
    pcoll = previews.new()
    pcoll.my_previews_dir = ""
    preview_collections["main"] = pcoll

def unregister_previews():
    # Unload all preview collections
    for pcoll in preview_collections.values():
        previews.remove(pcoll)
    preview_collections.clear()

def load_thumbnail(capture_path):
    """Load a single thumbnail into the preview collection."""
    pcoll = preview_collections["main"]

    if capture_path and capture_path not in pcoll:
        thumb_path = core_utils.get_thumbnail_preview(capture_path)
        if thumb_path:
            pcoll.load(capture_path, thumb_path, 'IMAGE')

@bpy.app.handlers.persistent
def on_depsgraph_update(scene):
    """Check for active capture and load its thumbnail if needed."""
    if bpy.context.scene and hasattr(bpy.context.scene, "remix_captures"):
        captures = bpy.context.scene.remix_captures
        index = bpy.context.scene.remix_captures_index
        if 0 <= index < len(captures):
            capture = captures[index]
            load_thumbnail(capture.full_path)
