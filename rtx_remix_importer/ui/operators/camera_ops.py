import bpy
import mathutils

class AlignViewToCamera(bpy.types.Operator):
    """Align the 3D viewport to the selected imported camera"""
    bl_idname = "remix.align_view_to_camera"
    bl_label = "Align View to Imported Camera"
    bl_options = {'REGISTER', 'UNDO'}

    camera_name: bpy.props.StringProperty(
        name="Camera Name",
        description="The name of the camera object to align the view to"
    )

    @classmethod
    def poll(cls, context):
        # Check if there is a 3D viewport
        return context.space_data and context.space_data.type == 'VIEW_3D'

    def execute(self, context):
        if not self.camera_name:
            self.report({'WARNING'}, "No camera name specified.")
            return {'CANCELLED'}

        target_camera = context.scene.objects.get(self.camera_name)

        if not target_camera or target_camera.type != 'CAMERA':
            self.report({'WARNING'}, f"Could not find the specified camera: '{self.camera_name}'")
            return {'CANCELLED'}
        
        # Get the viewport region
        rv3d = context.space_data.region_3d

        if rv3d is None:
            self.report({'WARNING'}, "Active space is not a 3D View.")
            return {'CANCELLED'}

        # Ensure the viewport is in perspective mode to match the camera
        rv3d.view_perspective = 'PERSP'

        # Decompose the camera's matrix to get location and rotation
        cam_matrix = target_camera.matrix_world.copy()
        cam_location = cam_matrix.to_translation()
        cam_rotation_quat = cam_matrix.to_quaternion()

        # Set the viewport's rotation to match the camera
        rv3d.view_rotation = cam_rotation_quat

        # Set a small distance for "maximum zoom" to increase fly/walk precision.
        # This is the key to making the view feel "zoomed in".
        rv3d.view_distance = 1.0

        # The pivot point (view_location) must be calculated to place the viewport
        # at the camera's location, given the new view_distance.
        forward_vector = cam_rotation_quat @ mathutils.Vector((0.0, 0.0, -1.0))
        rv3d.view_location = cam_location + forward_vector * rv3d.view_distance

        self.report({'INFO'}, f"Aligned viewport to camera: {target_camera.name}")

        return {'FINISHED'}

def register():
    bpy.utils.register_class(AlignViewToCamera)

def unregister():
    bpy.utils.unregister_class(AlignViewToCamera) 