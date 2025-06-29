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

class RemixOperatorBase:
    """Base class for Remix operators to share all helper methods."""

    def batch_convert_textures_native(self, meshes_data, usd_file_path):
        """Finds all unique DDS files and calls the native module to convert them."""
        print("--- Collecting textures for conversion ---")
        dds_paths_to_convert = set()
        
        for mesh_data in meshes_data:
            if 'material' in mesh_data:
                textures = mesh_data['material'].get('textures', {})
                for tex_path in textures.values():
                    if tex_path.lower().endswith(".dds"):
                        full_path = self.resolve_texture_path(tex_path, usd_file_path)
                        if full_path and os.path.exists(full_path):
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

    def create_blender_mesh(self, mesh_data, usd_file_path):
        mesh_definition_path = mesh_data['mesh_definition_path']
        instance_path = mesh_data['instance_path']
        instance_name = mesh_data['instance_name']
        base_name = instance_name
        if '_' in instance_name:
            parts = instance_name.rsplit('_', 1)
            if parts[1].isdigit():
                base_name = parts[0]
        mesh_data_name = base_name.replace("inst_", "mesh_")
        mesh = bpy.data.meshes.get(mesh_data_name)
        if mesh:
            print(f"Reusing existing mesh data: {mesh.name}")
        else:
            print(f"Creating new mesh data: {mesh_data_name}")
            mesh = bpy.data.meshes.new(name=mesh_data_name)
            mesh["usd_path"] = mesh_definition_path
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
        obj = bpy.data.objects.new(instance_name, mesh)
        obj["usd_path"] = instance_path
        if 'transform' in mesh_data:
            flat_matrix = mesh_data['transform']
            matrix = mathutils.Matrix([flat_matrix[0:4], flat_matrix[4:8], flat_matrix[8:12], flat_matrix[12:16]])
            matrix.transpose()
            obj.matrix_world = matrix
        bpy.context.collection.objects.link(obj)
        if 'material' in mesh_data:
            mat_data = mesh_data['material']
            mat_path = mat_data['material_path']
            mat_name = mat_data['name']
            material = None
            if mat_name in bpy.data.materials:
                material = bpy.data.materials[mat_name]
                if material.get("usd_path") != mat_path:
                    material = None
            if not material:
                for mat in bpy.data.materials:
                    if mat.get("usd_path") == mat_path:
                        material = mat
                        break
            if not material:
                material = self.create_blender_material(mat_data, usd_file_path)
            if material:
                if material.name not in mesh.materials:
                    mesh.materials.append(material)

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

    def create_blender_material(self, material_data, usd_file_path):
        """Creates a new Blender material."""
        name = material_data['name']
        mat_path = material_data['material_path']
        
        material = bpy.data.materials.new(name=name)
        material["usd_path"] = mat_path
        material.use_nodes = True
        
        self.build_material_nodes(material, material_data, usd_file_path)
        return material

    def build_material_nodes(self, material, material_data, usd_file_path):
        """Builds the node tree for a given material."""
        # This function contains the node creation logic extracted from create_blender_material
        nodes = material.node_tree.nodes
        links = material.node_tree.links
        
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        output = nodes.new(type='ShaderNodeOutputMaterial')
        bsdf.location = (0, 0)
        output.location = (300, 0)
        links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])

        textures = material_data.get('textures', {})
        node_pos_y = 300
        for tex_type, tex_path in textures.items():
            final_tex_path = tex_path
            if tex_path.lower().endswith(".dds"):
                # Use the correct file path context for resolving textures
                full_dds_path = self.resolve_texture_path(tex_path, usd_file_path)
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
            
    def find_blender_object(self, usd_path):
        """Finds a Blender object by its 'usd_path' custom property."""
        for obj in bpy.context.scene.objects:
            if obj.get("usd_path") == usd_path:
                return obj
        return None

    def replace_mesh(self, usd_path, mesh_data):
        """Replaces the geometry of an existing Blender mesh."""
        obj_to_replace = None
        # Find the correct object INSTANCE to get the mesh data from
        for obj in bpy.context.scene.objects:
            if obj.data.get("usd_path") == usd_path:
                obj_to_replace = obj
                break
        
        if not obj_to_replace:
            print(f"  > Warning: Could not find object with mesh path {usd_path} to replace.")
            return

        print(f"  > Replacing mesh data for: {obj_to_replace.data.name}")
        mesh = obj_to_replace.data
        
        # Clear existing geometry
        mesh.clear_geometry()
        
        # Populate with new data (re-using logic from create_blender_mesh)
        vertices = [tuple(mesh_data['vertices'][i:i+3]) for i in range(0, len(mesh_data['vertices']), 3)]
        
        faces = []
        current_index = 0
        for count in mesh_data['face_vertex_counts']:
            faces.append(tuple(mesh_data['face_vertex_indices'][current_index : current_index + count]))
            current_index += count
        
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        
        if 'uvs' in mesh_data:
            # Remove old UV layers before adding new one
            while mesh.uv_layers:
                mesh.uv_layers.remove(mesh.uv_layers[0])
            self.apply_uvs(mesh, mesh_data)

    def replace_material(self, usd_path, material_data, mod_file_path):
        """Replaces the node tree of an existing Blender material."""
        mat_to_replace = bpy.data.materials.get(material_data['name'])
        if not mat_to_replace:
            # Fallback to searching by custom property
            for mat in bpy.data.materials:
                if mat.get("usd_path") == usd_path:
                    mat_to_replace = mat
                    break
        
        if not mat_to_replace:
            print(f"  > Warning: Could not find material with path {usd_path} to replace.")
            return

        print(f"  > Replacing material nodes for: {mat_to_replace.name}")
        
        # Clear existing nodes
        for node in mat_to_replace.node_tree.nodes:
            mat_to_replace.node_tree.nodes.remove(node)
            
        # Rebuild node tree using the same logic as creation
        self.build_material_nodes(mat_to_replace, material_data, mod_file_path)

