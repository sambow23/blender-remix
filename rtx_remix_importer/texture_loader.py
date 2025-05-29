import bpy
import os
import tempfile
import subprocess
from typing import Optional, Dict, Set, Tuple
from .core_utils import get_texture_processor

# Global cache to prevent duplicate loading
_loaded_textures: Dict[str, bpy.types.Image] = {}
_loading_in_progress: Set[str] = set()

def clear_texture_cache():
    """Clear the global texture cache."""
    global _loaded_textures, _loading_in_progress
    _loaded_textures.clear()
    _loading_in_progress.clear()

def load_texture_smart(
    texture_path: str, 
    is_normal: bool = False, 
    is_non_color: bool = False,
    force_reload: bool = False
) -> Optional[bpy.types.Image]:
    """
    Handles DDS files.
    
    Args:
        texture_path: Path to the texture file
        is_normal: Whether this is a normal map
        is_non_color: Whether this is non-color data
        force_reload: Force reload even if already cached
    
    Returns:
        Loaded Blender image or None if failed
    """
    if not texture_path or not os.path.exists(texture_path):
        print(f"Texture not found: {texture_path}")
        return None
    
    # Normalize path for consistent caching
    abs_path = os.path.abspath(texture_path)
    cache_key = f"{abs_path}_{is_normal}_{is_non_color}"
    
    # Check cache first
    if not force_reload and cache_key in _loaded_textures:
        cached_image = _loaded_textures[cache_key]
        if cached_image and cached_image.name in bpy.data.images:
            print(f"Using cached texture: {os.path.basename(texture_path)}")
            return cached_image
        else:
            # Remove invalid cache entry
            del _loaded_textures[cache_key]
    
    # Prevent duplicate loading attempts
    if cache_key in _loading_in_progress:
        print(f"Texture loading already in progress: {os.path.basename(texture_path)}")
        return None
    
    _loading_in_progress.add(cache_key)
    
    try:
        # Determine file type and load accordingly
        file_ext = os.path.splitext(texture_path)[1].lower()
        
        if file_ext == '.dds':
            image = _load_dds_texture(abs_path, is_normal, is_non_color)
        else:
            image = _load_standard_texture(abs_path, is_normal, is_non_color)
        
        if image:
            _loaded_textures[cache_key] = image
            print(f"Successfully loaded texture: {os.path.basename(texture_path)}")
        else:
            print(f"Failed to load texture: {os.path.basename(texture_path)}")
        
        return image
        
    finally:
        _loading_in_progress.discard(cache_key)

def _load_standard_texture(
    texture_path: str, 
    is_normal: bool, 
    is_non_color: bool
) -> Optional[bpy.types.Image]:
    """Load standard texture formats (PNG, JPG, etc.)."""
    try:
        # Generate unique name to avoid conflicts
        base_name = os.path.splitext(os.path.basename(texture_path))[0]
        unique_name = _generate_unique_image_name(base_name)
        
        # Load the image
        image = bpy.data.images.load(texture_path)
        image.name = unique_name
        
        # Set color space
        if is_non_color or is_normal:
            image.colorspace_settings.name = 'Non-Color'
        else:
            image.colorspace_settings.name = 'sRGB'
        
        return image
        
    except Exception as e:
        print(f"Error loading standard texture {texture_path}: {e}")
        return None

def _load_dds_texture(
    texture_path: str, 
    is_normal: bool, 
    is_non_color: bool
) -> Optional[bpy.types.Image]:
    """Load DDS texture with conversion fallback."""
    # First try direct loading (in case Blender supports it)
    try:
        base_name = os.path.splitext(os.path.basename(texture_path))[0]
        unique_name = _generate_unique_image_name(base_name)
        
        image = bpy.data.images.load(texture_path)
        image.name = unique_name
        
        # Set color space
        if is_non_color or is_normal:
            image.colorspace_settings.name = 'Non-Color'
        else:
            image.colorspace_settings.name = 'sRGB'
        
        print(f"Direct DDS loading successful: {os.path.basename(texture_path)}")
        return image
        
    except Exception as direct_error:
        print(f"Direct DDS loading failed for {os.path.basename(texture_path)}: {direct_error}")
        
        # Try conversion fallback
        return _convert_dds_to_png(texture_path, is_normal, is_non_color)

