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
import os
import platform
import subprocess
import shutil
import concurrent.futures
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

        print("--- Starting Native USD Import ---")
        meshes_data = remix_native.import_usd(self.filepath)
        if not meshes_data:
            self.report({'WARNING'}, "Native importer returned no data.")
            return {'CANCELLED'}
        print(f"Native module returned data for {len(meshes_data)} meshes.")

        # --- Batch-convert all necessary textures using the native module ---
        self.batch_convert_textures_native(meshes_data, self.filepath)

        # --- Create Blender objects and materials ---
        print("--- Creating Blender scene ---")
        for mesh_data in meshes_data:
            self.create_blender_mesh(mesh_data)
        
        print("--- Native USD Import Finished ---")
        return {'FINISHED'}

    def batch_convert_textures_native(self, meshes_data, usd_file_path):
        """Finds all unique DDS files and calls the native module to convert them."""
        print("--- Collecting textures for conversion ---")
        dds_paths_to_convert = set()
        
        # Gather all unique texture paths
        for mesh_data in meshes_data:
            if 'material' in mesh_data:
                textures = mesh_data['material'].get('textures', {})
                for tex_path in textures.values():
                    if tex_path.lower().endswith(".dds"):
                        # Resolve the full, absolute path to the DDS file
                        full_path = self.resolve_texture_path(tex_path, usd_file_path)
                        if full_path and os.path.exists(full_path):
                            # Check if the PNG already exists in the cache
                            cached_png_path = self.get_cached_png_path(full_path)
                            if not os.path.exists(cached_png_path):
                                dds_paths_to_convert.add(full_path)

        if not dds_paths_to_convert:
            print("No new textures to convert.")
            return
        
        print(f"Found {len(dds_paths_to_convert)} unique textures to convert...")
        
        texconv_path = self.get_texconv_path()
        cache_dir = os.path.join(os.path.dirname(__file__), "texture_cache")
        os.makedirs(cache_dir, exist_ok=True)
        
        # Call the native C++ function to perform the conversion in parallel
        remix_native.batch_convert_textures(list(dds_paths_to_convert), texconv_path, cache_dir)

    def resolve_texture_path(self, tex_path, usd_file_path):
        """Robustly resolves a texture path from the USD file."""
        usd_dir = os.path.dirname(usd_file_path)
        texture_path_unix = tex_path.replace("\\\\", "/").replace("\\", "/")
        if texture_path_unix.startswith("../"):
            texture_path_unix = texture_path_unix[3:]
        return os.path.normpath(os.path.join(usd_dir, texture_path_unix))

    def get_cached_png_path(self, dds_path):
        """Calculates the final path for a cached PNG file from an absolute DDS path."""
        addon_dir = os.path.dirname(__file__)
        cache_dir = os.path.join(addon_dir, "texture_cache")
        original_dds_filename = os.path.basename(dds_path)
        png_filename = os.path.splitext(original_dds_filename)[0] + ".png"
        return os.path.join(cache_dir, png_filename)

    def get_texconv_path(self):
        """Gets the path to the texconv executable."""
        addon_dir = os.path.dirname(__file__)
        return os.path.join(addon_dir, "bin", "texconv.exe")

    def create_blender_mesh(self, mesh_data):
        """Creates a Blender mesh object from the data returned by C++."""
        mesh_definition_path = mesh_data['mesh_definition_path']
        instance_path = mesh_data['instance_path']
        instance_name = mesh_data['instance_name']

        # Construct the correct, clean mesh data-block name
        base_name = instance_name
        if '_' in instance_name:
            parts = instance_name.rsplit('_', 1)
            if parts[1].isdigit():
                base_name = parts[0] # Strips the _1, _2 suffix
        
        mesh_data_name = base_name.replace("inst_", "mesh_")

        # --- Check for existing mesh data BY NAME ---
        mesh = bpy.data.meshes.get(mesh_data_name)
        
        if mesh:
            print(f"Reusing existing mesh data: {mesh.name}")
        else:
            print(f"Creating new mesh data: {mesh_data_name}")
            mesh = bpy.data.meshes.new(name=mesh_data_name)
            mesh["usd_path"] = mesh_definition_path # Store definition path for good measure

            # Process vertices, faces, and UVs (only if creating for the first time)
            flat_verts = mesh_data['vertices']
            vertices = [tuple(flat_verts[i:i+3]) for i in range(0, len(flat_verts), 3)]

            face_indices = mesh_data['face_vertex_indices']
            face_counts = mesh_data['face_vertex_counts']
            
            faces = []
            current_index = 0
            for count in face_counts:
                faces.append(tuple(face_indices[current_index : current_index + count]))
                current_index += count
            
            mesh.from_pydata(vertices, [], faces)
            mesh.update()

            if 'uvs' in mesh_data:
                self.apply_uvs(mesh, mesh_data)

        # Create a new object for this instance, and link it to the (now correctly shared) mesh data
        obj = bpy.data.objects.new(instance_name, mesh)
        obj["usd_path"] = instance_path # Store unique instance path on object

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
                # Only add the material to the mesh's slots if it's not already there.
                # This prevents duplicate slots on shared mesh data.
                if material.name not in mesh.materials:
                    mesh.materials.append(material)

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
        textures = material_data.get('textures', {})
        node_pos_y = 300
        for tex_type, tex_path in textures.items():
            final_tex_path = tex_path
            if tex_path.lower().endswith(".dds"):
                # Resolve the original DDS path to find the correct cached PNG path
                full_dds_path = self.resolve_texture_path(tex_path, self.filepath)
                final_tex_path = self.get_cached_png_path(full_dds_path)
                if not os.path.exists(final_tex_path):
                    print(f"  > WARNING: Expected cached texture not found: {final_tex_path}")
                    continue
            
            print(f"  > Loading texture '{tex_type}': {final_tex_path}")
            
            tex_node = nodes.new(type='ShaderNodeTexImage')
            tex_node.location = (-300, node_pos_y)
            
            try:
                tex_node.image = bpy.data.images.load(final_tex_path, check_existing=True)
            except Exception as e:
                print(f"    ! Could not load image: {e}")
                continue

            # Connect to BSDF based on type
            if tex_type == 'diffuse_texture' or tex_type == 'diffuseColor':
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