class REMIX_OT_apply_mod(bpy.types.Operator, ImportHelper, RemixOperatorBase):
    """Applies a mod.usda file to the currently loaded capture"""
    bl_idname = "remix_native.apply_mod"
    bl_label = "Apply Mod/Sublayer"

    filter_glob: StringProperty(
        default="*.usd;*.usda;*.usdc",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        if not NATIVE_MODULE_LOADED:
            self.report({'ERROR'}, "Native module is not loaded.")
            return {'CANCELLED'}
        
        base_capture_path = context.scene.get("rtx_remix_base_capture")
        if not base_capture_path:
            self.report({'ERROR'}, "No base capture file loaded. Please import a capture first.")
            return {'CANCELLED'}

        print(f"Applying mod '{self.filepath}' to base '{base_capture_path}'")
        
        try:
            replacements = remix_native.apply_mod(base_capture_path, self.filepath)
            print(f"C++ module returned {len(replacements)} replacements.")
            
            # --- Process the replacements ---
            for usd_path, data in replacements.items():
                if "vertices" in data: # This is a mesh replacement
                    self.replace_mesh(usd_path, data)
                elif "textures" in data: # This is a material replacement
                    self.replace_material(usd_path, data, self.filepath)

        except Exception as e:
            self.report({'ERROR'}, f"C++ module failed: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

class REMIX_OT_import_usd(bpy.types.Operator, ImportHelper, RemixOperatorBase):
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

        self.batch_convert_textures_native(meshes_data, self.filepath)

        print("--- Creating Blender scene ---")
        for mesh_data in meshes_data:
            self.create_blender_mesh(mesh_data, self.filepath)
        
        context.scene["rtx_remix_base_capture"] = self.filepath
        
        print("--- Native USD Import Finished ---")
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
            col.operator(REMIX_OT_apply_mod.bl_idname, icon='FILE_REFRESH')
        else:
            col.label(text="Native module failed to load.", icon='ERROR')


# --- Registration ---
classes = (
    REMIX_OT_import_usd,
    REMIX_OT_apply_mod,
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
 