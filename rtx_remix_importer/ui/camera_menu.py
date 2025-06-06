import bpy

class UI_MT_RemixCameraMenu(bpy.types.Menu):
    bl_idname = "UI_MT_remix_camera_menu"
    bl_label = "Select Imported Camera"

    def draw(self, context):
        layout = self.layout
        
        # Find all cameras imported by the addon
        imported_cameras = [
            obj for obj in context.scene.objects 
            if obj.type == 'CAMERA' and 'is_remix_camera' in obj.data
        ]

        if not imported_cameras:
            layout.label(text="No imported cameras found", icon='INFO')
            return

        for cam in sorted(imported_cameras, key=lambda o: o.name):
            source_file = cam.data.get("remix_capture_source", "Unknown Capture")
            
            # Format the display name
            display_name = f"{cam.name} ({source_file})"
            
            # Add an operator to the menu for each camera
            op = layout.operator("remix.align_view_to_camera", text=display_name, icon='CAMERA_DATA')
            op.camera_name = cam.name

def register():
    bpy.utils.register_class(UI_MT_RemixCameraMenu)

def unregister():
    bpy.utils.unregister_class(UI_MT_RemixCameraMenu) 