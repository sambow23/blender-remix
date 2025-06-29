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
        if not NATIVE_MODULE_LOADED:
            self.report({'ERROR'}, "Native module is not loaded.")
            return {'CANCELLED'}

        print(f"Python: Passing filepath '{self.filepath}' to C++ module.")
        
        try:
            prim_data = remix_native.import_usd(self.filepath)
            
            print("\n--- Prims returned from C++ ---")
            if not prim_data:
                print("No geometric prims found.")
            else:
                for item in prim_data:
                    print(f"  Name: {item['name']}, Type: {item['type']}")
            print("-----------------------------\n")

            self.report({'INFO'}, f"Successfully processed USD. See console for details.")

        except Exception as e:
            self.report({'ERROR'}, f"C++ module failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


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