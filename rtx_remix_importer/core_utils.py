import bpy
import os
import math
import hashlib
import json
import tempfile
import subprocess
import asyncio
import threading
from typing import Optional, Tuple, Dict, Any, List, Callable
from concurrent.futures import ThreadPoolExecutor

try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf, Vt
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

from . import constants

# Global thread pool for async operations
_thread_pool = None

def get_thread_pool():
    """Get or create the global thread pool for async operations."""
    global _thread_pool
    if _thread_pool is None:
        _thread_pool = ThreadPoolExecutor(max_workers=4)
    return _thread_pool

def cleanup_thread_pool():
    """Cleanup the global thread pool."""
    global _thread_pool
    if _thread_pool is not None:
        _thread_pool.shutdown(wait=True)
        _thread_pool = None

# --- Path Utilities ---

def get_relative_path(from_path: str, to_path: str) -> str:
    """Calculates the relative path from one file to another with proper cross-platform handling."""
    try:
        from_dir = os.path.dirname(from_path)
        abs_from_path = os.path.abspath(from_dir)
        abs_to_path = os.path.abspath(to_path)
        rel_path = os.path.relpath(abs_to_path, start=abs_from_path)
        
        # Normalize path separators for USD (always forward slashes)
        rel_path = rel_path.replace('\\', '/')
        
        # Ensure relative paths start with './' for clarity
        if not rel_path.startswith("../") and not rel_path.startswith("./") and rel_path != ".":
            rel_path = "./" + rel_path
            
        return rel_path
    except Exception as e:
        print(f"Error calculating relative path from '{from_path}' to '{to_path}': {e}")
        return to_path

def sanitize_prim_name(name: str) -> str:
    """Sanitizes a name for use as a USD prim name."""
    invalid_chars = " .!@#$%^&*()-+=[]{};:'\"<>,/?`~"
    sanitized = name
    for char in invalid_chars:
        sanitized = sanitized.replace(char, '_')
    
    # Ensure it starts with a letter or underscore
    if not sanitized or not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = '_' + sanitized
    return sanitized

def generate_uuid_name(name: str, prefix: str = "ref_") -> str:
    """Generates a UUID-style name based on the input name for RTX Remix compatibility."""
    import uuid
    uuid_seed = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    return f"{prefix}{uuid_seed.hex[:32]}"

# --- Material Utilities ---

class MaterialPathResolver:
    """Handles material path resolution and caching."""
    
    def __init__(self, usd_file_path_context: str):
        self.usd_file_path_context = usd_file_path_context
        self._texture_dir_cache = {}
    
    def find_texture_dir(self) -> Optional[str]:
        """Find texture directory with caching."""
        if self.usd_file_path_context in self._texture_dir_cache:
            return self._texture_dir_cache[self.usd_file_path_context]
        
        usd_dir = os.path.dirname(self.usd_file_path_context)
        mod_dir = os.path.dirname(usd_dir)
        mod_root_dir = os.path.dirname(mod_dir)

        potential_dirs = [
            os.path.join(mod_dir, "captures", "textures"),
            os.path.join(mod_dir, "assets", "textures"),
            os.path.join(mod_dir, "assets"),
            os.path.join(mod_root_dir, "assets", "textures"),
            os.path.join(mod_root_dir, "assets"),
            os.path.join(mod_root_dir, "textures"),
        ]

        for p_dir in potential_dirs:
            if os.path.isdir(p_dir):
                self._texture_dir_cache[self.usd_file_path_context] = p_dir
                return p_dir
        
        self._texture_dir_cache[self.usd_file_path_context] = None
        return None

def create_material_cache_key(base_material_path: str, instance_metadata: Dict[str, Any]) -> str:
    """Create a unique cache key for materials with metadata."""
    metadata_hash = ""
    if instance_metadata:
        sorted_meta_string = json.dumps(instance_metadata, sort_keys=True)
        metadata_hash = hashlib.md5(sorted_meta_string.encode('utf-8')).hexdigest()[:8]
    
    return f"{base_material_path}_{metadata_hash}" if metadata_hash else base_material_path

