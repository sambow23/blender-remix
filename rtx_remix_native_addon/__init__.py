bl_info = {
    "name": "RTX Remix Native Toolkit",
    "author": "You",
    "version": (0, 1, 1),
    "blender": (4, 1, 0),
    "description": "A native C++ powered addon for the RTX Remix workflow.",
    "category": "Import-Export",
}

import bpy
import mathutils # Import mathutils for matrix operations
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
        mesh_path = mesh_data['mesh_path']
        object_name = mesh_data['name']

        # --- Check for existing mesh data using its unique path ---
        mesh = None
        for m in bpy.data.meshes:
            if m.get("usd_path") == mesh_path:
                mesh = m
                break
        
        if mesh:
            print(f"Reusing existing mesh data for path: {mesh_path}")
        else:
            # Generate a user-friendly name for the mesh data block
            # e.g., "inst_ABC" -> "mesh_ABC"
            mesh_name = object_name.replace("inst_", "mesh_")

            # Check if this user-friendly name already exists
            if mesh_name in bpy.data.meshes:
                mesh = bpy.data.meshes[mesh_name]
                # Still, double-check if the path matches, just in case of non-unique names
                if mesh.get("usd_path") != mesh_path:
                     mesh = bpy.data.meshes.new(name=mesh_name)
            else:
                mesh = bpy.data.meshes.new(name=mesh_name)

            mesh["usd_path"] = mesh_path # Store unique path in a custom property

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
            
            # Populate mesh with data
            mesh.from_pydata(vertices, [], faces)
            mesh.update()

            # --- Apply UVs ---
            if 'uvs' in mesh_data:
                self.apply_uvs(mesh, mesh_data)

        # Create a new object for this instance
        obj = bpy.data.objects.new(object_name, mesh)
        obj["usd_path"] = mesh_path

        # Apply the transform
        if 'transform' in mesh_data:
            flat_matrix = mesh_data['transform']
            matrix = mathutils.Matrix([
                flat_matrix[0:4],
                flat_matrix[4:8],
                flat_matrix[8:12],
                flat_matrix[12:16]
            ])
            # Transpose from USD's row-major to Blender's column-major
            matrix.transpose()
            obj.matrix_world = matrix

        # Link object to scene
        bpy.context.collection.objects.link(obj)

        # --- Assign Material ---
        if 'material' in mesh_data:
            mat_data = mesh_data['material']
            mat_path = mat_data['material_path']
            mat_name = mat_data['name']

            # Find existing material by its unique path property
            material = None
            if mat_name in bpy.data.materials:
                material = bpy.data.materials[mat_name]
                if material.get("usd_path") != mat_path:
                    # Name collision, but not the right material. Fallback to search.
                    material = None
            
            if not material:
                for mat in bpy.data.materials:
                    if mat.get("usd_path") == mat_path:
                        material = mat
                        break
            
            if not material:
                material = self.create_blender_material(mat_data)

            if material:
                obj.data.materials.append(material)

    def create_blender_material(self, material_data):
        """Creates a new Blender material with a node tree based on texture data."""
        mat_path = material_data['material_path']
        name = material_data['name'] # This is the clean name, e.g. "mat_..."
        print(f"Creating material '{name}' ({mat_path})...")
        
        material = bpy.data.materials.new(name=name)
        material["usd_path"] = mat_path # Store unique path in a custom property
        material.use_nodes = True
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        
        # Clear default nodes
        for node in nodes:
            nodes.remove(node)
            
        # Add Principled BSDF and Output nodes
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        output = nodes.new(type='ShaderNodeOutputMaterial')
        bsdf.location = (0, 0)
        output.location = (300, 0)
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

        # Create and connect texture nodes
        textures = material_data['textures']
        node_pos_y = 300
        for tex_type, tex_path in textures.items():
            print(f"  > Found texture '{tex_type}': {tex_path}")
            
            tex_node = nodes.new(type='ShaderNodeTexImage')
            tex_node.location = (-300, node_pos_y)
            
            try:
                tex_node.image = bpy.data.images.load(tex_path, check_existing=True)
            except Exception as e:
                print(f"    ! Could not load image: {e}")
                continue

            # Connect to BSDF based on type
            if tex_type == 'diffuseColor':
                links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
            elif tex_type == 'normal':
                normal_map_node = nodes.new(type='ShaderNodeNormalMap')
                normal_map_node.location = (-100, node_pos_y)
                links.new(tex_node.outputs['Color'], normal_map_node.inputs['Color'])
                links.new(normal_map_node.outputs['Normal'], bsdf.inputs['Normal'])
                tex_node.image.colorspace_settings.name = 'Non-Color'
            elif tex_type == 'roughness':
                links.new(tex_node.outputs['Color'], bsdf.inputs['Roughness'])
                tex_node.image.colorspace_settings.name = 'Non-Color'
            elif tex_type == 'metallic':
                links.new(tex_node.outputs['Color'], bsdf.inputs['Metallic'])
                tex_node.image.colorspace_settings.name = 'Non-Color'
            elif tex_type == 'emissive_color':
                 links.new(tex_node.outputs['Color'], bsdf.inputs['Emission'])
            
            node_pos_y -= 350
            
        return material

    def apply_uvs(self, mesh, mesh_data):
        """Applies UV data to a Blender mesh."""
        flat_uvs = mesh_data['uvs']
        uvs = [tuple(flat_uvs[i:i+2]) for i in range(0, len(flat_uvs), 2)]
        interpolation = mesh_data['uv_interpolation']
        
        uv_layer = mesh.uv_layers.new(name="UVMap")

        if interpolation == 'faceVarying':
            # This is the most common and direct case for Blender
            if len(uv_layer.data) == len(uvs):
                for i in range(len(uvs)):
                    uv_layer.data[i].uv = uvs[i]
            else:
                print(f"Warning: Mismatch between loop count ({len(uv_layer.data)}) and UV count ({len(uvs)}) for mesh '{mesh.name}'.")

        elif interpolation == 'vertex':
            # Less common for UVs, but possible
            if len(mesh.vertices) == len(uvs):
                # Map vertex UVs to mesh loops
                for loop in mesh.loops:
                    uv_layer.data[loop.index].uv = uvs[loop.vertex_index]
            else:
                 print(f"Warning: Mismatch between vertex count ({len(mesh.vertices)}) and UV count ({len(uvs)}) for mesh '{mesh.name}'.")
        else:
            print(f"Warning: Unsupported UV interpolation '{interpolation}' for mesh '{mesh.name}'.")

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
