import bpy
import mathutils

class AlignViewToCamera(bpy.types.Operator):
    """Align the 3D viewport to the selected imported camera"""
    bl_idname = "remix.align_view_to_camera"
    bl_label = "Align View to Imported Camera"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # Check if there is a 3D viewport
        return context.space_data and context.space_data.type == 'VIEW_3D'

    def execute(self, context):
        last_camera_name = context.scene.get("remix_last_imported_camera", "")

        if not last_camera_name:
            self.report({'WARNING'}, "No recently imported camera found. Import a capture with a camera first.")
            return {'CANCELLED'}

        target_camera = context.scene.objects.get(last_camera_name)

        if not target_camera or target_camera.type != 'CAMERA':
            self.report({'WARNING'}, f"Could not find the last imported camera: '{last_camera_name}'")
            return {'CANCELLED'}
        
        # Get the viewport region
        region = context.region
        rv3d = context.space_data.region_3d

        if rv3d is None:
            self.report({'WARNING'}, "Active space is not a 3D View.")
            return {'CANCELLED'}

        # Get the camera's world matrix
        cam_matrix = target_camera.matrix_world.copy()

        # Calculate the new view matrix for the viewport
        # The viewport camera looks down its -Z axis.
        # We want the viewport to be "at" the camera's location, looking in the same direction.
        rv3d.view_matrix = cam_matrix.inverted()
        
        # Center the view on the camera's location
        rv3d.view_location = cam_matrix.to_translation()

        self.report({'INFO'}, f"Aligned viewport to camera: {target_camera.name}")

        return {'FINISHED'}

def register():
    bpy.utils.register_class(AlignViewToCamera)

def unregister():
    bpy.utils.unregister_class(AlignViewToCamera) 