# --- USD Stage Utilities ---

class USDStageManager:
    """Manages USD stage operations with proper resource cleanup."""
    
    def __init__(self):
        self._open_stages = {}
    
    def get_or_open_stage(self, file_path: str) -> Optional[Usd.Stage]:
        """Get or open a USD stage with caching."""
        if not USD_AVAILABLE:
            return None
        
        abs_path = os.path.abspath(file_path)
        if abs_path in self._open_stages:
            return self._open_stages[abs_path]
        
        try:
            stage = Usd.Stage.Open(abs_path, Usd.Stage.LoadAll)
            if stage:
                self._open_stages[abs_path] = stage
                return stage
        except Exception as e:
            print(f"Error opening USD stage '{abs_path}': {e}")
        
        return None
    
    def close_stage(self, file_path: str):
        """Close and remove a stage from cache."""
        abs_path = os.path.abspath(file_path)
        if abs_path in self._open_stages:
            # USD stages don't have explicit close, just remove from cache
            del self._open_stages[abs_path]
    
    def cleanup_all(self):
        """Cleanup all cached stages."""
        self._open_stages.clear()

# Global stage manager instance
_stage_manager = USDStageManager()

def get_stage_manager() -> USDStageManager:
    """Get the global stage manager."""
    return _stage_manager

# --- Async Texture Processing ---

