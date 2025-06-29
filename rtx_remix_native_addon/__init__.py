bl_info = {
    "name": "RTX Remix Native Toolkit",
    "author": "You",
    "version": (0, 1, 0),
    "blender": (4, 1, 0),
    "description": "A native C++ powered addon for the RTX Remix workflow.",
    "category": "Import-Export",
}

import bpy
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty

try:
    # This is where we import our compiled C++ module.
    # The .so file must be in the same directory as this __init__.py
    from . import remix_native
    NATIVE_MODULE_LOADED = True
except ImportError as e:
    print("Failed to import native module:", e)
    NATIVE_MODULE_LOADED = False

# --- Blender Operator ---
class REMIX_OT_import_usd(bpy.types.Operator, ImportHelper):
    """Import a USD file using the native C++ core"""
    bl_idname = "remix_native.import_usd"
    bl_label = "Import USD (Native)"

    filter_glob: StringProperty(
        default="*.usd;*.usda;*.usdc",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        if NATIVE_MODULE_LOADED:
            print(f"Importing {self.filepath} with native module...")
            # Call the C++ function
            meshes_data = remix_native.import_usd(self.filepath)
            
            if not meshes_data:
                self.report({'WARNING'}, "Native importer returned no mesh data.")
                return {'CANCELLED'}

            print(f"Native module returned data for {len(meshes_data)} meshes.")

            # Create Blender objects
            for mesh_data in meshes_data:
                self.create_blender_mesh(mesh_data)
        else:
            self.report({'ERROR'}, "Native module is not loaded.")
            return {'CANCELLED'}
        
        return {'FINISHED'}

    def create_blender_mesh(self, mesh_data):
        """Creates a Blender mesh object from the data returned by C++."""
        name = mesh_data['name']
        
        # Process vertices
        flat_verts = mesh_data['vertices']
        vertices = [tuple(flat_verts[i:i+3]) for i in range(0, len(flat_verts), 3)]

        # Process faces
        face_indices = mesh_data['face_vertex_indices']
        face_counts = mesh_data['face_vertex_counts']
        
        faces = []
        current_index = 0
        for count in face_counts:
            faces.append(tuple(face_indices[current_index : current_index + count]))
            current_index += count

        # Create mesh and object
        mesh = bpy.data.meshes.new(name=name)
        obj = bpy.data.objects.new(name, mesh)

        print(f"Creating mesh '{name}' with {len(vertices)} vertices and {len(faces)} faces.")

        # Populate mesh with data
        mesh.from_pydata(vertices, [], faces)
        mesh.update()

        # Link object to scene
        bpy.context.collection.objects.link(obj)

# --- Blender UI Panel ---
class REMIX_PT_native_panel(bpy.types.Panel):
    bl_label = "RTX Remix Native"
    bl_idname = "REMIX_PT_native_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'RTX Remix'

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        
        if NATIVE_MODULE_LOADED:
            col.operator(REMIX_OT_import_usd.bl_idname, icon='IMPORT')
        else:
            col.label(text="Native module failed to load.", icon='ERROR')


# --- Registration ---
classes = (
    REMIX_OT_import_usd,
    REMIX_PT_native_panel,
)

def register():
    print("Registering RTX Remix Native Toolkit...")
    if NATIVE_MODULE_LOADED:
        print("Native module loaded successfully!")
        # Call our C++ test function
        remix_native.hello()
    else:
        print("WARNING: Native module could not be loaded. Addon will have limited functionality.")
    
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    print("Unregistering RTX Remix Native Toolkit.")
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register() 