def _convert_dds_to_png(
    dds_path: str, 
    is_normal: bool, 
    is_non_color: bool
) -> Optional[bpy.types.Image]:
    """Convert DDS to PNG using texconv and load the result."""
    texture_processor = get_texture_processor()
    
    if not texture_processor.is_available():
        print(f"texconv not available, cannot convert DDS: {os.path.basename(dds_path)}")
        return _create_placeholder_texture(dds_path, is_normal, is_non_color)
    
    try:
        # Create temporary PNG file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_png_path = temp_file.name
        
        try:
            # Convert DDS to PNG using texconv
            cmd = [
                texture_processor.texconv_path,
                dds_path,
                "-o", os.path.dirname(temp_png_path),
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
            
            if result.returncode == 0:
                # texconv creates file with original name, rename it
                original_name = os.path.splitext(os.path.basename(dds_path))[0]
                texconv_output = os.path.join(os.path.dirname(temp_png_path), f"{original_name}.png")
                
                if os.path.exists(texconv_output):
                    os.replace(texconv_output, temp_png_path)
                
                if os.path.exists(temp_png_path):
                    # Load the converted PNG
                    image = _load_standard_texture(temp_png_path, is_normal, is_non_color)
                    if image:
                        # Rename to match original DDS
                        base_name = os.path.splitext(os.path.basename(dds_path))[0]
                        image.name = _generate_unique_image_name(f"{base_name}_converted")
                        print(f"Successfully converted DDS to PNG: {os.path.basename(dds_path)}")
                        return image
            
            print(f"texconv conversion failed for {os.path.basename(dds_path)}: {result.stderr}")
            
        finally:
            # Cleanup temporary file
            if os.path.exists(temp_png_path):
                try:
                    os.remove(temp_png_path)
                except:
                    pass
                    
    except Exception as e:
        print(f"Error converting DDS {os.path.basename(dds_path)}: {e}")
    
    # Fallback to placeholder
    return _create_placeholder_texture(dds_path, is_normal, is_non_color)

def _create_placeholder_texture(
    original_path: str, 
    is_normal: bool, 
    is_non_color: bool
) -> bpy.types.Image:
    """Create a placeholder texture when loading fails."""
    base_name = os.path.splitext(os.path.basename(original_path))[0]
    unique_name = _generate_unique_image_name(f"{base_name}_placeholder")
    
    # Create a small colored image as placeholder
    if is_normal:
        # Normal map placeholder (neutral normal)
        color = (0.5, 0.5, 1.0, 1.0)  # Neutral normal in tangent space
    elif is_non_color:
        # Non-color data placeholder (mid-gray)
        color = (0.5, 0.5, 0.5, 1.0)
    else:
        # Diffuse placeholder (magenta to indicate missing texture)
        color = (1.0, 0.0, 1.0, 1.0)
    
    # Create 32x32 placeholder image
    image = bpy.data.images.new(unique_name, width=32, height=32)
    
    # Fill with solid color
    pixels = [color[i % 4] for i in range(32 * 32 * 4)]
    image.pixels = pixels
    
    # Set color space
    if is_non_color or is_normal:
        image.colorspace_settings.name = 'Non-Color'
    else:
        image.colorspace_settings.name = 'sRGB'
    
    print(f"Created placeholder texture for: {os.path.basename(original_path)}")
    return image

def _generate_unique_image_name(base_name: str) -> str:
    """Generate a unique image name to avoid conflicts."""
    # Remove invalid characters
    safe_name = "".join(c for c in base_name if c.isalnum() or c in "._-")
    
    if safe_name not in bpy.data.images:
        return safe_name
    
    # Find unique name with suffix
    counter = 1
    while f"{safe_name}.{counter:03d}" in bpy.data.images:
        counter += 1
    
    return f"{safe_name}.{counter:03d}"

def cleanup_duplicate_textures():
    """
    Clean up duplicate textures based on community solutions.
    References: https://blenderartists.org/t/cleanup-duplicate-images-textures/1395542
    """
    print("Cleaning up duplicate textures...")
    
    removed_count = 0
    base_images = {}
    
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
    
    print(f"Cleanup complete. Removed {removed_count} duplicate textures.")
    return removed_count

def get_texture_info() -> Dict[str, int]:
    """Get information about loaded textures."""
    info = {
        'total_images': len(bpy.data.images),
        'cached_textures': len(_loaded_textures),
        'loading_in_progress': len(_loading_in_progress),
        'dds_files': 0,
        'duplicates': 0
    }
    
    # Count DDS files and duplicates
    base_names = set()
    for image in bpy.data.images:
        if image.filepath.lower().endswith('.dds'):
            info['dds_files'] += 1
        
        # Check for duplicates
        name_parts = image.name.rsplit('.', 1)
        if len(name_parts) == 2 and name_parts[1].isdigit():
            info['duplicates'] += 1
        else:
            base_names.add(image.name)
    
    return info 