class TextureProcessor:
    """Handles texture processing operations asynchronously."""
    
    def __init__(self, texconv_path: Optional[str] = None):
        self.texconv_path = texconv_path or self._find_texconv()
        self._format_map = {
            'BC1_UNORM': 'BC1_UNORM',
            'BC1_UNORM_SRGB': 'BC1_UNORM_SRGB',
            'BC3_UNORM': 'BC3_UNORM',
            'BC3_UNORM_SRGB': 'BC3_UNORM_SRGB',
            'BC4_UNORM': 'BC4_UNORM',
            'BC4_SNORM': 'BC4_SNORM',
            'BC5_UNORM': 'BC5_UNORM',
            'BC5_SNORM': 'BC5_SNORM',
            'BC7_UNORM': 'BC7_UNORM',
            'BC7_UNORM_SRGB': 'BC7_UNORM_SRGB',
        }
        
        # RTX Remix texture type suffixes
        self.texture_type_suffixes = {
            'base color': ".a.rtex",
            'albedo': ".a.rtex",
            'normal': ".n.rtex", 
            'roughness': ".r.rtex",
            'metallic': ".m.rtex",
            'emission': ".e.rtex",
            'emissive': ".e.rtex",
            'opacity': ".o.rtex"
        }
        
        # DDS format recommendations by texture type
        self.format_recommendations = {
            'base color': 'BC7_UNORM_SRGB',
            'albedo': 'BC7_UNORM_SRGB',
            'normal': 'BC5_UNORM',
            'roughness': 'BC4_UNORM',
            'metallic': 'BC4_UNORM',
            'emission': 'BC7_UNORM_SRGB',
            'emissive': 'BC7_UNORM_SRGB',
            'opacity': 'BC4_UNORM'
        }
    
    def _find_texconv(self) -> Optional[str]:
        """Find texconv.exe in the addon directory."""
        addon_dir = os.path.dirname(__file__)
        potential_path = os.path.join(addon_dir, "texconv", "texconv.exe")
        return potential_path if os.path.exists(potential_path) else None
    
    def is_available(self) -> bool:
        """Check if texture processing is available."""
        return self.texconv_path is not None
    
    def get_recommended_format(self, texture_type: str) -> str:
        """Get recommended DDS format for texture type."""
        return self.format_recommendations.get(texture_type.lower(), 'BC7_UNORM_SRGB')
    
    def get_texture_suffix(self, texture_type: str) -> str:
        """Get RTX Remix suffix for texture type."""
        return self.texture_type_suffixes.get(texture_type.lower(), "")
    
    async def convert_png_to_dds_async(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert a Blender image to DDS format asynchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        # Use recommended format if not specified
        if dds_format is None:
            dds_format = self.get_recommended_format(texture_type)
        
        def _convert():
            return self._convert_png_to_dds_sync(bl_image, output_path, dds_format, progress_callback)
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(get_thread_pool(), _convert)
    
    def convert_png_to_dds_sync(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        texture_type: str = 'base color',
        dds_format: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert a Blender image to DDS format synchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        # Use recommended format if not specified
        if dds_format is None:
            dds_format = self.get_recommended_format(texture_type)
        
        return self._convert_png_to_dds_sync(bl_image, output_path, dds_format, progress_callback)
    
    def _convert_png_to_dds_sync(
        self, 
        bl_image: bpy.types.Image, 
        output_path: str, 
        dds_format: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Internal PNG to DDS conversion implementation."""
        try:
            if progress_callback:
                progress_callback(f"Converting {bl_image.name} to DDS...")
            
            # Create output directory
            output_dir = os.path.dirname(output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            # Create temporary PNG file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                temp_input_path = temp_file.name
            
            try:
                # Save Blender image to temporary file
                self._save_blender_image_to_file(bl_image, temp_input_path)
                
                if progress_callback:
                    progress_callback("Running texconv...")
                
                # Convert using texconv
                texconv_format = self._format_map.get(dds_format, 'BC7_UNORM_SRGB')
                cmd = [
                    self.texconv_path,
                    temp_input_path,
                    "-o", output_dir,
                    "-ft", "dds",
                    "-f", texconv_format,
                    "-m", "0",  # Generate all mipmaps
                    "-y",  # Overwrite existing
                    "-nologo",
                ]
                
                result = subprocess.run(
                    cmd, 
                    capture_output=True, 
                    text=True, 
                    check=False, 
                    shell=False,
                    timeout=60  # 60 second timeout
                )
                
                if result.returncode != 0:
                    if progress_callback:
                        progress_callback(f"texconv failed: {result.stderr}")
                    return False
                
                # Handle texconv output file naming
                temp_output_base = os.path.splitext(os.path.basename(temp_input_path))[0]
                temp_dds_filename = f"{temp_output_base}.dds"
                temp_dds_path = os.path.join(output_dir, temp_dds_filename)
                
                if not os.path.exists(temp_dds_path):
                    if progress_callback:
                        progress_callback("texconv output file not found")
                    return False
                
                # Rename to final output path
                if temp_dds_path != output_path:
                    os.replace(temp_dds_path, output_path)
                
                if progress_callback:
                    progress_callback("Conversion complete")
                
                return True
                
            finally:
                # Cleanup temporary file
                if os.path.exists(temp_input_path):
                    try:
                        os.remove(temp_input_path)
                    except:
                        pass
                        
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False
    
    def convert_dds_to_png_sync(
        self,
        dds_path: str,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert DDS file to PNG format synchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        try:
            if progress_callback:
                progress_callback(f"Converting {os.path.basename(dds_path)} to PNG...")
            
            # Create output directory
            output_dir = os.path.dirname(output_path)
            os.makedirs(output_dir, exist_ok=True)
            
            # Convert DDS to PNG using texconv
            cmd = [
                self.texconv_path,
                dds_path,
                "-o", output_dir,
                "-ft", "png",
                "-y",  # Overwrite existing
                "-nologo",
            ]
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                check=False, 
                shell=False,
                timeout=30
            )
            
            if result.returncode != 0:
                if progress_callback:
                    progress_callback(f"texconv failed: {result.stderr}")
                return False
            
            # Handle texconv output file naming
            original_name = os.path.splitext(os.path.basename(dds_path))[0]
            texconv_output = os.path.join(output_dir, f"{original_name}.png")
            
            if os.path.exists(texconv_output):
                # Rename to final output path if different
                if texconv_output != output_path:
                    os.replace(texconv_output, output_path)
                
                if progress_callback:
                    progress_callback("Conversion complete")
                return True
            else:
                if progress_callback:
                    progress_callback("texconv output file not found")
                return False
                
        except Exception as e:
            if progress_callback:
                progress_callback(f"Error: {e}")
            return False
    
    async def convert_dds_to_png_async(
        self,
        dds_path: str,
        output_path: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Convert DDS file to PNG format asynchronously."""
        if not self.is_available():
            if progress_callback:
                progress_callback("texconv.exe not found")
            return False
        
        def _convert():
            return self.convert_dds_to_png_sync(dds_path, output_path, progress_callback)
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(get_thread_pool(), _convert)
    
    def batch_convert_dds_to_png(
        self,
        dds_files: List[str],
        output_dir: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> List[str]:
        """Batch convert multiple DDS files to PNG."""
        if not self.is_available():
            return []
        
        converted_files = []
        total_files = len(dds_files)
        
        for i, dds_file in enumerate(dds_files):
            if progress_callback:
                progress_callback(i, total_files, f"Converting {os.path.basename(dds_file)}")
            
            base_name = os.path.splitext(os.path.basename(dds_file))[0]
            output_path = os.path.join(output_dir, f"{base_name}.png")
            
            if self.convert_dds_to_png_sync(dds_file, output_path):
                converted_files.append(output_path)
        
        if progress_callback:
            progress_callback(total_files, total_files, "Batch conversion complete")
        
        return converted_files
    
    def _save_blender_image_to_file(self, bl_image: bpy.types.Image, filepath: str):
        """Save a Blender image to a file."""
        scene = bpy.context.scene
        render_settings = scene.render
        
        # Store original settings
        orig_filepath = render_settings.filepath
        orig_format = render_settings.image_settings.file_format
        orig_color_mode = render_settings.image_settings.color_mode
        orig_color_depth = render_settings.image_settings.color_depth
        
        try:
            # Set temporary render settings
            render_settings.filepath = filepath
            render_settings.image_settings.file_format = 'PNG'
            render_settings.image_settings.color_mode = 'RGBA'
            render_settings.image_settings.color_depth = '8'
            
            # Save the image
            bl_image.save_render(filepath=filepath, scene=scene)
            
        finally:
            # Restore original settings
            render_settings.filepath = orig_filepath
            render_settings.image_settings.file_format = orig_format
            render_settings.image_settings.color_mode = orig_color_mode
            render_settings.image_settings.color_depth = orig_color_depth

    # Legacy method names for backward compatibility
    async def convert_texture_async(self, bl_image, output_path, dds_format='BC7_UNORM_SRGB', progress_callback=None):
        """Legacy method - use convert_png_to_dds_async instead."""
        return await self.convert_png_to_dds_async(bl_image, output_path, 'base color', dds_format, progress_callback)

# Global texture processor instance
_texture_processor = None

def get_texture_processor() -> TextureProcessor:
    """Get the global texture processor."""
    global _texture_processor
    if _texture_processor is None:
        _texture_processor = TextureProcessor()
    return _texture_processor

# --- Progress Tracking ---

class ProgressTracker:
    """Tracks progress for long-running operations."""
    
    def __init__(self, total_steps: int, callback: Optional[Callable[[float, str], None]] = None):
        self.total_steps = total_steps
        self.current_step = 0
        self.callback = callback
        self._cancelled = False
    
    def update(self, message: str = ""):
        """Update progress."""
        if self._cancelled:
            return False
        
        progress = self.current_step / self.total_steps if self.total_steps > 0 else 0.0
        if self.callback:
            self.callback(progress, message)
        return True
    
    def step(self, message: str = ""):
        """Advance to next step."""
        self.current_step += 1
        return self.update(message)
    
    def cancel(self):
        """Cancel the operation."""
        self._cancelled = True
    
    def is_cancelled(self) -> bool:
        """Check if operation was cancelled."""
        return self._cancelled

# --- Cleanup Functions ---

def cleanup_addon_resources():
    """Cleanup all addon resources."""
    cleanup_thread_pool()
    get_stage_manager().cleanup_all() 