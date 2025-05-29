import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, EnumProperty

class REMIX_OT_CleanupDuplicateTextures(Operator):
    """Clean up duplicate textures (e.g., texture.001, texture.002) and remap to base textures"""
    bl_idname = "remix.cleanup_duplicate_textures"
    bl_label = "Cleanup Duplicate Textures"
    bl_description = "Remove duplicate textures and remap materials to use base textures"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        try:
            from ..texture_loader import cleanup_duplicate_textures
            removed_count = cleanup_duplicate_textures()
            self.report({'INFO'}, f"Cleaned up {removed_count} duplicate textures")
            return {'FINISHED'}
        except ImportError:
            # Fallback implementation
            removed_count = self._cleanup_fallback()
            self.report({'INFO'}, f"Cleaned up {removed_count} duplicate textures (fallback)")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error cleaning up textures: {e}")
            return {'CANCELLED'}
    
    def _cleanup_fallback(self):
        """Fallback cleanup implementation based on community solutions."""
        removed_count = 0
        
        # Group images by base name
        for image in list(bpy.data.images):
            if not image.name:
                continue
                
            # Check if this is a numbered duplicate (e.g., "texture.001", "texture.002")
            name_parts = image.name.rsplit('.', 1)
            if len(name_parts) == 2 and name_parts[1].isdigit():
                base_name = name_parts[0]
                
                # Find the base image (without number)
                base_image = None
                for img in bpy.data.images:
                    if img.name == base_name:
                        base_image = img
                        break
                
                if base_image:
                    # Remap users to base image
                    try:
                        image.user_remap(base_image)
                        bpy.data.images.remove(image)
                        removed_count += 1
                        print(f"Removed duplicate: {image.name} -> {base_name}")
                    except Exception as e:
                        print(f"Failed to remove duplicate {image.name}: {e}")
        
        return removed_count

class REMIX_OT_ClearTextureCache(Operator):
    """Clear the texture loading cache"""
    bl_idname = "remix.clear_texture_cache"
    bl_label = "Clear Texture Cache"
    bl_description = "Clear the internal texture loading cache"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        try:
            from ..texture_loader import clear_texture_cache
            clear_texture_cache()
            self.report({'INFO'}, "Texture cache cleared")
            return {'FINISHED'}
        except ImportError:
            self.report({'WARNING'}, "Texture cache not available")
            return {'CANCELLED'}

class REMIX_OT_ShowTextureInfo(Operator):
    """Show information about loaded textures"""
    bl_idname = "remix.show_texture_info"
    bl_label = "Show Texture Info"
    bl_description = "Display information about loaded textures"
    bl_options = {'REGISTER'}
    
    def execute(self, context):
        try:
            from ..texture_loader import get_texture_info
            info = get_texture_info()
            
            message = (
                f"Texture Information:\n"
                f"Total Images: {info['total_images']}\n"
                f"Cached Textures: {info['cached_textures']}\n"
                f"Loading in Progress: {info['loading_in_progress']}\n"
                f"DDS Files: {info['dds_files']}\n"
                f"Duplicates: {info['duplicates']}"
            )
            
            self.report({'INFO'}, message)
            print(message)
            return {'FINISHED'}
        except ImportError:
            # Fallback info
            total_images = len(bpy.data.images)
            duplicates = 0
            dds_files = 0
            
            for image in bpy.data.images:
                if image.filepath.lower().endswith('.dds'):
                    dds_files += 1
                
                name_parts = image.name.rsplit('.', 1)
                if len(name_parts) == 2 and name_parts[1].isdigit():
                    duplicates += 1
            
            message = (
                f"Texture Information (basic):\n"
                f"Total Images: {total_images}\n"
                f"DDS Files: {dds_files}\n"
                f"Duplicates: {duplicates}"
            )
            
            self.report({'INFO'}, message)
            print(message)
            return {'FINISHED'}

class REMIX_OT_ConvertDDSTextures(Operator):
    """Convert DDS textures to PNG for better Blender compatibility"""
    bl_idname = "remix.convert_dds_textures"
    bl_label = "Convert DDS Textures"
    bl_description = "Convert DDS textures to PNG format for better Blender compatibility"
    bl_options = {'REGISTER'}
    
    selected_only: BoolProperty(
        name="Selected Objects Only",
        description="Only convert textures used by selected objects",
        default=False
    )
    
    def execute(self, context):
        try:
            from ..core_utils import get_texture_processor
            texture_processor = get_texture_processor()
            
            if not texture_processor.is_available():
                self.report({'ERROR'}, "texconv.exe not found. Cannot convert DDS textures.")
                return {'CANCELLED'}
            
            # Find DDS textures to convert
            dds_images = []
            if self.selected_only:
                # Get textures from selected objects
                for obj in context.selected_objects:
                    if obj.type == 'MESH' and obj.data.materials:
                        for material in obj.data.materials:
                            if material and material.use_nodes:
                                for node in material.node_tree.nodes:
                                    if node.type == 'TEX_IMAGE' and node.image:
                                        if node.image.filepath.lower().endswith('.dds'):
                                            dds_images.append(node.image)
            else:
                # Get all DDS images
                for image in bpy.data.images:
                    if image.filepath.lower().endswith('.dds'):
                        dds_images.append(image)
            
            if not dds_images:
                self.report({'INFO'}, "No DDS textures found to convert")
                return {'FINISHED'}
            
            converted_count = 0
            for image in dds_images:
                # This would need async implementation for real use
                print(f"Would convert: {image.name}")
                converted_count += 1
            
            self.report({'INFO'}, f"Found {converted_count} DDS textures to convert (conversion not implemented yet)")
            return {'FINISHED'}
            
        except ImportError:
            self.report({'ERROR'}, "Texture conversion tools not available")
            return {'CANCELLED'}

# Panel for texture management
class REMIX_PT_TextureManagement(bpy.types.Panel):
    """Panel for texture management tools"""
    bl_label = "Texture Management"
    bl_idname = "REMIX_PT_texture_management"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_parent_id = "REMIX_PT_asset_processing"  # Make it a sub-panel
    
    def draw(self, context):
        layout = self.layout
        
        # Cleanup section
        box = layout.box()
        box.label(text="Cleanup Tools", icon='BRUSH_DATA')
        
        col = box.column(align=True)
        col.operator("remix.cleanup_duplicate_textures", icon='DUPLICATE')
        col.operator("remix.clear_texture_cache", icon='FILE_REFRESH')
        
        # Info section
        box = layout.box()
        box.label(text="Information", icon='INFO')
        box.operator("remix.show_texture_info", icon='VIEWZOOM')
        
        # Conversion section
        box = layout.box()
        box.label(text="Conversion", icon='FILE_IMAGE')
        
        col = box.column(align=True)
        op = col.operator("remix.convert_dds_textures", text="Convert All DDS", icon='EXPORT')
        op.selected_only = False
        
        op = col.operator("remix.convert_dds_textures", text="Convert Selected DDS", icon='EXPORT')
        op.selected_only = True

# Registration
classes = [
    REMIX_OT_CleanupDuplicateTextures,
    REMIX_OT_ClearTextureCache,
    REMIX_OT_ShowTextureInfo,
    REMIX_OT_ConvertDDSTextures,
    REMIX_PT_TextureManagement,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls) 